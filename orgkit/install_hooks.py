#!/usr/bin/env python3
"""Idempotently install orgkit engine hooks into ~/.claude/settings.json.

Project-level hooks in .claude/settings.json are unreliable (Claude Code
issue #11544 + post-CVE trust gate). User-global hooks in
~/.claude/settings.json execute reliably on every session.

This script merges four hook entries into the user-global file:

  SessionStart       → sync_org.py, role_inject.py
  UserPromptSubmit   → role_inject.py
  Stop               → sync_org.py, role_digest.py scrape

All command strings point at the TARGET repo's .org/ absolute paths so
hooks work regardless of where Claude Code is started from.

Safe to re-run: skips entries already present (matched by exact command
string). Backs up settings.json once per run with a timestamp suffix.

Usage (called by setup.py after engine is copied):
  python3 .org/install_hooks.py --target /abs/path/to/target/repo
  python3 .org/install_hooks.py           # auto-detect repo root
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from core import detect_repo_root  # noqa: E402

USER_SETTINGS = Path.home() / ".claude" / "settings.json"


def _engine_path(target_root: Path, script: str) -> str:
    """Return a quoted absolute command string for a .org/ script."""
    p = target_root / ".org" / script
    return f'python3 "{p}"'


def _desired_hooks(target_root: Path) -> dict[str, list[str]]:
    """Build the event→commands map using the target repo's absolute paths."""
    inject = _engine_path(target_root, "role_inject.py")
    sync = _engine_path(target_root, "sync_org.py")
    digest = f'python3 "{target_root / ".org" / "role_digest.py"}" scrape'
    return {
        "SessionStart": [sync, inject],
        "UserPromptSubmit": [inject],
        "Stop": [sync, digest],
    }


def _all_commands(group_list: list) -> set[str]:
    seen: set[str] = set()
    for group in group_list:
        for h in group.get("hooks", []):
            cmd = h.get("command")
            if cmd:
                seen.add(cmd)
    return seen


def _ensure(group_list: list, command: str) -> bool:
    """Append the command hook if not already present. Returns True if added."""
    if command in _all_commands(group_list):
        return False
    group_list.append({"hooks": [{"type": "command", "command": command}]})
    return True


def _org_path_in_cmd(cmd: str, target_org: str) -> bool:
    """Return True if cmd references target_org's .org/ path."""
    return target_org in cmd


def uninstall(target_root: Path, yes: bool = False, deep: bool = False) -> int:
    """Remove orgkit hook entries for target_root from ~/.claude/settings.json.

    Makes a timestamped backup before any changes.

    deep=False (default): removes hook entries only.  Engine, slash-command
    stubs, and ORG*.md files are LEFT in place.

    deep=True: in addition to hooks, removes:
      - <repo>/.org/          (engine scripts)
      - <repo>/.claude/commands/role-*.md and orgkit*.md
      - <repo>/ORG.md, ORG_PLAN.md, ORG_MAP.md, ORG_MAP.svg

    NEVER touched regardless of deep flag:
      - CLAUDE.md
      - Any */memory/* directory or file

    When deep=False, prompts the user (or skips with yes=True).
    When deep=True, prompts for confirmation unless yes=True.

    Returns 0 on success, 1 on error.
    """
    root = target_root.resolve()
    target_org = str(root / ".org")

    if not USER_SETTINGS.exists():
        print(f"[uninstall] {USER_SETTINGS} not found — nothing to do.", file=sys.stderr)
        return 0

    raw = USER_SETTINGS.read_text(encoding="utf-8")
    try:
        settings = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"[uninstall] settings.json is not valid JSON: {exc}", file=sys.stderr)
        return 1

    # Timestamped backup
    backup = USER_SETTINGS.with_suffix(f".json.bak.{int(time.time())}")
    shutil.copy2(USER_SETTINGS, backup)
    print(f"[uninstall] backup: {backup}")

    # Find and remove hook entries for this repo
    hooks = settings.get("hooks", {})
    removed_hooks: list[tuple[str, str]] = []
    kept_hooks: list[tuple[str, str]] = []

    for event in list(hooks.keys()):
        group_list = hooks[event]
        if not isinstance(group_list, list):
            continue
        new_groups: list[dict] = []
        for group in group_list:
            hook_list = group.get("hooks", [])
            keep: list[dict] = []
            for h in hook_list:
                cmd = h.get("command", "")
                if _org_path_in_cmd(cmd, target_org):
                    removed_hooks.append((event, cmd))
                else:
                    keep.append(h)
                    kept_hooks.append((event, cmd))
            if keep:
                new_groups.append({**group, "hooks": keep})
            # else: drop the group entirely (was purely orgkit hooks)
        hooks[event] = new_groups

    if removed_hooks:
        USER_SETTINGS.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
        print(f"\n[uninstall] removed {len(removed_hooks)} hook entry(s) for this repo:")
        for event, cmd in removed_hooks:
            print(f"  - {event:20s} {cmd}")
    else:
        print(f"\n[uninstall] no hook entries found for {target_org}")

    if kept_hooks:
        print(f"\n[uninstall] kept {len(kept_hooks)} other hook(s) (untouched)")

    # ------------------------------------------------------------------ #
    # Deep removal: engine dir, slash-command stubs, ORG docs            #
    # CLAUDE.md and */memory/* are NEVER touched.                        #
    # ------------------------------------------------------------------ #
    print()
    engine_dir = root / ".org"
    commands_dir = root / ".claude" / "commands"
    org_files = [
        root / "ORG.md",
        root / "ORG_PLAN.md",
        root / "ORG_MAP.md",
        root / "ORG_MAP.svg",
    ]

    # Collect what actually exists
    deep_items: list[tuple[str, Path]] = []
    if engine_dir.is_dir():
        deep_items.append(("engine dir", engine_dir))
    if commands_dir.is_dir():
        orgkit_cmds = (
            list(commands_dir.glob("orgkit*.md"))
            + list(commands_dir.glob("role-*.md"))
        )
        for cmd_file in orgkit_cmds:
            deep_items.append(("slash command", cmd_file))
    for org_f in org_files:
        if org_f.exists():
            deep_items.append((org_f.name, org_f))

    if not deep_items:
        print("[uninstall] No engine, slash-command stubs, or ORG docs found.")
    elif deep:
        # deep=True: do it (with optional confirmation)
        do_deep = True
        if not yes:
            print("--deep: the following orgkit files will be REMOVED:")
            for label, path in deep_items:
                print(f"  [{label}]  {path}")
            print("  CLAUDE.md and all */memory/* folders are NOT touched.")
            try:
                raw_in = input("  Proceed? [y/N] ").strip().lower()
                do_deep = raw_in in ("y", "yes")
            except (EOFError, KeyboardInterrupt):
                do_deep = False

        if do_deep:
            for label, path in deep_items:
                if path.is_dir():
                    shutil.rmtree(path)
                    print(f"  [uninstall] removed dir:  {path}")
                elif path.is_file():
                    path.unlink()
                    print(f"  [uninstall] removed file: {path}")
        else:
            print("  Cancelled — no files removed.")
    else:
        # deep=False (default): list what was kept, explain --deep
        print("Orgkit-generated files were left in place (hooks only were removed):")
        for label, path in deep_items:
            print(f"  [{label}]  {path}")
        print()
        print("  To remove these too, re-run with --deep:")
        print(f"    python3 .org/install_hooks.py --uninstall --deep")
        print("  CLAUDE.md and all */memory/* folders are NEVER touched.")

    print()
    print("[uninstall] Hooks removed. Restart Claude Code for the change to take effect.")
    print("[uninstall] CLAUDE.md and all memory/ folders were NOT touched.")
    return 0


def install(target_root: Path | None = None, quiet: bool = False) -> int:
    """Merge hook entries into ~/.claude/settings.json.

    Parameters
    ----------
    target_root : Path, optional
        The repo whose .org/ scripts should be registered. Defaults to
        auto-detected repo root.
    quiet : bool
        Suppress normal stdout (used by setup.py which prints its own summary).

    Returns 0 on success, 1 on error.
    """
    root = target_root or detect_repo_root()

    # On a genuinely fresh machine ~/.claude/settings.json may not exist yet
    # (Claude Code creates it lazily). Don't refuse — create the dir and an
    # empty-skeleton settings file so hooks still get wired up.
    created_skeleton = False
    if not USER_SETTINGS.exists():
        try:
            USER_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
            USER_SETTINGS.write_text("{}\n", encoding="utf-8")
            created_skeleton = True
        except OSError as exc:
            print(
                f"error: could not create {USER_SETTINGS}: {exc}",
                file=sys.stderr,
            )
            return 1

    raw = USER_SETTINGS.read_text(encoding="utf-8")
    try:
        settings = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"error: {USER_SETTINGS} is not valid JSON: {exc}", file=sys.stderr)
        return 1

    # Timestamped backup — only one per run. Skip when we just created the
    # skeleton (nothing pre-existing to back up).
    backup: Path | None = None
    if not created_skeleton:
        backup = USER_SETTINGS.with_suffix(f".json.bak.{int(time.time())}")
        shutil.copy2(USER_SETTINGS, backup)

    settings.setdefault("hooks", {})
    desired = _desired_hooks(root)
    added: list[tuple[str, str]] = []
    skipped: list[tuple[str, str]] = []

    for event, commands in desired.items():
        group_list = settings["hooks"].setdefault(event, [])
        for cmd in commands:
            if _ensure(group_list, cmd):
                added.append((event, cmd))
            else:
                skipped.append((event, cmd))

    USER_SETTINGS.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")

    if not quiet:
        if created_skeleton:
            print(f"created:  {USER_SETTINGS} (was missing — wrote empty skeleton)")
        else:
            print(f"backup:   {backup}")
        print(f"settings: {USER_SETTINGS}")
        print()
        print(f"added ({len(added)}):")
        for event, cmd in added:
            print(f"  + {event:20s} {cmd}")
        if skipped:
            print()
            print(f"already present ({len(skipped)}):")
            for event, cmd in skipped:
                print(f"  = {event:20s} {cmd}")
        print()
        print("Next step: restart any open Claude Code sessions for hooks to take effect.")

    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="Install orgkit hooks into ~/.claude/settings.json"
    )
    ap.add_argument(
        "--target",
        default=None,
        help="Target repo root (default: auto-detect via $CLAUDE_PROJECT_DIR / walk-up)",
    )
    ap.add_argument("--quiet", action="store_true", help="Suppress output (used by setup.py)")
    ap.add_argument("--uninstall", action="store_true", help="Remove hook entries for this repo")
    ap.add_argument(
        "--deep",
        action="store_true",
        help=(
            "With --uninstall: also remove the engine (.org/), orgkit slash-command stubs, "
            "and ORG*.md/ORG_MAP.svg.  NEVER touches CLAUDE.md or */memory/*."
        ),
    )
    ap.add_argument("--yes", action="store_true", help="Skip confirmation prompts")
    args = ap.parse_args()

    target = Path(args.target).resolve() if args.target else None
    if args.uninstall:
        root = target or detect_repo_root()
        return uninstall(root, yes=args.yes, deep=args.deep)
    return install(target_root=target, quiet=args.quiet)


if __name__ == "__main__":
    sys.exit(main())
