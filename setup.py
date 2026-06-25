#!/usr/bin/env python3
# pyright: reportMissingImports=false, reportAttributeAccessIssue=false
# (setup.py inserts its own dir on sys.path at runtime so `orgkit.*` resolves;
#  static analysers can't see that, so silence the false-positive here.)
"""orgkit setup.py — interactive bootstrap for the org-memory starter kit.

Installs the orgkit engine into a target repo and wires up Claude Code hooks.

Interactive (no args):
  Walks through: target repo → fresh/migrate → role definition → CLAUDE.md
  seed → copy engine → install hooks → regenerate ORG.md → summary.

Non-interactive (scriptable / testable):
  python3 setup.py --target <path> --fresh --roles "eng:Engineering,growth:Growth" --yes
  python3 setup.py --target <path> --fresh --roles-json '[{"name":"eng","desc":"Backend, frontend, and infra"}]' --yes
  python3 setup.py --target <path> --migrate --yes --role-map "src:eng,site:growth"
  python3 setup.py --target <path> --fresh --roles "a:desc" --yes
  # (scheduled batch capture is opt-in/interactive: run /orgkit-cadence)

Recovery / maintenance flags:
  --analyze         Read-only scan: propose org chart + token savings. Changes nothing.
  --uninstall       Remove orgkit hooks from ~/.claude/settings.json. Prompts for engine removal.
  --rollback        Reverse the last migration (parses MIGRATION.md). Supports --dry-run.
  --doctor          Diagnose + repair broken states. Supports --dry-run.

All flags:
  --target PATH     Target repo root (default: cwd)
  --fresh           Scaffold new roles
  --migrate         Migrate existing messy repo
  --roles STR       Comma-sep "name:desc" pairs (fresh mode, non-interactive).
                    Descriptions must not contain commas — use --roles-json otherwise.
  --roles-json STR  Comma-safe JSON role spec, inline or @file (fresh mode). Preferred
                    transport when descriptions contain commas.
  --role-map STR    Comma-sep "folder:role" pairs (migrate mode, non-interactive)
  --yes             Skip all confirmation prompts
  --dry-run         Report only; make no changes (--rollback / --doctor)
  --install-cron    Deprecated. Prints a pointer to /orgkit-cadence, which sets
                    up scheduled batch capture interactively (auth + cadence).
  --weekly          Deprecated no-op (kept for backward-compat).
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap the import path so `orgkit.*` imports work whether setup.py is
# run from the orgkit repo root (distributable, has an `orgkit/` package dir)
# OR in-place from an installed `.org/` engine (modules are flattened — there
# is no `orgkit/` package next to this file).
#
# Repo layout:   <repo>/setup.py + <repo>/orgkit/<modules>.py        → real package
# Installed:     <target>/.org/setup.py + <target>/.org/<modules>.py → flat
#
# In the flat layout `from orgkit.core import ...` would raise ModuleNotFoundError.
# We synthesise an `orgkit` package object whose __path__ points at this dir so
# that `from orgkit.X import ...` (here AND in the lazy imports below) resolves
# to the flattened sibling modules without rewriting any import site.
# ---------------------------------------------------------------------------
import importlib  # noqa: E402
import types  # noqa: E402

_SETUP_DIR = Path(__file__).resolve().parent
_PKG_DIR = _SETUP_DIR / "orgkit"

if _PKG_DIR.is_dir():
    # Repo layout: the real `orgkit/` package sits next to setup.py.
    sys.path.insert(0, str(_PKG_DIR))
    sys.path.insert(0, str(_SETUP_DIR))
else:
    # Flat / installed layout: modules live directly beside this file (e.g. in
    # <target>/.org/). Put this dir on sys.path so the flat modules import, and
    # register a synthetic `orgkit` package aliasing the same directory so
    # `from orgkit.X import ...` keeps working in-place.
    sys.path.insert(0, str(_SETUP_DIR))
    if "orgkit" not in sys.modules:
        _pkg = types.ModuleType("orgkit")
        _pkg.__path__ = [str(_SETUP_DIR)]  # type: ignore[attr-defined]
        sys.modules["orgkit"] = _pkg
        importlib.invalidate_caches()

# Now import engine modules
from orgkit.core import (  # noqa: E402
    load_roles_cfg,
    save_roles_cfg,
    default_roles_cfg,
    global_claude_md_path,
    role_md_path,
    pending_md_path,
    role_memory_dir,
    safe_write,
    write_marker,
)
from orgkit import interview  # noqa: E402

# ---------------------------------------------------------------------------
# ROLE.md template (used when scaffolding fresh roles)
# ---------------------------------------------------------------------------
_ROLE_MD_TEMPLATE = """# {role} role memory

_This file is the accumulated knowledge base for the **{role}** role.
It is auto-injected into every Claude Code session that starts inside `{role}/`.
Update it by tagging insights in any file with `[LESSON]:` `[PATTERN]:` `[GOTCHA]:` `[TOOL]:` —
the Stop hook picks them up automatically. Run `/role-promote {role}` to merge pending insights._

---

## Context
{desc}

## Best practices

## Patterns

## Gotchas

## Tools / stacks
"""


# ---------------------------------------------------------------------------
# Phase-aware progress display
# ---------------------------------------------------------------------------

# ANSI clear-line / cursor-up (used only when stdout is a TTY)
_TTY = sys.stdout.isatty()

# Phase state symbols
_DONE    = "[✓]"
_RUNNING = "[⏳]"
_WAIT    = "[ ]"


def _render_checklist(phases: list[tuple[str, str]], current_idx: int) -> str:
    """Build the phase checklist string.

    phases: list of (label, detail) where detail is extra info (e.g. "2 roles").
    current_idx: 0-based index of the phase currently running (-1 = all done).
    """
    lines = ["", "orgkit setup " + "─" * 35]
    for i, (label, detail) in enumerate(phases):
        if i < current_idx:
            sym = _DONE
        elif i == current_idx:
            sym = _RUNNING
        else:
            sym = _WAIT
        suffix = f"  ({detail})" if detail else ""
        lines.append(f"  {sym} {label}{suffix}")
    lines.append("")
    return "\n".join(lines)


def _print_checklist(phases: list[tuple[str, str]], current_idx: int, prev_lines: list[int]) -> list[int]:
    """Print the checklist, optionally overwriting the previous render on TTY."""
    text = _render_checklist(phases, current_idx)
    rendered_lines = text.split("\n")

    if _TTY and prev_lines:
        # Move cursor up and clear
        n = prev_lines[0]
        sys.stdout.write(f"\x1b[{n}A\x1b[0J")
        sys.stdout.flush()

    print(text)
    return [len(rendered_lines)]


# ---------------------------------------------------------------------------
# Engine copy
# ---------------------------------------------------------------------------

def copy_engine(target_root: Path, force: bool = False) -> None:
    """Copy orgkit/  →  <target>/.org/  (always overwrites engine .py files)."""
    src_dir = _SETUP_DIR / "orgkit"
    dst_dir = target_root / ".org"
    dst_dir.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    skipped: list[str] = []

    for src_file in sorted(src_dir.iterdir()):
        if src_file.suffix == ".pyc" or src_file.name == "__pycache__":
            continue
        if src_file.is_dir():
            continue
        dst_file = dst_dir / src_file.name
        # Engine .py files are always overwritten (orgkit-owned, not user data).
        # Non-.py files (e.g. config stubs) still respect force flag.
        if src_file.suffix == ".py" or force or not dst_file.exists():
            shutil.copy2(src_file, dst_file)
            copied.append(src_file.name)
        else:
            skipped.append(src_file.name)

    # Also vendor setup.py itself into .org/ so the installed engine is
    # self-contained: the post-install slash commands (orgkit-init, orgkit-doctor)
    # invoke `${CLAUDE_PROJECT_DIR}/.org/setup.py`. Its bootstrap detects the flat
    # layout (no sibling `orgkit/` package) and synthesises one, so the in-place
    # copy runs without ModuleNotFoundError.
    setup_src = _SETUP_DIR / "setup.py"
    if setup_src.is_file():
        shutil.copy2(setup_src, dst_dir / "setup.py")
        copied.append("setup.py")

    print(f"\n[setup] engine copied to {dst_dir.relative_to(target_root)}/")
    if copied:
        print(f"  copied:  {', '.join(copied)}")
    if skipped:
        print(f"  skipped (already present): {', '.join(skipped)}")


# ---------------------------------------------------------------------------
# Slash-command copy  (commands/*.md → <target>/.claude/commands/)
# ---------------------------------------------------------------------------

def copy_commands(target_root: Path) -> None:
    """Copy commands/*.md → <target>/.claude/commands/.

    - Source is resolved relative to this setup.py file (the orgkit repo).
    - Never clobbers an existing same-named command without noting it.
    """
    src_dir = _SETUP_DIR / "commands"
    if not src_dir.is_dir():
        print("[setup] commands/ directory not found — skipping slash-command install")
        return

    dst_dir = target_root / ".claude" / "commands"
    dst_dir.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    noted: list[str] = []   # already-present files we won't clobber

    for src_file in sorted(src_dir.glob("*.md")):
        dst_file = dst_dir / src_file.name
        if dst_file.exists():
            noted.append(src_file.name)
        else:
            shutil.copy2(src_file, dst_file)
            copied.append(src_file.name)

    print(f"\n[setup] slash commands → {dst_dir.relative_to(target_root)}/")
    if copied:
        print(f"  installed: {', '.join(copied)}")
    if noted:
        print(f"  already present (kept existing): {', '.join(noted)}")
    if not copied and not noted:
        print("  (no .md files found in commands/)")


# ---------------------------------------------------------------------------
# Templates copy  (templates/ → <target>/.org/templates/)
# ---------------------------------------------------------------------------

def copy_templates(target_root: Path) -> None:
    """Copy templates/  →  <target>/.org/templates/.

    Resolved relative to this setup.py file.
    """
    src_dir = _SETUP_DIR / "templates"
    if not src_dir.is_dir():
        print("[setup] templates/ directory not found — skipping")
        return

    dst_dir = target_root / ".org" / "templates"
    dst_dir.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    skipped: list[str] = []

    for src_file in sorted(src_dir.iterdir()):
        if src_file.is_dir():
            continue
        dst_file = dst_dir / src_file.name
        if dst_file.exists():
            skipped.append(src_file.name)
        else:
            shutil.copy2(src_file, dst_file)
            copied.append(src_file.name)

    print(f"\n[setup] templates → {dst_dir.relative_to(target_root)}/")
    if copied:
        print(f"  copied: {', '.join(copied)}")
    if skipped:
        print(f"  skipped (already present): {', '.join(skipped)}")


# ---------------------------------------------------------------------------
# Fresh mode
# ---------------------------------------------------------------------------

def _scaffold_role(target_root: Path, name: str, desc: str) -> bool:
    """Create `<role>/memory/ROLE.md` (+ _pending seed + .last_promote marker)
    if absent. Idempotent — never clobbers an existing ROLE.md. Returns True if
    the ROLE.md was newly created. Shared by fresh AND migrate so migration is
    complete in one step (organized folders + seeded brains)."""
    mem_dir = role_memory_dir(target_root, name)
    mem_dir.mkdir(parents=True, exist_ok=True)
    rmd = role_md_path(target_root, name)
    created = False
    if not rmd.exists():
        rmd.write_text(
            _ROLE_MD_TEMPLATE.format(role=name, desc=desc or f"{name} role"),
            encoding="utf-8",
        )
        created = True
    pend = pending_md_path(target_root, name)
    if not pend.exists():
        safe_write(
            pend,
            "# Pending insights\n\n"
            "_Empty. `/capture` and the Stop hook queue work here; "
            "`/role-promote` reconciles it into ROLE.md._\n\n",
        )
    # A fresh, empty brain is not "stale needing reconcile" — stamp now so the
    # SessionStart hook doesn't nag on day one.
    write_marker(mem_dir / ".last_promote")
    return created


def _ensure_global_claude(target_root: Path, content: str = "") -> None:
    """Seed `CLAUDE.md` (global memory) if absent. Uses `content` when given,
    else a minimal personalise-me template. Never clobbers an existing file."""
    gp = global_claude_md_path(target_root)
    if gp.exists():
        return
    body = content or (
        "# Global memory\n\n"
        "_This loads into every Claude Code session in this repo. Keep it lean._\n\n"
        "## Who I am\n\n_TODO_\n\n"
        "## What I'm building\n\n_TODO_\n\n"
        "## How I work\n\n_TODO_\n"
    )
    safe_write(gp, body)
    print("  [setup] created: CLAUDE.md")


def run_fresh(target_root: Path, role_defs: list[dict[str, str]], claude_md_content: str) -> None:
    """Scaffold roles, memory dirs, ROLE.md files, and CLAUDE.md."""
    # Ensure roles.json exists
    rf_path = target_root / ".org" / "roles.json"
    if rf_path.exists():
        cfg = load_roles_cfg(target_root)
    else:
        cfg = default_roles_cfg()
        (target_root / ".org").mkdir(parents=True, exist_ok=True)

    roles: dict = cfg.setdefault("roles", {})

    for role_def in role_defs:
        name = role_def["name"]
        desc = role_def.get("desc", "")

        if name not in roles:
            roles[name] = {"desc": desc, "folders": []}
        else:
            # Don't overwrite existing — just update desc if blank
            if not roles[name].get("desc"):
                roles[name]["desc"] = desc

        created = _scaffold_role(target_root, name, desc)
        rmd = role_md_path(target_root, name)
        print(f"  [fresh] {'created' if created else 'exists (kept)'}: {rmd.relative_to(target_root)}")

    save_roles_cfg(target_root, cfg)
    print(f"  [fresh] roles.json saved with {len(roles)} role(s)")

    # Seed CLAUDE.md (global memory)
    _ensure_global_claude(target_root, claude_md_content)


# ---------------------------------------------------------------------------
# Migrate mode
# ---------------------------------------------------------------------------

def run_migrate(
    target_root: Path,
    role_map: dict[str, str],
    yes: bool,
    dry_run: bool = False,
) -> None:
    """Move folders + fix refs using migrate.py."""
    # Lazy import to avoid module-load overhead when not migrating
    from orgkit.migrate import move_folders, fix_path_refs, write_migration_md

    if not role_map:
        print("[setup] No folders assigned to roles — nothing to migrate.")
        return

    print(f"\n[setup] migrate: moving {len(role_map)} folder(s)...")
    records = move_folders(target_root, role_map, dry_run=dry_run)
    fix_summary = fix_path_refs(target_root, records, dry_run=dry_run)

    if not dry_run:
        write_migration_md(target_root, records, fix_summary)


# ---------------------------------------------------------------------------
# Hook installation
# ---------------------------------------------------------------------------

def run_install_hooks(target_root: Path) -> int:
    """Install lifecycle hooks. Returns the installer's exit code (0 == ok)."""
    from orgkit.install_hooks import install
    print("\n[setup] installing hooks into ~/.claude/settings.json ...")
    rc = install(target_root=target_root, quiet=True)
    if rc == 0:
        print("  [hooks] done")
    else:
        print("  [hooks] FAILED: hook installation returned non-zero — see error above.")
        print("  [hooks] remediation: re-run "
              f'`python3 "{target_root / ".org" / "install_hooks.py"}" --target "{target_root}"` '
              "and fix the reported cause (e.g. invalid ~/.claude/settings.json).")
    return rc


# ---------------------------------------------------------------------------
# Sync ORG.md
# ---------------------------------------------------------------------------

def run_sync_org(target_root: Path) -> dict:
    """Regenerate ORG.md (no auto-stub) and return real post-sync counts.

    Returns {"roles": int, "unmapped": list[str]} computed straight from the
    on-disk roles.json + filesystem so the summary reports the true role count
    (not an assumed one) and can warn about still-at-root folders.
    """
    from orgkit.sync_org import regenerate
    from orgkit.core import list_top_dirs

    print("\n[setup] regenerating ORG.md ...")
    # auto_stub=False: never inject TODO stub roles for deferred folder moves.
    regenerate(target_root, auto_stub=False)

    cfg = load_roles_cfg(target_root)
    roles = cfg.get("roles", {})
    excl = set(cfg.get("_meta", {}).get("exclude_dirs", []))
    on_disk = set(list_top_dirs(target_root, excl))
    mapped = {f for r in roles.values() for f in r.get("folders", [])}
    unmapped = sorted(on_disk - mapped - set(roles.keys()))
    return {"roles": len(roles), "unmapped": unmapped}


# ---------------------------------------------------------------------------
# Render ORG_PLAN.md
# ---------------------------------------------------------------------------

def run_render_plan(target_root: Path) -> None:
    from orgkit.plan import render_plan
    print("\n[setup] generating ORG_PLAN.md ...")
    render_plan(target_root)


# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------

def print_summary(
    target_root: Path,
    mode: str,
    roles: list[str],
    hooks_rc: int = 0,
    sync_info: dict | None = None,
) -> None:
    sync_info = sync_info or {}
    print("\n" + "=" * 60)
    print("orgkit setup complete!")
    print("=" * 60)
    print(f"\nTarget repo : {target_root}")
    print(f"Mode        : {mode}")
    print(f"Roles       : {', '.join(roles) if roles else '(none)'}")
    print(f"\nWhat was installed:")
    print(f"  {target_root / '.org'}/               ← engine scripts + templates")
    print(f"  {target_root / '.claude' / 'commands'}/  ← slash commands")
    print(f"  {target_root / 'ORG.md'}              ← auto-generated org chart")
    print(f"  {target_root / 'ORG_PLAN.md'}         ← adoption roadmap (computed)")
    if hooks_rc == 0:
        print(f"  ~/.claude/settings.json               ← hooks registered")
    else:
        print(f"  ~/.claude/settings.json               ← HOOKS NOT REGISTERED (install failed)")
        print(f"      role memory will NOT auto-inject until you fix this. Re-run:")
        print(f'      python3 "{target_root / ".org" / "install_hooks.py"}" --target "{target_root}"')
    unmapped = sync_info.get("unmapped") or []
    if unmapped:
        print(f"\nHeads up: {len(unmapped)} folder(s) still at the repo root are NOT mapped to any role:")
        print(f"  {', '.join(unmapped)}")
        print(f"  They were left as-is (not stubbed as roles). Map + move them with /orgkit-migrate.")
    print(f"\nNext steps:")
    print(f"  1. Restart Claude Code so hooks take effect.")
    print(f"  2. Open a session inside a role folder — ROLE.md auto-injects.")
    print(f"  3. Tag insights with [LESSON]: / [PATTERN]: / [GOTCHA]: / [TOOL]:  in any file.")
    print(f"  4. Run /role-promote <role> when prompted to merge pending insights.")
    print(f"  5. Check ORG_PLAN.md anytime for your adoption progress.")
    print()


# ---------------------------------------------------------------------------
# Repo URL helper (for --map watermark)
# ---------------------------------------------------------------------------

_DEFAULT_REPO_URL = "github.com/hdk10/orgkit"


def _derive_repo_url(target_root: Path) -> str:
    """Derive a display URL from the target repo's git remote origin.

    Handles both HTTPS and SSH remotes:
      https://github.com/user/repo.git  →  github.com/user/repo
      git@github.com:user/repo.git      →  github.com/user/repo

    Falls back to _DEFAULT_REPO_URL if no remote is configured.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(target_root), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        url = result.stdout.strip()
        if not url:
            return _DEFAULT_REPO_URL
        # Strip trailing .git
        if url.endswith(".git"):
            url = url[:-4]
        # Convert SSH syntax: git@github.com:user/repo  →  github.com/user/repo
        if url.startswith("git@"):
            url = url[len("git@"):]
            url = url.replace(":", "/", 1)
        # Strip https:// or http:// prefix
        for prefix in ("https://", "http://"):
            if url.startswith(prefix):
                url = url[len(prefix):]
                break
        return url or _DEFAULT_REPO_URL
    except Exception:
        return _DEFAULT_REPO_URL


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="setup.py",
        description="orgkit — install org memory engine into a target repo",
    )
    ap.add_argument("--target", default=None, help="Target repo root (default: cwd)")
    ap.add_argument("--fresh", action="store_true", help="Scaffold new roles (fresh mode)")
    ap.add_argument("--migrate", action="store_true", help="Migrate existing messy repo")
    ap.add_argument(
        "--roles",
        default=None,
        help='Comma-sep "name:desc" pairs for fresh mode. E.g. "eng:Engineering,growth:Growth". '
             'WARNING: split on commas — descriptions must NOT contain commas or the list shatters '
             'into phantom roles. For comma-bearing descriptions use --roles-json instead.',
    )
    ap.add_argument(
        "--roles-json",
        dest="roles_json",
        default=None,
        help='Comma-SAFE JSON role spec for fresh mode (preferred over --roles). Inline JSON or @file. '
             'E.g. \'[{"name":"eng","desc":"Backend, frontend, and infra"}]\' or @roles.json. '
             'Accepts a JSON array of {name,desc} objects or a name->desc object. Descriptions may contain commas.',
    )
    ap.add_argument(
        "--role-map",
        dest="role_map",
        default=None,
        help='Comma-sep "folder:role" pairs for non-interactive migrate (--migrate --yes). '
             'E.g. "src:eng,marketing-site:growth". Unlisted folders are left in place.',
    )
    ap.add_argument("--yes", action="store_true", help="Skip all confirmation prompts")
    ap.add_argument("--dry-run", action="store_true", help="Report only, make no changes (--rollback/--doctor)")
    ap.add_argument("--install-cron", action="store_true", help="Also install periodic reconcile job")
    ap.add_argument("--weekly", action="store_true", default=True, help="Weekly cron (default)")
    # Recovery / maintenance flags
    ap.add_argument(
        "--analyze",
        action="store_true",
        help="Read-only scan: propose org chart + token-savings estimate. Changes nothing.",
    )
    ap.add_argument(
        "--uninstall",
        action="store_true",
        help="Remove orgkit hooks for this repo from ~/.claude/settings.json.",
    )
    ap.add_argument(
        "--rollback",
        action="store_true",
        help="Reverse the last migration recorded in MIGRATION.md.",
    )
    ap.add_argument(
        "--doctor",
        action="store_true",
        help="Diagnose and repair common broken orgkit states.",
    )
    ap.add_argument(
        "--map",
        action="store_true",
        help="Render a shareable org-map SVG (ORG_MAP.svg). Screenshot it, post it.",
    )
    ap.add_argument(
        "--deep",
        action="store_true",
        help="With --uninstall: also remove engine files (.org/) and slash commands (.claude/commands/).",
    )
    return ap.parse_args()


def main() -> int:
    args = parse_args()

    # -----------------------------------------------------------------------
    # RECOVERY / READ-ONLY modes — handle before normal setup flow
    # -----------------------------------------------------------------------
    _recovery_root = Path(args.target).resolve() if args.target else Path.cwd().resolve()

    if args.analyze:
        from orgkit.analyze import run_analyze
        return run_analyze(_recovery_root)

    if args.uninstall:
        from orgkit.install_hooks import uninstall
        return uninstall(target_root=_recovery_root, yes=args.yes, deep=args.deep)

    if args.rollback:
        from orgkit.migrate import rollback
        dry_run = getattr(args, "dry_run", False)
        return rollback(_recovery_root, dry_run=dry_run)

    if args.doctor:
        from orgkit.doctor import run_doctor
        dry_run = getattr(args, "dry_run", False)
        return run_doctor(_recovery_root, yes=args.yes, dry_run=dry_run)

    if args.map:
        from orgkit.orgmap import run as run_map
        repo_url = _derive_repo_url(_recovery_root)
        run_map(_recovery_root, None, None, repo_url=repo_url)
        return 0

    # -----------------------------------------------------------------------
    # Normal setup flow
    # -----------------------------------------------------------------------
    print("=" * 60)
    print("orgkit — org memory starter kit setup")
    print("=" * 60)

    # Define the phases for progress display
    phases: list[tuple[str, str]] = [
        ("Phase 1  Detect target repo",              ""),
        ("Phase 2  Design roles",                    ""),
        ("Phase 3  Scaffold memory + global CLAUDE.md", ""),
        ("Phase 4  Install engine + slash commands", ""),
        ("Phase 5  Register hooks",                  ""),
        ("Phase 6  Generate org map + adoption plan",""),
    ]
    prev: list[int] = []

    def tick(idx: int, detail: str = "") -> None:
        """Advance checklist display to show phase `idx` as running."""
        nonlocal phases
        if detail:
            # Update the detail string for the current phase
            label, _ = phases[idx]
            phases[idx] = (label, detail)
        nonlocal prev
        prev = _print_checklist(phases, idx, prev)

    # --- Phase 1: Detect target repo ---
    tick(0)
    target_root = interview.ask_target(args)

    if not target_root.exists():
        if interview.confirm(f"Directory does not exist. Create it? {target_root}", args.yes):
            target_root.mkdir(parents=True)
        else:
            print("Aborted.")
            return 1

    mode = interview.ask_mode(args)
    phases[0] = (phases[0][0], str(target_root.name))
    prev = _print_checklist(phases, 1, prev)

    # --- Phase 2: Design roles ---
    tick(1)
    role_defs: list[dict[str, str]] = []
    claude_md = ""
    if mode == "fresh":
        role_defs = interview.ask_roles_fresh(args)
        claude_md = interview.ask_global_claude_md(args, target_root)

        if not interview.confirm(
            f"\nScaffold {len(role_defs)} role(s) into {target_root}?", args.yes
        ):
            print("Aborted.")
            return 1

        phases[1] = (phases[1][0], f"{len(role_defs)} roles")
        role_names = [r["name"] for r in role_defs]

    elif mode == "migrate":
        from orgkit.migrate import scan_unmapped
        from orgkit.core import load_roles as _load_roles

        print(f"\n[setup] scanning {target_root} for unmapped folders...")
        unmapped = scan_unmapped(target_root)
        existing_role_names = list(_load_roles(target_root).keys())
        role_map = interview.ask_roles_for_migrate(
            unmapped, existing_role_names, args.yes,
            preset_map=getattr(args, "role_map", None),
        )

        if not role_map:
            print("[setup] No folders to migrate — exiting migrate flow.")
        else:
            if not interview.confirm(
                f"\nMove {len(role_map)} folder(s) and fix path refs?", args.yes
            ):
                print("Aborted.")
                return 1
            run_migrate(target_root, role_map, yes=args.yes)

        # Migration is complete in ONE step: organized folders AND seeded
        # brains. Scaffold a ROLE.md per role + a global CLAUDE.md (idempotent;
        # never clobbers anything that already exists).
        _roles_cfg = _load_roles(target_root)
        role_names = list(_roles_cfg.keys())
        for _rn in role_names:
            _scaffold_role(target_root, _rn, _roles_cfg.get(_rn, {}).get("desc", ""))
        _ensure_global_claude(target_root)
        if role_names:
            print(f"  [migrate] seeded ROLE.md for {len(role_names)} role(s) + CLAUDE.md")
        phases[1] = (phases[1][0], f"{len(role_names)} roles")

    else:
        print(f"Unknown mode: {mode}", file=sys.stderr)
        return 1

    prev = _print_checklist(phases, 2, prev)

    # --- Phase 3: Scaffold memory + global CLAUDE.md ---
    tick(2)
    if mode == "fresh":
        run_fresh(target_root, role_defs, claude_md)
        phases[2] = (phases[2][0], f"{len(role_defs)} role brains seeded")
    prev = _print_checklist(phases, 3, prev)

    # --- Phase 4: Install engine + slash commands + templates ---
    tick(3)
    copy_engine(target_root)
    copy_commands(target_root)
    copy_templates(target_root)
    phases[3] = (phases[3][0], "engine + commands + templates")
    prev = _print_checklist(phases, 4, prev)

    # --- Phase 5: Register hooks ---
    tick(4)
    hooks_rc = run_install_hooks(target_root)
    phases[4] = (
        phases[4][0],
        "hooks registered" if hooks_rc == 0 else "FAILED — hooks NOT registered",
    )
    prev = _print_checklist(phases, 5, prev)

    # --- Phase 6: Generate org map + adoption plan ---
    tick(5)
    sync_info = run_sync_org(target_root)
    run_render_plan(target_root)

    # Optional: scheduled batch capture.
    # This is now an opt-in, INTERACTIVE flow — it needs a subscription token
    # (`claude setup-token`), an auth probe, and a cadence/slot choice based on
    # your real usage. setup.py can't do that non-interactively, so we point you
    # at the command that can instead of silently doing the wrong thing.
    if args.install_cron:
        print(
            "\n[setup] Scheduled batch capture is opt-in and interactive.\n"
            "  Run  /orgkit-cadence  in a Claude Code session: it analyses your\n"
            "  usage, recommends a cadence + awake-aware time slots, and installs\n"
            "  the cron (Sonnet-only, subscription token, never an API key)."
        )

    # Mark all done (current_idx = total = all ticked)
    prev = _print_checklist(phases, len(phases), prev)

    # --- Summary ---
    print_summary(target_root, mode, role_names, hooks_rc=hooks_rc, sync_info=sync_info)

    return 0


if __name__ == "__main__":
    sys.exit(main())
