#!/usr/bin/env python3
"""orgkit.plan — compute and render ORG_PLAN.md.

Writes <repo>/ORG_PLAN.md: a phased onboarding + steady-state roadmap as
GitHub task-list checkboxes whose state is COMPUTED from real repo state,
not hardcoded.

Usage:
  python3 .org/plan.py              # auto-detect repo root
  python3 .org/plan.py --target /path/to/repo
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from core import (  # noqa: E402
    detect_repo_root,
    load_roles,
    read_marker_ts,
    global_claude_md_path,
    role_md_path,
    pending_md_path,
    role_memory_dir,
)

# ---------------------------------------------------------------------------
# Staleness threshold (must match install_cron / role_inject)
# ---------------------------------------------------------------------------
_STALE_DAYS = 7

# ---------------------------------------------------------------------------
# Template skeleton fingerprints — used to detect "not yet personalised"
# ---------------------------------------------------------------------------
_CLAUDE_TEMPLATE_SENTINEL = "_TODO: Describe what this repo is and what lives here._"
_ROLE_TEMPLATE_SENTINEL = "It is auto-injected into every Claude Code session"
_ROLE_DESC_PLACEHOLDER = "TODO — auto-added by sync_org"


# ---------------------------------------------------------------------------
# Individual check helpers
# ---------------------------------------------------------------------------

def _engine_installed(repo_root: Path) -> bool:
    """Engine presence = the .org/ engine modules (core.py et al.), not
    roles.json. A missing/corrupt roles.json is a separate, recoverable
    condition and must not read as "engine gone"."""
    org = repo_root / ".org"
    if not org.is_dir():
        return False
    if (org / "core.py").is_file():
        return True
    return any(org.glob("*.py"))


def _hooks_registered(repo_root: Path) -> bool:
    """Check ~/.claude/settings.json for hook entries referencing this repo."""
    settings = Path.home() / ".claude" / "settings.json"
    if not settings.exists():
        return False
    try:
        data = json.loads(settings.read_text(encoding="utf-8"))
    except Exception:
        return False
    # Resolve both sides to handle /tmp vs /private/tmp symlinks on macOS
    repo_org = str((repo_root / ".org").resolve())
    hooks = data.get("hooks", {})
    for group_list in hooks.values():
        if not isinstance(group_list, list):
            continue
        for group in group_list:
            if not isinstance(group, dict):
                continue
            for h in group.get("hooks", []):
                cmd = h.get("command", "")
                if _cmd_references_org(cmd, repo_org):
                    return True
    return False


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
    # Raw substring fallback (covers the no-symlink case directly).
    if repo_org_resolved in cmd:
        return True
    for m in _ORG_PATH_RE.findall(cmd):
        # m is like /abs/.org/sync_org.py — resolve up to the .org dir.
        idx = m.find("/.org/")
        org_dir = m[: idx + len("/.org")]
        try:
            if str(Path(org_dir).resolve()) == repo_org_resolved:
                return True
        except Exception:
            continue
    return False


def _claude_md_exists_nonempty(repo_root: Path) -> bool:
    p = global_claude_md_path(repo_root)
    if not p.exists():
        return False
    return bool(p.read_text(encoding="utf-8").strip())


def _claude_md_personalised(repo_root: Path) -> bool:
    p = global_claude_md_path(repo_root)
    if not p.exists():
        return False
    text = p.read_text(encoding="utf-8")
    return _CLAUDE_TEMPLATE_SENTINEL not in text


def _at_least_one_role(repo_root: Path) -> bool:
    return bool(load_roles(repo_root))


def _all_roles_have_desc(repo_root: Path) -> tuple[bool, list[str]]:
    """Returns (all_have_desc, list_of_missing)."""
    roles = load_roles(repo_root)
    missing = [
        name for name, info in roles.items()
        if not info.get("desc") or _ROLE_DESC_PLACEHOLDER in info.get("desc", "")
    ]
    return (len(missing) == 0 and bool(roles), missing)


def _at_least_one_project(repo_root: Path) -> tuple[bool, str]:
    """Returns (found, first_project_path_hint)."""
    roles = load_roles(repo_root)
    for role_name in roles:
        role_dir = repo_root / role_name
        if not role_dir.is_dir():
            continue
        for sub in role_dir.iterdir():
            if sub.is_dir() and not sub.name.startswith(".") and sub.name != "memory":
                return (True, f"{role_name}/{sub.name}")
    return (False, "")


def _all_projects_have_memory(repo_root: Path) -> tuple[bool, list[str]]:
    """Returns (all_have_PROJECT_md, list_of_missing_paths)."""
    roles = load_roles(repo_root)
    missing = []
    any_found = False
    for role_name in roles:
        role_dir = repo_root / role_name
        if not role_dir.is_dir():
            continue
        for sub in role_dir.iterdir():
            if sub.is_dir() and not sub.name.startswith(".") and sub.name != "memory":
                any_found = True
                mem = sub / "memory" / "PROJECT.md"
                if not mem.exists():
                    missing.append(f"{role_name}/{sub.name}")
    return (any_found and len(missing) == 0, missing)


def _at_least_one_role_md_with_content(repo_root: Path) -> bool:
    """At least one ROLE.md has content beyond the template skeleton."""
    roles = load_roles(repo_root)
    for role_name in roles:
        rmd = role_md_path(repo_root, role_name)
        if rmd.exists():
            text = rmd.read_text(encoding="utf-8")
            if _ROLE_TEMPLATE_SENTINEL not in text and text.strip():
                return True
    return False


def _at_least_one_lesson_tag(repo_root: Path) -> bool:
    """Search for [LESSON]/[GOTCHA]/[PATTERN]/[TOOL] tags in .md files under role dirs.

    A tag counts only when it appears at the start of a line (possibly after
    whitespace/bullets), NOT when it's inside backtick-quoted template text.
    """
    import re as _re
    roles = load_roles(repo_root)
    # Match tag at the start of a line (after optional whitespace/list-bullets)
    _TAG_RE = _re.compile(
        r"^\s*(?:[-*>]\s*)?\[(LESSON|GOTCHA|PATTERN|TOOL)\]:",
        _re.MULTILINE,
    )
    for role_name in roles:
        role_dir = repo_root / role_name
        if not role_dir.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(role_dir):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in {"__pycache__", "node_modules"}]
            for fn in filenames:
                if not fn.endswith(".md"):
                    continue
                fpath = Path(dirpath) / fn
                try:
                    text = fpath.read_text(encoding="utf-8")
                    if _TAG_RE.search(text):
                        return True
                except Exception:
                    continue
    return False


def _at_least_one_role_reconciled(repo_root: Path) -> bool:
    """At least one role has a .last_promote marker."""
    roles = load_roles(repo_root)
    for role_name in roles:
        mem = role_memory_dir(repo_root, role_name)
        if read_marker_ts(mem / ".last_promote") > 0.0:
            return True
    return False


def _no_stale_with_pending(repo_root: Path) -> tuple[bool, list[str]]:
    """Returns (all_clean, list_of_stale+pending roles)."""
    import time
    roles = load_roles(repo_root)
    stale_pending = []
    now = time.time()
    for role_name in roles:
        mem = role_memory_dir(repo_root, role_name)
        ts = read_marker_ts(mem / ".last_promote")
        is_stale = (now - ts) >= _STALE_DAYS * 86400

        pend = pending_md_path(repo_root, role_name)
        has_pending = False
        if pend.exists():
            lines = pend.read_text(encoding="utf-8").splitlines()
            has_pending = any(
                ln.strip()
                and not ln.strip().startswith("#")
                and not ln.strip().startswith("<!--")
                and not ln.strip().startswith("_")  # italic-meta seeds/markers
                for ln in lines
            )
        if is_stale and has_pending:
            stale_pending.append(role_name)
    return (len(stale_pending) == 0, stale_pending)


# ---------------------------------------------------------------------------
# Progress bar renderer
# ---------------------------------------------------------------------------

def _progress_bar(done: int, total: int, width: int = 10) -> str:
    filled = round(width * done / total) if total else 0
    bar = "█" * filled + "░" * (width - filled)
    return f"{bar} {done}/{total} done"


# ---------------------------------------------------------------------------
# Main render function
# ---------------------------------------------------------------------------

def render_plan(repo_root: Path) -> None:
    """Compute repo state and write ORG_PLAN.md.

    No-op on a repo that was never onboarded (no .org/roles.json). Without
    this guard a SessionStart hook would drop an unsolicited ORG_PLAN.md into
    every repo the user opens Claude Code in, breaking the documented
    "no-op gracefully" / "safe by design" promise.
    """
    if not (repo_root / ".org" / "roles.json").exists():
        return

    # ---- Compute all checks ------------------------------------------------
    engine_ok         = _engine_installed(repo_root)
    hooks_ok          = _hooks_registered(repo_root)
    claude_md_ok      = _claude_md_exists_nonempty(repo_root)
    claude_personalised = _claude_md_personalised(repo_root)

    has_role          = _at_least_one_role(repo_root)
    descs_ok, missing_descs = _all_roles_have_desc(repo_root)
    has_project, _ = _at_least_one_project(repo_root)
    projects_mem_ok, missing_mem = _all_projects_have_memory(repo_root)

    role_md_has_content = _at_least_one_role_md_with_content(repo_root)
    has_lesson_tag      = _at_least_one_lesson_tag(repo_root)

    any_reconciled      = _at_least_one_role_reconciled(repo_root)
    no_stale_ok, stale_roles = _no_stale_with_pending(repo_root)

    # ---- Map to checkbox items  -------------------------------------------
    # Each item: (checked, text, hint_if_unchecked)
    items: list[tuple[bool, str, str]] = [
        # Phase 1
        (engine_ok,    "engine installed (`.org/` present)",                         "→ run `python3 setup.py --target . --fresh --roles \"<role>:<desc>\" --yes`"),
        (hooks_ok,     "hooks registered in `~/.claude/settings.json`",              "→ re-run `python3 setup.py` or `python3 .org/install_hooks.py`"),
        (claude_md_ok, "`CLAUDE.md` exists and is non-empty",                        "→ run setup, or create `CLAUDE.md` manually with repo description"),
        # Phase 2
        (has_role,          "at least one role defined",                             "→ edit `.org/roles.json` or re-run setup with `--roles`"),
        (descs_ok,          "every role has a real one-line description",             f"→ edit `.org/roles.json`, add `desc` for: {', '.join(missing_descs) or 'none'}"),
        (claude_personalised, "`CLAUDE.md` personalised (not just the template)",    "→ open `CLAUDE.md` and fill in the TODO sections"),
        # Phase 3
        (has_project,       "at least one project folder exists under a role",       "→ run `/new-project` or `mkdir <role>/<project>`"),
        (projects_mem_ok,   "every project has `memory/PROJECT.md`",                 f"→ create `memory/PROJECT.md` in: {', '.join(missing_mem[:3]) or 'none'}{'…' if len(missing_mem) > 3 else ''}"),
        # Phase 4
        (role_md_has_content, "at least one `ROLE.md` has content beyond the template skeleton", "→ run `/role-promote <role>` or add notes directly to `<role>/memory/ROLE.md`"),
        (has_lesson_tag,    "at least one `[LESSON]/[GOTCHA]/[PATTERN]/[TOOL]` tag exists",     "→ tag an insight in any `.md` file with `[LESSON]: your note`"),
        # Phase 5
        (any_reconciled,    "at least one role has been reconciled (`.last_promote` marker)",   "→ run `/role-promote <role>`"),
        (no_stale_ok,       f"no role is stale-with-pending (all green){' — stale: ' + ', '.join(stale_roles) if stale_roles else ''}",
                            "→ run `/role-promote <role>` for each stale role"),
    ]

    total  = len(items)
    done   = sum(1 for chk, _, _ in items if chk)
    bar    = _progress_bar(done, total)

    # ---- Build markdown ----------------------------------------------------
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines: list[str] = [
        "# ORG_PLAN — Onboarding & Adoption Roadmap",
        "",
        f"_Auto-generated {ts} by `.org/plan.py`. Re-runs every SessionStart/Stop._",
        "",
        f"**Progress: {bar}**",
        "",
    ]

    phase_defs = [
        ("Phase 1 — Foundations",   items[0:3]),
        ("Phase 2 — Shape your org", items[3:6]),
        ("Phase 3 — First work",     items[6:8]),
        ("Phase 4 — Capture",        items[8:10]),
        ("Phase 5 — Compounding",    items[10:12]),
    ]

    for phase_title, phase_items in phase_defs:
        phase_done = sum(1 for chk, _, _ in phase_items if chk)
        lines.append(f"## {phase_title}  _{phase_done}/{len(phase_items)} done_")
        lines.append("")
        for chk, text, hint in phase_items:
            mark = "x" if chk else " "
            lines.append(f"- [{mark}] {text}")
            if not chk:
                lines.append(f"  {hint}")
        lines.append("")

    # Next unchecked items
    next_up = [(text, hint) for chk, text, hint in items if not chk][:3]
    if next_up:
        lines += ["## Next up", ""]
        for text, hint in next_up:
            lines.append(f"- **{text}**")
            lines.append(f"  {hint}")
        lines.append("")
    else:
        lines += ["## All done! 🎉", "", "_All adoption checklist items are complete._", ""]

    out = repo_root / "ORG_PLAN.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"[plan] wrote {out.relative_to(repo_root)} — {done}/{total} items done")


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="orgkit plan — render ORG_PLAN.md")
    ap.add_argument("--target", default=None, help="Target repo root (default: auto-detect)")
    args = ap.parse_args()
    repo_root = Path(args.target).resolve() if args.target else detect_repo_root()
    render_plan(repo_root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
