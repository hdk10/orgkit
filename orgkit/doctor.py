#!/usr/bin/env python3
"""orgkit.doctor — diagnose and repair broken orgkit states.

Detects common broken states and offers to fix them interactively (or
automatically with --yes).  Read-only when run with --dry-run.

Checks performed
----------------
1. Dangling hooks  — ~/.claude/settings.json entries pointing at .org/ paths
                     that no longer exist on disk.
2. Engine/hooks mismatch — engine installed but hooks missing (or vice versa).
3. Malformed roles.json — missing, empty, or unparseable.
4. Drift — folders on disk not in roles.json, or roles.json folders missing.
5. Orphan markers   — ROLE.md present but 0 bytes.
6. Stale promote    — ROLE.md is newer than .last_promote (content added since last reconcile).

Usage:
  python3 setup.py --doctor [--target PATH] [--yes] [--dry-run]
  python3 .org/doctor.py    [--target PATH] [--yes] [--dry-run]
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from core import (  # noqa: E402
    detect_repo_root,
    save_roles_cfg,
    default_roles_cfg,
    list_top_dirs,
    roles_file,
    role_md_path,
    role_memory_dir,
    read_marker_ts,
)

USER_SETTINGS = Path.home() / ".claude" / "settings.json"

# Status codes for the diagnosis table
_OK   = "OK"
_WARN = "WARN"
_FAIL = "FAIL"
_INFO = "INFO"

# ANSI colour (suppressed when not a TTY)
_IS_TTY = sys.stdout.isatty()

def _c(text: str, code: str) -> str:
    if not _IS_TTY:
        return text
    codes = {"green": "32", "yellow": "33", "red": "31", "cyan": "36", "bold": "1"}
    return f"\x1b[{codes.get(code, '0')}m{text}\x1b[0m"


def _status_str(status: str) -> str:
    if status == _OK:
        return _c("OK  ", "green")
    if status == _WARN:
        return _c("WARN", "yellow")
    if status == _FAIL:
        return _c("FAIL", "red")
    return _c("INFO", "cyan")


# ---------------------------------------------------------------------------
# Settings.json helpers
# ---------------------------------------------------------------------------

def _load_settings() -> dict[str, Any] | None:
    if not USER_SETTINGS.exists():
        return None
    try:
        return json.loads(USER_SETTINGS.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_settings(data: dict[str, Any]) -> None:
    backup = USER_SETTINGS.with_suffix(f".json.bak.{int(time.time())}")
    shutil.copy2(USER_SETTINGS, backup)
    USER_SETTINGS.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"    [doctor] settings.json updated (backup: {backup.name})")


def _iter_hook_commands(settings: dict[str, Any]):
    """Yield (event, list_index, hook_index, command_str) for every hook command."""
    hooks = settings.get("hooks", {})
    for event, group_list in hooks.items():
        if not isinstance(group_list, list):
            continue
        for gi, group in enumerate(group_list):
            for hi, hook in enumerate(group.get("hooks", [])):
                cmd = hook.get("command")
                if cmd:
                    yield event, gi, hi, str(cmd)


def _find_dangling_hooks(settings: dict[str, Any]) -> list[tuple[str, int, int, str]]:
    """Return list of (event, gi, hi, cmd) for hooks pointing at non-existent .org/ paths."""
    dangling = []
    for event, gi, hi, cmd in _iter_hook_commands(settings):
        # Extract all quoted or unquoted paths that look like /<something>/.org/
        matches = re.findall(r'"?(/[^"]+/\.org/[^"\s]+)"?', cmd)
        if not matches:
            # Also check for unquoted: python3 /some/path/.org/foo.py
            matches = re.findall(r'(\S+/\.org/\S+)', cmd)
        for m in matches:
            p = Path(m)
            if not p.exists():
                dangling.append((event, gi, hi, cmd))
                break
    return dangling


# Match a /.../.org/... path inside a hook command, with or without the
# surrounding quotes the installer wraps the path in (e.g. python3 "/a/.org/x.py").
_ORG_PATH_RE = re.compile(r'/[^"\'\s]+/\.org/[^"\'\s]+')


def _cmd_references_org(cmd: str, repo_org_resolved: str) -> bool:
    """True if a hook command points at ``repo_org_resolved`` (an already
    .resolve()'d <repo>/.org path), normalising /tmp vs /private/tmp symlinks.

    The stored command quotes the script path (``python3 "/abs/.org/x.py"``),
    so resolving a bare token would carry the quote chars and produce garbage.
    We extract the `/.org/` substring with a regex (quote-insensitive) and
    resolve its parent `.org` directory before comparing.
    """
    if not cmd:
        return False
    if repo_org_resolved in cmd:
        return True
    for m in _ORG_PATH_RE.findall(cmd):
        idx = m.find("/.org/")
        org_dir = m[: idx + len("/.org")]
        try:
            if str(Path(org_dir).resolve()) == repo_org_resolved:
                return True
        except Exception:
            continue
    return False


def _hooks_for_repo(settings: dict[str, Any], target_org: str) -> list[tuple[str, int, int, str]]:
    """Return hooks whose command references target_org path.

    Resolves symlinks on both sides so /tmp and /private/tmp compare equal on macOS.
    """
    target_org_resolved = str(Path(target_org).resolve())
    found = []
    for event, gi, hi, cmd in _iter_hook_commands(settings):
        if _cmd_references_org(cmd, target_org_resolved):
            found.append((event, gi, hi, cmd))
    return found


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

class Check:
    def __init__(self, name: str, status: str, detail: str, fix_desc: str = "", fixable: bool = False):
        self.name = name
        self.status = status
        self.detail = detail
        self.fix_desc = fix_desc
        self.fixable = fixable
        self._fix_fn = None  # set externally

    def set_fix(self, fn):
        self._fix_fn = fn
        return self

    def apply_fix(self) -> bool:
        if self._fix_fn:
            try:
                self._fix_fn()
                return True
            except Exception as exc:
                print(f"    [doctor] fix failed: {exc}")
                return False
        return False


def check_dangling_hooks(settings: dict | None, _repo_org: str) -> Check:
    if settings is None:
        return Check("Dangling hooks", _INFO, "~/.claude/settings.json not found — skipping hook checks")

    dangling = _find_dangling_hooks(settings)
    if not dangling:
        return Check("Dangling hooks", _OK, "No dangling hook entries found")

    detail = f"{len(dangling)} hook(s) point at paths that don't exist on disk"
    return Check(
        "Dangling hooks", _WARN, detail,
        fix_desc=f"Remove {len(dangling)} dangling hook entry(s) from settings.json",
        fixable=True,
    )


def _engine_present(repo_root: Path) -> bool:
    """Is the orgkit ENGINE installed in this repo?

    The engine is the .py modules under .org/ (core.py et al.), NOT roles.json.
    Determining presence from roles.json is wrong: a merely missing or
    malformed roles.json (a recoverable FAIL that check_roles_json repairs by
    writing a default) would otherwise look like "engine gone" and trigger the
    destructive hook-removal fix, deleting this repo's real working hooks. Key
    off the actual engine files so a bad roles.json never masquerades as a
    missing engine.
    """
    org = repo_root / ".org"
    if not org.is_dir():
        return False
    # Prefer the core sentinel; fall back to any engine .py module present.
    if (org / "core.py").is_file():
        return True
    return any(org.glob("*.py"))


def check_engine_hooks_mismatch(repo_root: Path, settings: dict | None) -> Check:
    engine_ok = _engine_present(repo_root)
    if settings is None:
        hooks_ok = False
    else:
        # Use resolved path so /tmp == /private/tmp on macOS
        repo_org = str((repo_root / ".org").resolve())
        hooks_ok = bool(_hooks_for_repo(settings, repo_org))

    if engine_ok and hooks_ok:
        return Check("Engine ↔ hooks", _OK, "Engine installed and hooks registered")
    if engine_ok and not hooks_ok:
        return Check(
            "Engine ↔ hooks", _WARN,
            "Engine (.org/) is present but NO hooks in settings.json reference this repo",
            fix_desc="Re-register hooks via install_hooks.py",
            fixable=True,
        )
    if not engine_ok and hooks_ok:
        return Check(
            "Engine ↔ hooks", _WARN,
            "Hooks in settings.json reference this repo but .org/ engine is missing",
            fix_desc="Remove stale hook entries for this repo",
            fixable=True,
        )
    # Neither
    return Check("Engine ↔ hooks", _INFO, "Engine not installed — run setup.py first")


def check_roles_json(repo_root: Path) -> tuple[Check, dict | None]:
    rf = roles_file(repo_root)
    if not rf.exists():
        return Check(
            "roles.json", _FAIL, "Missing: .org/roles.json not found",
            fix_desc="Create default roles.json",
            fixable=True,
        ), None

    try:
        cfg = json.loads(rf.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return Check(
            "roles.json", _FAIL, f"Malformed JSON: {exc}",
            fix_desc="Restore from roles.json.bak if present, else create default",
            fixable=True,
        ), None

    if not cfg:
        return Check(
            "roles.json", _FAIL, "Empty / zero-byte JSON object",
            fix_desc="Write default roles.json structure",
            fixable=True,
        ), None

    roles = cfg.get("roles", {})
    if not isinstance(roles, dict):
        return Check(
            "roles.json", _FAIL, '"roles" key is not a dict',
            fix_desc="Overwrite with default roles.json",
            fixable=True,
        ), None

    return Check("roles.json", _OK, f"Valid — {len(roles)} role(s) defined"), cfg


def check_drift(repo_root: Path, cfg: dict | None) -> list[Check]:
    checks: list[Check] = []
    if cfg is None:
        checks.append(Check("Folder drift", _INFO, "Skipped — roles.json unavailable"))
        return checks

    excl: set[str] = set(cfg.get("_meta", {}).get("exclude_dirs", []))
    roles: dict = cfg.get("roles", {})
    role_names: set[str] = set(roles.keys())
    on_disk: set[str] = set(list_top_dirs(repo_root, excl))
    mapped: set[str] = {f for r in roles.values() for f in r.get("folders", [])}

    # Folders on disk not in any role
    unlisted = sorted(on_disk - mapped - role_names)
    # Folders in roles.json not found anywhere on disk
    missing = []
    for role_name, role_info in roles.items():
        for f in role_info.get("folders", []):
            exists_top = (repo_root / f).is_dir()
            exists_under_role = (repo_root / role_name / f).is_dir()
            if not exists_top and not exists_under_role:
                missing.append(f"{role_name}/{f}")

    if unlisted:
        detail = f"{len(unlisted)} folder(s) on disk not in roles.json: {', '.join(unlisted[:5])}"
        if len(unlisted) > 5:
            detail += f" (+{len(unlisted) - 5} more)"
        checks.append(Check("Unlisted folders", _WARN, detail))
    else:
        checks.append(Check("Unlisted folders", _OK, "All top-level folders accounted for"))

    if missing:
        detail = f"{len(missing)} role folder(s) missing on disk: {', '.join(missing[:5])}"
        if len(missing) > 5:
            detail += f" (+{len(missing) - 5} more)"
        checks.append(Check("Missing folders", _WARN, detail))
    else:
        checks.append(Check("Missing folders", _OK, "All roles.json folders exist on disk"))

    return checks


def check_role_md_orphans(repo_root: Path, cfg: dict | None) -> Check:
    if cfg is None:
        return Check("ROLE.md files", _INFO, "Skipped — roles.json unavailable")

    roles = cfg.get("roles", {})
    empty_rmd: list[str] = []
    for role_name in roles:
        rmd = role_md_path(repo_root, role_name)
        if rmd.exists() and rmd.stat().st_size == 0:
            empty_rmd.append(role_name)

    if empty_rmd:
        return Check(
            "ROLE.md files", _WARN,
            f"{len(empty_rmd)} ROLE.md file(s) are 0 bytes: {', '.join(empty_rmd)}",
            fix_desc="Nothing auto-fixed (requires user content) — just flagging",
        )
    return Check("ROLE.md files", _OK, "No zero-byte ROLE.md files")


def check_marker_drift(repo_root: Path, cfg: dict | None) -> Check:
    if cfg is None:
        return Check(".last_promote markers", _INFO, "Skipped — roles.json unavailable")

    roles = cfg.get("roles", {})
    suspicious: list[str] = []
    for role_name in roles:
        mem = role_memory_dir(repo_root, role_name)
        marker_path = mem / ".last_promote"
        rmd = role_md_path(repo_root, role_name)
        if not marker_path.exists() or not rmd.exists():
            continue
        ts = read_marker_ts(marker_path)
        try:
            rmd_mtime = rmd.stat().st_mtime
        except OSError:
            continue
        # Flag when ROLE.md is NEWER than .last_promote by more than 1 hour:
        # this means content was added after the last reconcile → needs /role-promote.
        # The healthy state is .last_promote >= ROLE.md mtime (promote ran after last edit).
        if rmd_mtime > ts + 3600:
            suspicious.append(role_name)

    if suspicious:
        return Check(
            ".last_promote markers", _WARN,
            f"ROLE.md is newer than .last_promote for: {', '.join(suspicious)}  "
            "(new content added since last reconcile — run /role-promote)",
        )
    return Check(".last_promote markers", _OK, "Marker timestamps look reasonable")


# ---------------------------------------------------------------------------
# Fix implementations
# ---------------------------------------------------------------------------

def _fix_dangling_hooks(settings: dict, dry_run: bool) -> None:
    """Remove dangling hook entries from settings.json."""
    dangling_coords: set[tuple[str, int, int]] = set()
    for event, gi, hi, _ in _find_dangling_hooks(settings):
        dangling_coords.add((event, gi, hi))

    hooks = settings.get("hooks", {})
    removed = 0
    for event in list(hooks.keys()):
        group_list = hooks[event]
        if not isinstance(group_list, list):
            continue
        for gi, group in enumerate(group_list):
            hook_list = group.get("hooks", [])
            new_hooks = [
                h for hi, h in enumerate(hook_list)
                if (event, gi, hi) not in dangling_coords
            ]
            removed += len(hook_list) - len(new_hooks)
            group["hooks"] = new_hooks
        # Prune empty groups
        hooks[event] = [g for g in group_list if g.get("hooks")]

    print(f"    [doctor] removed {removed} dangling hook entry(s)")
    if not dry_run:
        _save_settings(settings)


def _fix_rehook(repo_root: Path, dry_run: bool) -> None:
    """Re-register hooks for this repo."""
    if dry_run:
        print("    [doctor] --dry-run: would run install_hooks.py")
        return
    from install_hooks import install  # type: ignore[import]
    install(target_root=repo_root, quiet=False)


def _fix_remove_stale_repo_hooks(settings: dict, repo_root: Path, dry_run: bool) -> None:
    """Remove hooks that reference this (engine-missing) repo."""
    # Use resolved path so /tmp == /private/tmp on macOS
    repo_org_resolved = str((repo_root / ".org").resolve())
    hooks = settings.get("hooks", {})
    removed = 0
    for event in list(hooks.keys()):
        group_list = hooks[event]
        if not isinstance(group_list, list):
            continue
        for group in group_list:
            hook_list = group.get("hooks", [])
            new_h = []
            for h in hook_list:
                cmd = str(h.get("command", ""))
                # Quote-insensitive /.org/ extraction + resolve (matches
                # _hooks_for_repo so removal and detection always agree).
                cmd_touches_repo = _cmd_references_org(cmd, repo_org_resolved)
                if not cmd_touches_repo:
                    new_h.append(h)
            removed += len(hook_list) - len(new_h)
            group["hooks"] = new_h
        hooks[event] = [g for g in group_list if g.get("hooks")]
    print(f"    [doctor] removed {removed} stale hook entry(s) for this repo")
    if not dry_run:
        _save_settings(settings)


def _fix_roles_json(repo_root: Path, dry_run: bool) -> None:
    """Restore roles.json from backup or write a default."""
    rf = roles_file(repo_root)
    bak = rf.with_suffix(".json.bak")
    if bak.exists():
        print(f"    [doctor] restoring from {bak.name}")
        if not dry_run:
            shutil.copy2(bak, rf)
    else:
        print("    [doctor] writing default roles.json")
        if not dry_run:
            rf.parent.mkdir(parents=True, exist_ok=True)
            save_roles_cfg(repo_root, default_roles_cfg())


# ---------------------------------------------------------------------------
# Diagnosis table renderer
# ---------------------------------------------------------------------------

def _print_table(checks: list[Check]) -> None:
    col1 = max(len(c.name) for c in checks) + 2
    col2 = 6
    col3 = max(len(c.detail) for c in checks) + 2

    # Cap col3 to terminal width
    try:
        tw = os.get_terminal_size().columns
    except OSError:
        tw = 100
    col3 = min(col3, tw - col1 - col2 - 10)

    hdr = f"  {'Check':<{col1}} {'Status':<{col2}} Detail"
    print(hdr)
    print("  " + "─" * (len(hdr) + 5))
    for c in checks:
        detail = c.detail
        if len(detail) > col3:
            detail = detail[:col3 - 3] + "..."
        print(f"  {c.name:<{col1}} {_status_str(c.status):<{col2+9}}  {detail}")
    print()


# ---------------------------------------------------------------------------
# Main doctor routine
# ---------------------------------------------------------------------------

def run_doctor(target_root: Path, yes: bool = False, dry_run: bool = False) -> int:
    repo_root = target_root.resolve()
    repo_org = str(repo_root / ".org")

    print()
    print("=" * 60)
    print("orgkit doctor — diagnosing", repo_root.name)
    print("=" * 60)
    if dry_run:
        print("  (--dry-run: reporting only, no changes will be made)")
    print()

    settings = _load_settings()

    # ---- Run all checks -------------------------------------------------------
    dangle_check = check_dangling_hooks(settings, repo_org)
    mismatch_check = check_engine_hooks_mismatch(repo_root, settings)
    roles_check, cfg = check_roles_json(repo_root)
    drift_checks = check_drift(repo_root, cfg)
    orphan_check = check_role_md_orphans(repo_root, cfg)
    marker_check = check_marker_drift(repo_root, cfg)

    all_checks: list[Check] = [
        dangle_check,
        mismatch_check,
        roles_check,
        *drift_checks,
        orphan_check,
        marker_check,
    ]

    # ---- Print diagnosis table -----------------------------------------------
    print("DIAGNOSIS")
    print()
    _print_table(all_checks)

    # ---- Summary counts -------------------------------------------------------
    n_fail = sum(1 for c in all_checks if c.status == _FAIL)
    n_warn = sum(1 for c in all_checks if c.status == _WARN)
    n_ok   = sum(1 for c in all_checks if c.status == _OK)
    print(f"  {_c(str(n_ok), 'green')} OK  ·  {_c(str(n_warn), 'yellow')} WARN  ·  {_c(str(n_fail), 'red')} FAIL")
    print()

    # ---- Apply fixes for fixable issues -------------------------------------
    fixable = [c for c in all_checks if c.fixable and c.status in (_WARN, _FAIL)]
    if not fixable:
        print("  Nothing to fix.")
        return 0

    print("Fixable issues:")
    for c in fixable:
        print(f"  [{c.status}] {c.name}: {c.fix_desc}")
    print()

    if not dry_run:
        if yes or _ask_yn("  Apply all fixes above?"):
            _apply_fixes(fixable, repo_root, settings, dry_run)
        else:
            print("  Skipped. Re-run with --yes to apply automatically.")
    else:
        print("  --dry-run: no fixes applied.")

    return 0 if (n_fail == 0 and n_warn == 0) else 1


def _apply_fixes(
    fixable: list[Check],
    repo_root: Path,
    settings: dict | None,
    dry_run: bool,
) -> None:
    for c in fixable:
        print(f"\n  Fixing: {c.name}...")
        if c.name == "Dangling hooks" and settings is not None:
            _fix_dangling_hooks(settings, dry_run)
        elif c.name == "Engine ↔ hooks":
            # Engine presence is decided by the actual .org/ engine files, NOT
            # roles.json. This guarantees the destructive hook-removal fix only
            # fires when the engine is genuinely gone — never just because
            # roles.json is missing/corrupt (which _fix_roles_json repairs).
            if _engine_present(repo_root):
                _fix_rehook(repo_root, dry_run)
            elif settings is not None:
                _fix_remove_stale_repo_hooks(settings, repo_root, dry_run)
        elif c.name == "roles.json":
            _fix_roles_json(repo_root, dry_run)
        else:
            print(f"    [doctor] no auto-fix available for: {c.name}")


def _ask_yn(prompt: str) -> bool:
    try:
        raw = input(prompt + " [y/N] ").strip().lower()
        return raw == "y" or raw == "yes"
    except (EOFError, KeyboardInterrupt):
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="orgkit doctor — diagnose and repair broken orgkit states"
    )
    ap.add_argument("--target", default=None, help="Target repo root (default: auto-detect)")
    ap.add_argument("--yes", action="store_true", help="Apply fixes without prompting")
    ap.add_argument("--dry-run", action="store_true", help="Report only, make no changes")
    args = ap.parse_args()
    root = Path(args.target).resolve() if args.target else detect_repo_root()
    return run_doctor(root, yes=args.yes, dry_run=getattr(args, "dry_run", False))


if __name__ == "__main__":
    sys.exit(main())
