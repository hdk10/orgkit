#!/usr/bin/env python3
"""Regenerate ORG.md from .org/roles.json + current filesystem state.

Designed to run on every Claude Code session start (via SessionStart hook).
Path-agnostic: anchors to detected repo root, never hardcoded /Users/ paths.

Behaviour
---------
- Flags UNMAPPED dirs: on disk at root but no role assigned.
- Flags MISSING folders: in roles.json but not on disk anywhere.
- Optionally auto-promotes completely unmapped top-level dirs as stub roles in
  roles.json (opt-in via --auto-stub / auto_stub=True). OFF by default so a
  fresh install with deferred folder moves never pollutes the curated role set.
- Skips regeneration when ORG.md is newer than root/roles.json/all role subdirs.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Locate this module's own engine directory so the import always resolves,
# whether this file lives at <repo>/.org/sync_org.py (installed) or inside
# the orgkit source tree at orgkit/sync_org.py.
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from core import (  # noqa: E402
    detect_repo_root,
    load_roles_cfg,
    save_roles_cfg,
    list_top_dirs,
)


def _list_subdirs(parent: Path) -> list[str]:
    if not parent.is_dir():
        return []
    return sorted(p.name for p in parent.iterdir() if p.is_dir() and not p.name.startswith("."))


def _list_loose_files(repo_root: Path, exclude_names: set[str]) -> list[str]:
    return sorted(
        p.name for p in repo_root.iterdir()
        if p.is_file() and not p.name.startswith(".") and p.name not in exclude_names
    )


def _folder_locations(folder: str, role_names: set[str], on_disk: set[str], repo_root: Path) -> list[str]:
    """Where does this folder exist on disk? Returns list of relative paths."""
    locs: list[str] = []
    if folder in on_disk:
        locs.append(folder)
    for rn in role_names:
        # Use _list_subdirs so we only walk actual directories
        if folder in _list_subdirs(repo_root / rn):
            locs.append(f"{rn}/{folder}")
    return locs


def needs_regen(repo_root: Path, out_file: Path) -> bool:
    """Return True if ORG.md needs to be rebuilt.

    Staleness must reflect DEEP changes, not just top-level dir mtimes. On
    macOS, editing a file nested inside a role dir (e.g. <role>/memory/ROLE.md
    or a [LESSON]-tagged note in <role>/<project>/) does NOT bump the role
    dir's own mtime, so a top-level-only stat() would miss exactly the edits
    produced by the core orgkit workflow. We therefore walk each role/.org
    subtree for the newest .md mtime and compare that against ORG.md.
    """
    if not out_file.exists():
        return True
    out_mtime = out_file.stat().st_mtime
    if repo_root.stat().st_mtime > out_mtime:
        return True
    rf = repo_root / ".org" / "roles.json"
    if rf.exists() and rf.stat().st_mtime > out_mtime:
        return True
    for p in repo_root.iterdir():
        if not p.name.startswith(".") and p.is_dir():
            try:
                if p.stat().st_mtime > out_mtime:
                    return True
            except OSError:
                continue
            # Top-level dir mtime alone misses nested edits (macOS doesn't
            # propagate child mtimes to the parent). Walk the subtree for the
            # newest .md file so deep ROLE.md/_pending.md/lesson edits count.
            if _newest_md_mtime(p) > out_mtime:
                return True
    # The engine's own .org/ dir (markers, config) can change too.
    org_dir = repo_root / ".org"
    if org_dir.is_dir() and _newest_md_mtime(org_dir) > out_mtime:
        return True
    return False


def _newest_md_mtime(root: Path) -> float:
    """Return the newest mtime among .md files (and ROLE-memory markers) under
    ``root``, walking the full subtree. Returns 0.0 if nothing is found.

    Skips noisy/irrelevant dirs (VCS, caches, vendored deps) so a session
    start hook stays cheap.
    """
    newest = 0.0
    skip = {".git", "__pycache__", "node_modules", ".venv", "venv"}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for fn in filenames:
            # .md files plus the role-memory markers the adoption plan tracks.
            if fn.endswith(".md") or fn in {".last_promote", ".last_digest"}:
                try:
                    m = (Path(dirpath) / fn).stat().st_mtime
                except OSError:
                    continue
                if m > newest:
                    newest = m
    return newest


def regenerate(repo_root: Path, auto_stub: bool = False) -> None:
    out_file = repo_root / "ORG.md"
    repo_name = repo_root.name

    # Refresh the adoption plan UNCONDITIONALLY. render_plan() is cheap and
    # idempotent, and its checkboxes track deep-file state (ROLE.md content,
    # lesson tags, .last_promote markers) that the ORG.md staleness gate may
    # not always trip. Running it on every call is what makes plan.py's
    # "Re-runs every SessionStart/Stop" header actually true.
    try:
        from plan import render_plan  # type: ignore[import]
        render_plan(repo_root)
    except Exception as _exc:
        print(f"[sync_org] plan refresh skipped: {_exc}", file=sys.stderr)

    if not needs_regen(repo_root, out_file):
        return

    cfg = load_roles_cfg(repo_root)
    if not cfg:
        print(f"[sync_org] No roles.json found in {repo_root}/.org/ — skipping.", file=sys.stderr)
        return

    excl: set[str] = set(cfg.get("_meta", {}).get("exclude_dirs", []))
    roles: dict = cfg.setdefault("roles", {})
    role_names: set[str] = set(roles.keys())
    on_disk: set[str] = set(list_top_dirs(repo_root, excl))

    # Auto-promote unmapped top-level dirs as stub roles — only when explicitly
    # opted in. OFF by default so a fresh install that defers folder moves does
    # not silently mutate the user-approved curated role set into one bogus
    # TODO-role per still-at-root folder.
    mapped_at_top = {f for r in roles.values() for f in r.get("folders", [])}
    unmapped_top = sorted(on_disk - mapped_at_top - role_names)

    auto_added: list[str] = []
    if unmapped_top and auto_stub:
        for name in unmapped_top:
            roles[name] = {
                "desc": "TODO — auto-added by sync_org. Move into an existing role or define this one.",
                "folders": [],
            }
            auto_added.append(name)
        save_roles_cfg(repo_root, cfg)
        role_names = set(roles.keys())

    # Build present/absent per role
    role_status: dict[str, dict] = {}
    for name, role in roles.items():
        present: list[tuple[str, list[str]]] = []
        absent: list[str] = []
        for f in role.get("folders", []):
            locs = _folder_locations(f, role_names, on_disk, repo_root)
            if locs:
                present.append((f, locs))
            else:
                absent.append(f)
        role_status[name] = {"present": present, "absent": absent}

    total_placed = sum(len(s["present"]) for s in role_status.values())
    total_missing = sum(len(s["absent"]) for s in role_status.values())

    # Re-derive unmapped after auto-add
    mapped = {f for r in roles.values() for f in r.get("folders", [])}
    unmapped = sorted(on_disk - mapped - role_names)

    loose_files = _list_loose_files(
        repo_root, {"ORG.md", "MIGRATION.md", "FIX_LIST.md", "CLAUDE.md"}
    )

    lines: list[str] = []
    lines += [
        f"# {repo_name} Org Chart",
        "",
        f"_Auto-generated {datetime.now().strftime('%Y-%m-%d %H:%M')} by `.org/sync_org.py`. "
        "Edit `.org/roles.json` to change mapping._",
        "",
        f"**{len(roles)} roles · {total_placed} folders placed · {len(unmapped)} unmapped · {total_missing} missing**",
        "",
    ]

    if unmapped:
        lines += ["## Unmapped (on disk, no role assigned)", ""]
        for f in unmapped:
            lines.append(f"- `{f}/` — add to `.org/roles.json`")
        lines.append("")

    if total_missing:
        lines += ["## Missing (in roles.json, not on disk)", ""]
        for name, s in role_status.items():
            for f in s["absent"]:
                lines.append(f"- `{name}/{f}/` (or `{f}/` at root) — not found")
        lines.append("")

    if loose_files:
        lines += ["## Loose files at root (consider moving into a role folder)", ""]
        for f in loose_files:
            lines.append(f"- `{f}`")
        lines.append("")

    lines += ["## Roles", ""]
    for name, role in roles.items():
        s = role_status[name]
        count = len(s["present"])
        lines.append(f"### {name}/  _({count} folder{'s' if count != 1 else ''})_")
        lines.append(role.get("desc", ""))
        lines.append("")
        for f, locs in s["present"]:
            loc_str = ", ".join(f"`{l}/`" for l in locs)
            lines.append(f"- {loc_str}")
        for f in s["absent"]:
            lines.append(f"- ~~`{f}/`~~ (missing)")
        lines.append("")

    out_file.write_text("\n".join(lines), encoding="utf-8")

    msg = (
        f"[sync_org] wrote {out_file.relative_to(repo_root)} — "
        f"{total_placed} placed, {len(unmapped)} unmapped, {total_missing} missing"
    )
    if auto_added:
        msg += f" · auto-added roles: {', '.join(auto_added)}"
    print(msg)


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="Regenerate ORG.md from .org/roles.json + filesystem state."
    )
    ap.add_argument(
        "--auto-stub",
        action="store_true",
        help=(
            "Auto-promote unmapped top-level dirs into TODO stub roles in "
            "roles.json. OFF by default — opt in only when you want every "
            "still-at-root folder stubbed as its own role."
        ),
    )
    args = ap.parse_args()
    repo_root = detect_repo_root()
    regenerate(repo_root, auto_stub=args.auto_stub)
    return 0


if __name__ == "__main__":
    sys.exit(main())
