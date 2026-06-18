#!/usr/bin/env python3
"""orgkit.migrate — scan, move, and fix-up path references in a messy repo.

Four-stage migration
--------------------
1. SCAN: discover all top-level project folders that aren't already inside
   a role directory. Return them for the caller (setup.py / CLI) to assign.

2. FIND REFS: grep the moved folder name across all text files and classify
   every hit as: literal_path (auto-fixable absolute/repo-relative string),
   import (Python/JS/TS import statement — needs model judgment),
   relative_path (../folder or ./folder notation — needs model judgment),
   or other (any remaining hit — needs model judgment).

3. MOVE: given a {folder -> role} mapping, physically move each folder under
   <repo>/<role>/<folder>/ and write MIGRATION.md with the old→new map.

4. FIX REFS: rewrite only the literal_path hits deterministically.
   import, relative_path, and other hits are flagged in MIGRATION.md under
   "References needing manual/AI review" — they are NEVER silently skipped.

Flags
-----
--dry-run   Full preview: moves planned, every reference found, which are
            auto-fixed vs flagged. Nothing is written or moved.
--verbose   Print per-file change counts.
--role-map  JSON string or @file path: {"folder": "role", ...}  (for CLI use)

All path manipulation goes through core.py helpers.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from core import (  # noqa: E402
    detect_repo_root,
    load_roles_cfg,
    save_roles_cfg,
    list_top_dirs,
    derive_abs_prefix,
    derive_repo_prefix,
    walk_rewritable_files,
    _warn,
)

# Files that document the migration itself — skip when rewriting
_SKIP_AT_ROOT = frozenset({"MIGRATION.md", "FIX_LIST.md", "ORG.md", "CLAUDE.md"})


# ---------------------------------------------------------------------------
# Stage 1 — SCAN
# ---------------------------------------------------------------------------

def scan_unmapped(repo_root: Path) -> list[str]:
    """Return top-level folders that are not already role dirs or listed in any role.

    These are candidates for migration into a role sub-directory.
    """
    cfg = load_roles_cfg(repo_root)
    excl: set[str] = set(cfg.get("_meta", {}).get("exclude_dirs", []))
    roles: dict = cfg.get("roles", {})
    role_names: set[str] = set(roles.keys())
    mapped_folders: set[str] = {f for r in roles.values() for f in r.get("folders", [])}
    on_disk: set[str] = set(list_top_dirs(repo_root, excl))
    # Already-inside-a-role dirs are not at root, so they won't show up here.
    unmapped = sorted(on_disk - role_names - mapped_folders)
    return unmapped


# ---------------------------------------------------------------------------
# Stage 2 — FIND REFS (classify all references before moving anything)
# ---------------------------------------------------------------------------

# Reference classification is per-folder (the folder name is escaped into the
# pattern at call time), so it lives entirely inside _classify_line below.


def _classify_line(line: str, folder: str, abs_prefix: str = "", repo_prefix: str = "") -> str:
    """Classify one line that mentions *folder* into a reference category.

    Returns one of: 'literal_path', 'import', 'relative_path', 'other'.

    literal_path  — an absolute /abs/path/folder or repo-relative repo/folder
                    string that WILL actually be rewritten by fix_path_refs.
                    Only classified as literal_path when the line matches the
                    same abs/repo-prefix patterns used by _build_translation_patterns
                    (so lines like `cd /tmp/my-project` are not falsely labeled).
    import        — a Python `from X import` / `import X` or a JS/TS
                    `import … from '…'` / `require('…')` statement.
    relative_path — a dotted relative path (../folder, ./folder) in any file.
    other         — anything that mentions the folder name but doesn't fit the
                    above; must be human/AI-reviewed before deciding.

    Precedence (highest to lowest):
      1. relative_path  — checked first; ../folder patterns are never literal
      2. import         — Python/JS import statements
      3. literal_path   — only if the abs or repo-relative prefix actually matches
      4. other          — fallback
    """
    # 1. Relative path — MUST be checked before the slash-based literal check
    #    because ../folder also contains /folder, which would falsely match literal_path.
    if re.search(
        r"""(?:^|['"\s=:(])\.\.?[/\\]""" + re.escape(folder) + r"""(?=[/'"\s)\n]|$)""",
        line,
    ):
        return "relative_path"

    # 2. Python import
    stripped = line.lstrip()
    if stripped.startswith(("from ", "import ")):
        # Python: `from oldproj.utils import X` or `import oldproj`
        if re.search(
            r"""(?:^|\s)(?:from\s+|import\s+)[\w.]*""" + re.escape(folder) + r"""[\w.]*""",
            stripped,
        ):
            return "import"

    # 2b. JS/TS import or require
    if re.search(
        r"""(?:import|require|from)\s+['"][^'"]*""" + re.escape(folder) + r"""[^'"]*['"]""",
        line,
    ):
        return "import"

    # 3. Literal path — only when the line actually matches the rewrite patterns
    #    (abs prefix or repo-relative prefix) that fix_path_refs will apply.
    #    A bare `/folder` elsewhere (e.g. /tmp/my-project) is NOT rewritable.
    if abs_prefix:
        abs_old = f"{abs_prefix}/{folder}"
        if re.search(re.escape(abs_old) + r"(?=[/\"'\s\\)\n]|$)", line):
            return "literal_path"
    if repo_prefix:
        rel_old = f"{repo_prefix}/{folder}"
        if re.search(r"(?<![A-Za-z0-9_/-])" + re.escape(rel_old) + r"(?=[/\"'\s\\)\n]|$)", line):
            return "literal_path"
    # If no prefixes provided, fall back to the generic slash check (legacy behaviour
    # when called without repo context — still better than nothing).
    if not abs_prefix and not repo_prefix:
        if re.search(r"(?:/|\\)" + re.escape(folder) + r"(?=[/\\\"'\s)\n]|$)", line):
            return "literal_path"

    # 4. Fallback — module-dotted names, config values, comments, etc.
    return "other"


def find_references(repo_root: Path, folder: str) -> list[dict]:
    """Grep every text file in the repo for *folder* and classify each hit.

    Returns a list of dicts:
        {
          "file":     str  — path relative to repo_root,
          "line":     int  — 1-based line number,
          "line_text": str — the raw line (stripped),
          "category": str  — one of: literal_path, import, relative_path, other,
        }

    Files inside *folder* itself are excluded (their paths change anyway).
    The migration-doc skip list (_SKIP_AT_ROOT) is also excluded.
    """
    folder_pat = re.compile(re.escape(folder))
    files = walk_rewritable_files(repo_root)

    abs_prefix = derive_abs_prefix(repo_root)
    repo_prefix = derive_repo_prefix(repo_root)

    hits: list[dict] = []
    for fpath in files:
        # Skip self (the moved folder)
        try:
            rel = fpath.relative_to(repo_root)
        except ValueError:
            continue
        if rel.parts and rel.parts[0] == folder:
            continue
        # Skip migration docs
        if fpath.parent == repo_root and fpath.name in _SKIP_AT_ROOT:
            continue

        try:
            text = fpath.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            continue

        for lineno, raw_line in enumerate(text.splitlines(), start=1):
            if folder_pat.search(raw_line):
                category = _classify_line(raw_line, folder, abs_prefix, repo_prefix)
                hits.append({
                    "file": str(rel),
                    "line": lineno,
                    "line_text": raw_line.rstrip(),
                    "category": category,
                })

    return hits


# ---------------------------------------------------------------------------
# Post-move VERIFY — confirm zero DANGLING (old-form) references remain
# ---------------------------------------------------------------------------

# File types where a bare repo-relative `<folder>/...` token (no abs/repo prefix)
# is most likely an actual runtime path that the move just broke — e.g.
# `python3 apiproj/main.py` in a shell script, `cat apiproj/README.md`, a yaml
# `path: apiproj/conf`, or a Makefile recipe. Scoped here to keep the bare-ref
# check conservative: prose (.md/.txt) is excluded because bare folder mentions
# there are usually narrative, not executable paths.
_BARE_REF_EXTS = frozenset({
    ".sh", ".bash", ".zsh", ".ksh",
    ".yml", ".yaml",
    ".py",
    ".mk",
})
_BARE_REF_FILENAMES = frozenset({"Makefile", "makefile", "GNUmakefile", "Dockerfile"})


def _is_bare_ref_context(fpath: Path) -> bool:
    """True if *fpath* is a shell/Makefile/yaml/py file where a bare
    `<folder>/...` token is likely an executable path broken by the move."""
    if fpath.name in _BARE_REF_FILENAMES:
        return True
    return fpath.suffix.lower() in _BARE_REF_EXTS


def verify_migration(repo_root: Path, role_map: dict[str, str]) -> list[dict]:
    """Find references to the OLD (pre-move) path form of each migrated folder.

    Run AFTER a move to confirm nothing still points at the original location.
    Unlike find_references (which matches the bare folder name and therefore
    false-positives on the new `role/folder/…` location and on the folder's own
    internal files), this only flags genuine *dangling* hits:

      - absolute   `/abs/repo/<folder>`   NOT followed by the role segment
      - repo-rel   `<reponame>/<folder>`  NOT followed by the role segment
      - bare       `<folder>/…`           at the start of a path token, in
                   shell/Makefile/yaml/py files only (see _is_bare_ref_context)

    The first two carry the abs/repo prefix and are what fix_path_refs rewrites.
    The bare check catches the very common pattern of a repo-rooted relative
    path with no prefix — e.g. `python3 apiproj/main.py` in run.sh — which the
    prefixed patterns miss entirely and which the move silently breaks. To avoid
    false positives the bare check is restricted to executable/config file types
    and requires the `<folder>/` token to begin a path segment (not preceded by
    a path-segment char, so the correctly-migrated `<role>/<folder>` form is not
    flagged).

    A correctly-rewritten reference reads `<reponame>/<role>/<folder>` (or the
    abs equivalent); the negative-lookahead on the role segment means those are
    NOT reported. Files now living under `<role>/<folder>/` are skipped because
    their own relative `<folder>/…` mentions are not old-form path strings and
    would never carry the repo/abs prefix.

    Returns a list of dicts: {file, line, line_text, folder, category}.
    """
    abs_prefix = derive_abs_prefix(repo_root)     # e.g. /Users/alice/projects/myrepo
    repo_prefix = derive_repo_prefix(repo_root)   # e.g. myrepo
    files = walk_rewritable_files(repo_root)

    # Build per-folder dangling patterns: old prefix + folder, but NOT already
    # rewritten to role/folder. The negative lookahead "(?!/<role>(?:[/'\"\s]|$))"
    # ensures the new (correct) form `prefix/role/folder` is never flagged.
    # Each check carries a `bare` flag: bare-ref checks only apply to the
    # shell/Makefile/yaml/py file contexts gated by _is_bare_ref_context.
    checks: list[tuple[re.Pattern, str, str, bool]] = []
    for folder, role in sorted(role_map.items()):
        if abs_prefix:
            # Old absolute form `/abs/repo/<folder>` where the segment directly
            # after the repo prefix is the folder itself (NOT the role dir).
            # The negative lookahead skips the correctly-rewritten
            # `/abs/repo/<role>/<folder>` form.
            abs_old_strict = re.compile(
                r"(?<![A-Za-z0-9_/-])"
                + re.escape(abs_prefix) + r"/"
                + r"(?!" + re.escape(role) + r"/)"   # next seg is not the role
                + re.escape(folder)
                + r"(?=[/\"'\s\\)\n]|$)"
            )
            checks.append((abs_old_strict, folder, f"dangling_abs:{folder}", False))
        if repo_prefix:
            rel_old_strict = re.compile(
                r"(?<![A-Za-z0-9_/-])"
                + re.escape(repo_prefix) + r"/"
                + r"(?!" + re.escape(role) + r"/)"   # next seg is not the role
                + re.escape(folder)
                + r"(?=[/\"'\s\\)\n]|$)"
            )
            checks.append((rel_old_strict, folder, f"dangling_rel:{folder}", False))

        # Bare repo-relative `<folder>/` at the start of a path token. The
        # left look-behind forbids any path-segment char (so it cannot be the
        # tail of `<role>/<folder>` or `other/<folder>`); a trailing `/` is
        # required so the match is a directory prefix, not a bare word. Applied
        # only in _is_bare_ref_context files below.
        bare_old = re.compile(
            r"(?<![A-Za-z0-9_./\\-])"
            + re.escape(folder)
            + r"/"
        )
        checks.append((bare_old, folder, f"dangling_bare:{folder}", True))

    dangling: list[dict] = []
    for fpath in files:
        try:
            rel = fpath.relative_to(repo_root)
        except ValueError:
            continue
        # Skip the migration docs at root
        if fpath.parent == repo_root and fpath.name in _SKIP_AT_ROOT:
            continue
        # Skip files now living INSIDE any moved folder's new home: their own
        # internal mentions are not old-form path strings and are never dangling.
        skip = False
        for folder, role in role_map.items():
            if len(rel.parts) >= 2 and rel.parts[0] == role and rel.parts[1] == folder:
                skip = True
                break
        if skip:
            continue

        bare_ok = _is_bare_ref_context(fpath)

        try:
            text = fpath.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            continue

        for lineno, raw_line in enumerate(text.splitlines(), start=1):
            for pat, folder, label, is_bare in checks:
                if is_bare and not bare_ok:
                    continue
                if pat.search(raw_line):
                    dangling.append({
                        "file": str(rel),
                        "line": lineno,
                        "line_text": raw_line.rstrip(),
                        "folder": folder,
                        "category": label,
                    })
                    break  # one hit per line is enough

    return dangling


# ---------------------------------------------------------------------------
# Stage 3 — MOVE
# ---------------------------------------------------------------------------

def move_folders(
    repo_root: Path,
    role_map: dict[str, str],   # {folder_name: role_name}
    dry_run: bool = False,
) -> list[dict]:
    """Move each folder into its assigned role directory.

    - Creates the role directory if it doesn't exist.
    - Updates roles.json to record the folder under the role.
    - Returns a list of move records: {src, dst, role, folder}.
    - Never deletes anything.
    """
    cfg = load_roles_cfg(repo_root)
    if not cfg:
        cfg = {"_meta": {}, "roles": {}}
    roles: dict = cfg.setdefault("roles", {})

    records: list[dict] = []

    for folder, role in sorted(role_map.items()):
        src = repo_root / folder
        if not src.exists():
            _warn(f"[migrate] source not found, skipping: {src}")
            continue

        dst_dir = repo_root / role
        dst = dst_dir / folder

        if dst.exists():
            _warn(f"[migrate] destination already exists, skipping: {dst}")
            records.append({"src": str(src), "dst": str(dst), "role": role, "folder": folder, "status": "skipped"})
            continue

        record = {"src": str(src), "dst": str(dst), "role": role, "folder": folder, "status": "dry-run" if dry_run else "moved"}

        if not dry_run:
            dst_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))

            # Update roles.json
            if role not in roles:
                roles[role] = {"desc": "", "folders": []}
            role_folders: list[str] = roles[role].setdefault("folders", [])
            if folder not in role_folders:
                role_folders.append(folder)
                role_folders.sort()

        records.append(record)
        action = "would move" if dry_run else "moved"
        print(f"[migrate] {action}: {src.relative_to(repo_root)}  →  {dst.relative_to(repo_root)}")

    if not dry_run and records:
        save_roles_cfg(repo_root, cfg)

    return records


# ---------------------------------------------------------------------------
# Stage 3 — FIX REFS
# ---------------------------------------------------------------------------

def _build_translation_patterns(
    repo_root: Path,
    move_records: list[dict],
) -> list[tuple[re.Pattern, str, str]]:
    """Build (compiled_pattern, replacement, label) for each moved folder.

    Two pattern variants per folder:
      abs  — /absolute/path/to/repo/<old_folder>
      rel  — reponame/<old_folder>

    Both variants match a trailing slash, quote, space, newline, or end-of-string
    so we never mangle partial names (e.g. 'data' won't eat 'database').

    Sorted longest-old-name-first to avoid shorter patterns eating longer ones.
    """
    abs_prefix = derive_abs_prefix(repo_root)     # e.g. /Users/alice/projects/myrepo
    repo_prefix = derive_repo_prefix(repo_root)   # e.g. myrepo

    pats: list[tuple[re.Pattern, str, str]] = []

    sorted_records = sorted(move_records, key=lambda r: -len(r["folder"]))

    for rec in sorted_records:
        if rec.get("status") not in ("moved", "dry-run"):
            continue  # only rewrite for actually-moved (or would-move) folders
        folder = rec["folder"]
        role = rec["role"]
        new_rel = f"{role}/{folder}"

        # Absolute pattern
        abs_old = f"{abs_prefix}/{folder}"
        abs_new = f"{abs_prefix}/{new_rel}"
        abs_re = re.compile(re.escape(abs_old) + r"(?=[/\"'\s\\)\n]|$)")
        pats.append((abs_re, abs_new, f"abs:{folder}"))

        # Repo-relative pattern — word-boundary on left to avoid partial matches
        rel_old = f"{repo_prefix}/{folder}"
        rel_new = f"{repo_prefix}/{new_rel}"
        rel_re = re.compile(
            r"(?<![A-Za-z0-9_/-])" + re.escape(rel_old) + r"(?=[/\"'\s\\)\n]|$)"
        )
        pats.append((rel_re, rel_new, f"rel:{folder}"))

    return pats


def fix_path_refs(
    repo_root: Path,
    move_records: list[dict],
    all_refs: list[dict] | None = None,
    dry_run: bool = False,
    verbose: bool = False,
) -> dict:
    """Rewrite only literal-path references across all eligible files.

    import, relative_path, and other references (collected by find_references)
    are NOT touched — they are returned in the 'needs_review' list so the
    caller (slash command or human) can handle them with judgment.

    Returns a summary dict:
        {
          files_changed:      int,
          total_replacements: int,
          by_pattern:         {label: count},
          needs_review:       list[dict],   # hits that need human/AI review
          auto_fixed:         list[dict],   # literal_path hits that were fixed
        }
    """
    patterns = _build_translation_patterns(repo_root, move_records)
    if not patterns:
        needs_review: list[dict] = []
        auto_fixed: list[dict] = []
        if all_refs:
            for hit in all_refs:
                if hit["category"] == "literal_path":
                    auto_fixed.append(hit)
                else:
                    needs_review.append(hit)
        return {
            "files_changed": 0,
            "total_replacements": 0,
            "by_pattern": {},
            "needs_review": needs_review,
            "auto_fixed": auto_fixed,
        }

    # Separate pre-classified hits if provided
    needs_review = []
    auto_fixed = []
    if all_refs:
        for hit in all_refs:
            if hit["category"] == "literal_path":
                auto_fixed.append(hit)
            else:
                needs_review.append(hit)

    files = walk_rewritable_files(repo_root)

    files_changed = 0
    total_replacements = 0
    by_pattern: dict[str, int] = {}
    changed_files: list[dict] = []

    for fpath in files:
        # Skip migration docs at repo root
        if fpath.parent == repo_root and fpath.name in _SKIP_AT_ROOT:
            continue

        try:
            text = fpath.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            continue

        new_text = text
        file_changes = 0
        for pat, repl, label in patterns:
            new_text, n = pat.subn(repl, new_text)
            if n:
                by_pattern[label] = by_pattern.get(label, 0) + n
                file_changes += n

        if file_changes > 0:
            files_changed += 1
            total_replacements += file_changes
            rel = str(fpath.relative_to(repo_root))
            changed_files.append({"file": rel, "changes": file_changes})
            if not dry_run:
                fpath.write_text(new_text, encoding="utf-8")
            if verbose:
                mode_tag = "[dry-run]" if dry_run else "[rewrote]"
                print(f"  {mode_tag} {file_changes:4d}  {rel}")

    mode = "DRY RUN" if dry_run else "APPLIED"
    print(f"\n[migrate:fix-refs] [{mode}] {total_replacements} replacements across {files_changed} files")
    if needs_review:
        print(f"[migrate:fix-refs] ⚠  {len(needs_review)} reference(s) flagged for manual/AI review (see MIGRATION.md)")

    return {
        "files_changed": files_changed,
        "total_replacements": total_replacements,
        "by_pattern": by_pattern,
        "needs_review": needs_review,
        "auto_fixed": auto_fixed,
    }


# ---------------------------------------------------------------------------
# MIGRATION.md writer
# ---------------------------------------------------------------------------

def write_migration_md(repo_root: Path, records: list[dict], fix_summary: dict) -> None:
    lines = [
        "# Migration log",
        "",
        f"_Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} by `orgkit/migrate.py`._",
        "",
        "## Folder moves",
        "",
        "| Old path | New path | Status |",
        "| --- | --- | --- |",
    ]
    for rec in records:
        old = Path(rec["src"]).relative_to(repo_root) if Path(rec["src"]).is_relative_to(repo_root) else rec["src"]
        new = Path(rec["dst"]).relative_to(repo_root) if Path(rec["dst"]).is_relative_to(repo_root) else rec["dst"]
        lines.append(f"| `{old}` | `{new}` | {rec['status']} |")

    lines += [
        "",
        "## Auto-fixed path references (literal strings)",
        "",
        f"- Files changed: {fix_summary.get('files_changed', 0)}",
        f"- Total replacements: {fix_summary.get('total_replacements', 0)}",
        "",
        "### By pattern",
        "",
    ]
    for k, v in sorted(fix_summary.get("by_pattern", {}).items(), key=lambda x: -x[1]):
        lines.append(f"- `{k}`: {v}")

    # Needs-review section — always emit, even if empty, so readers know the tool checked
    needs_review: list[dict] = fix_summary.get("needs_review", [])
    lines += [
        "",
        "## ⚠ References needing manual/AI review",
        "",
    ]
    if needs_review:
        lines += [
            "These hits mention a moved folder but were **not** auto-fixed because they",
            "contain import statements, relative paths (`../folder`), or other references",
            "that require judgment to rewrite correctly. Use `/orgkit-migrate` (the slash",
            "command) to let the session model propose and apply the correct edits.",
            "",
            "| File | Line | Category | Line text |",
            "| --- | --- | --- | --- |",
        ]
        for hit in needs_review:
            # Escape pipe characters in line_text to avoid breaking the table
            text_safe = hit["line_text"].replace("|", "\\|").strip()
            lines.append(
                f"| `{hit['file']}` | {hit['line']} | `{hit['category']}` | `{text_safe}` |"
            )
    else:
        lines.append(
            "_No import / relative-path / other references found — "
            "all references were auto-fixable literal strings or none exist._"
        )

    out = repo_root / "MIGRATION.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[migrate] wrote {out.relative_to(repo_root)}")


# ---------------------------------------------------------------------------
# Rollback — reverse a migration recorded in MIGRATION.md
# ---------------------------------------------------------------------------

def _parse_migration_md(repo_root: Path) -> list[dict]:
    """Parse MIGRATION.md and return a list of move records (old, new, status).

    Returns [] if MIGRATION.md doesn't exist or can't be parsed.
    """
    mig_path = repo_root / "MIGRATION.md"
    if not mig_path.exists():
        return []

    records = []
    in_table = False
    for line in mig_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "| Old path |" in line:
            in_table = True
            continue
        if in_table and line.startswith("|"):
            parts = [p.strip().strip("`") for p in line.strip("|").split("|")]
            if len(parts) >= 3 and parts[0] and parts[0] != "---":
                old_rel = parts[0]
                new_rel = parts[1]
                status  = parts[2]
                records.append({
                    "old_rel": old_rel,
                    "new_rel": new_rel,
                    "status": status.strip(),
                })
        elif in_table and not line.startswith("|"):
            in_table = False

    return records


def rollback(
    repo_root: Path,
    dry_run: bool = False,
) -> int:
    """Reverse the latest migration recorded in MIGRATION.md.

    - Moves each folder from new_path back to old_path.
    - Reverses path-ref rewrites (new→old paths in text files).
    - Reverts roles.json folder assignments.
    - Backs up MIGRATION.md to MIGRATION.md.bak.
    - Writes ROLLBACK.md log.
    - Never deletes content.

    Returns 0 on success, 1 on error.
    """
    mig_path = repo_root / "MIGRATION.md"
    if not mig_path.exists():
        print("[rollback] MIGRATION.md not found — nothing to roll back.", file=sys.stderr)
        return 1

    records = _parse_migration_md(repo_root)
    if not records:
        print("[rollback] MIGRATION.md found but no move records parsed.", file=sys.stderr)
        return 1

    mode_tag = "[dry-run]" if dry_run else "[rollback]"
    print(f"\n{mode_tag} Rolling back {len(records)} folder move(s) from MIGRATION.md")

    # Build reverse move records
    cfg = load_roles_cfg(repo_root)
    if not cfg:
        cfg = {"_meta": {}, "roles": {}}
    roles: dict = cfg.setdefault("roles", {})

    reverse_records: list[dict] = []
    errors: list[str] = []

    for rec in records:
        status = rec["status"].strip()
        if status not in ("moved",):
            print(f"  {mode_tag} skip (status={status!r}): {rec['new_rel']} → {rec['old_rel']}")
            continue

        src_path = repo_root / rec["new_rel"]   # current (post-migration) location
        dst_path = repo_root / rec["old_rel"]   # where it should go back to

        if not src_path.exists():
            msg = f"source {rec['new_rel']} not found (may have been moved or deleted)"
            print(f"  {mode_tag} WARN: {msg}")
            errors.append(msg)
            continue

        if dst_path.exists():
            msg = f"destination {rec['old_rel']} already exists — skipping to avoid data loss"
            print(f"  {mode_tag} WARN: {msg}")
            errors.append(msg)
            continue

        action = "would move" if dry_run else "moving"
        print(f"  {mode_tag} {action}: {rec['new_rel']}  →  {rec['old_rel']}")

        if not dry_run:
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src_path), str(dst_path))

        # Build reverse move record for fix_path_refs
        folder_name = Path(rec["old_rel"]).name
        role_name = Path(rec["new_rel"]).parts[0] if len(Path(rec["new_rel"]).parts) > 1 else ""

        reverse_records.append({
            "src": str(repo_root / rec["new_rel"]),   # old "new" path (was destination)
            "dst": str(repo_root / rec["old_rel"]),   # reverting to old path
            "role": role_name,
            "folder": folder_name,
            "status": "dry-run" if dry_run else "moved",
        })

        # Revert roles.json: remove folder from the role it was migrated INTO
        if role_name and role_name in roles:
            role_folders = roles[role_name].get("folders", [])
            if folder_name in role_folders:
                role_folders.remove(folder_name)

    # Reverse path-ref rewrites: swap old/new in the translation patterns
    # We re-use fix_path_refs but with a synthetic "reverse" record set where
    # src=new location and dst=old location (that's what fix_path_refs reads)
    if reverse_records:
        print(f"\n{mode_tag} Reversing path references in text files...")
        # fix_path_refs rewrites src→dst; here src is the post-migration path
        # and dst is the original path — exactly what we want for reversal
        _fix_path_refs_reverse(repo_root, reverse_records, dry_run=dry_run)

        if not dry_run:
            save_roles_cfg(repo_root, cfg)
            print(f"{mode_tag} roles.json updated")

    # Backup MIGRATION.md and write ROLLBACK.md
    if not dry_run:
        bak = repo_root / "MIGRATION.md.bak"
        shutil.copy2(mig_path, bak)
        print(f"\n{mode_tag} MIGRATION.md backed up to MIGRATION.md.bak")

        rollback_lines = [
            "# Rollback log",
            "",
            f"_Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} by `orgkit/migrate.py rollback`._",
            "",
            "## Folders restored",
            "",
            "| New path (rolled back from) | Old path (restored to) | Result |",
            "| --- | --- | --- |",
        ]
        for rec in reverse_records:
            new_rel = rec["src"].replace(str(repo_root) + "/", "")
            old_rel = rec["dst"].replace(str(repo_root) + "/", "")
            result = rec["status"]
            rollback_lines.append(f"| `{new_rel}` | `{old_rel}` | {result} |")

        if errors:
            rollback_lines += ["", "## Warnings", ""]
            for e in errors:
                rollback_lines.append(f"- {e}")

        (repo_root / "ROLLBACK.md").write_text("\n".join(rollback_lines) + "\n", encoding="utf-8")
        print(f"{mode_tag} wrote ROLLBACK.md")

    n = len(reverse_records)
    n_err = len(errors)
    print(f"\n{mode_tag} rollback complete: {n} folder(s) restored, {n_err} warning(s)")
    return 0


def _fix_path_refs_reverse(
    repo_root: Path,
    reverse_records: list[dict],
    dry_run: bool = False,
) -> dict:
    """Reverse-rewrite path refs: swap src/dst compared to normal fix_path_refs.

    In rollback, the "moved" folders' NEW locations are in rec["src"],
    and the OLD (pre-migration) locations are in rec["dst"].
    We want to rewrite NEW→OLD in all files.

    We construct synthetic records where src=dst and dst=src so that
    _build_translation_patterns produces the reverse substitution.
    """
    # Build reverse patterns directly from reverse_records (new→old direction).
    # Each record's "src" is the post-migration location; "dst" is the original.
    # We want to rewrite src→dst in text files (i.e. migrated path → original path).
    abs_prefix = derive_abs_prefix(repo_root)
    repo_prefix = derive_repo_prefix(repo_root)

    pats: list[tuple[re.Pattern, str, str]] = []
    sorted_rev = sorted(reverse_records, key=lambda r: -len(r["folder"]))
    for rec in sorted_rev:
        if rec.get("status") not in ("moved", "dry-run"):
            continue
        folder = rec["folder"]
        role = rec["role"]
        new_rel = f"{role}/{folder}" if role else folder
        old_rel = folder

        # We want to rewrite new_rel → old_rel (the reverse of what forward migration did)
        abs_new = f"{abs_prefix}/{new_rel}"
        abs_old = f"{abs_prefix}/{old_rel}"
        abs_re = re.compile(re.escape(abs_new) + r"(?=[/\"'\s\\)\n]|$)")
        pats.append((abs_re, abs_old, f"abs:reverse:{folder}"))

        rel_new = f"{repo_prefix}/{new_rel}"
        rel_old = f"{repo_prefix}/{old_rel}"
        rel_re = re.compile(
            r"(?<![A-Za-z0-9_/-])" + re.escape(rel_new) + r"(?=[/\"'\s\\)\n]|$)"
        )
        pats.append((rel_re, rel_old, f"rel:reverse:{folder}"))

    if not pats:
        return {"files_changed": 0, "total_replacements": 0, "by_pattern": {}}

    files = walk_rewritable_files(repo_root)
    files_changed = 0
    total_replacements = 0
    by_pattern: dict[str, int] = {}

    for fpath in files:
        if fpath.parent == repo_root and fpath.name in _SKIP_AT_ROOT:
            continue
        try:
            text = fpath.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            continue
        new_text = text
        file_changes = 0
        for pat, repl, label in pats:
            new_text, n = pat.subn(repl, new_text)
            if n:
                by_pattern[label] = by_pattern.get(label, 0) + n
                file_changes += n
        if file_changes > 0:
            files_changed += 1
            total_replacements += file_changes
            if not dry_run:
                fpath.write_text(new_text, encoding="utf-8")

    mode = "DRY RUN" if dry_run else "APPLIED"
    print(f"[rollback:fix-refs] [{mode}] {total_replacements} reversal(s) across {files_changed} files")
    return {"files_changed": files_changed, "total_replacements": total_replacements, "by_pattern": by_pattern}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="orgkit migrate — scan, move, fix refs")
    ap.add_argument("--target", default=None, help="Target repo root (default: auto-detect)")
    ap.add_argument("--scan", action="store_true", help="Scan and print unmapped folders")
    ap.add_argument(
        "--verify",
        action="store_true",
        help="Post-move check: report DANGLING refs for --role-map folders — prefixed old-form "
             "(abs/reponame) refs everywhere, plus bare `<folder>/...` refs in shell/Makefile/yaml/py "
             "files. Does NOT catch bare refs in other file types, so a zero result is necessary but "
             "not sufficient. Read-only; does not move or rewrite. Exits 0 if zero dangling refs.",
    )
    ap.add_argument("--rollback", action="store_true", help="Reverse the latest migration recorded in MIGRATION.md")
    ap.add_argument(
        "--role-map",
        default=None,
        help='JSON {folder:role} or @filepath. E.g. \'{"myapp":"dev","analysis":"data-science"}\'',
    )
    ap.add_argument("--dry-run", action="store_true", help="Show what would happen, don't write")
    ap.add_argument("--verbose", action="store_true", help="Print per-file details")
    args = ap.parse_args()

    repo_root = Path(args.target).resolve() if args.target else detect_repo_root()

    if args.rollback:
        return rollback(repo_root, dry_run=args.dry_run)

    if args.scan:
        unmapped = scan_unmapped(repo_root)
        if unmapped:
            print("Unmapped top-level folders:")
            for f in unmapped:
                print(f"  {f}")
        else:
            print("No unmapped folders found.")
        return 0

    if not args.role_map:
        ap.print_help()
        return 1

    # Parse role-map
    raw_map = args.role_map
    if raw_map.startswith("@"):
        raw_map = Path(raw_map[1:]).read_text(encoding="utf-8")
    try:
        role_map: dict[str, str] = json.loads(raw_map)
    except json.JSONDecodeError as exc:
        print(f"error: --role-map is not valid JSON: {exc}", file=sys.stderr)
        return 1

    # --verify: post-move read-only check for dangling (old-form) references.
    if args.verify:
        print(f"[migrate:verify] target repo: {repo_root}")
        print(f"[migrate:verify] checking {len(role_map)} migrated folder(s) for old-form references")
        dangling = verify_migration(repo_root, role_map)
        if not dangling:
            print("\n[migrate:verify] OK — zero prefixed old-form (abs/reponame) refs, and zero")
            print("[migrate:verify] bare `<folder>/...` refs in shell/Makefile/yaml/py files.")
            print("[migrate:verify] NOTE: this is necessary but NOT sufficient. --verify does NOT")
            print("[migrate:verify] catch bare `<folder>/...` refs in other file types (e.g. .md,")
            print("[migrate:verify] .txt, .json) or non-path mentions. A zero result does not prove")
            print("[migrate:verify] the migration is clean — review MIGRATION.md needs_review too.")
            return 0
        print(f"\n[migrate:verify] ⚠  {len(dangling)} dangling reference(s) still point at the old location:")
        for hit in dangling:
            print(f"    {hit['file']}:{hit['line']}  [{hit['category']}]  {hit['line_text'][:120]}")
        print("\n[migrate:verify] FAILED — these must be fixed before the migration is clean.")
        return 1

    print(f"[migrate] target repo: {repo_root}")
    print(f"[migrate] moves planned: {len(role_map)}")

    # Stage 2: collect all references BEFORE moving (uses folder names, not paths)
    all_refs: list[dict] = []
    for folder in role_map:
        refs = find_references(repo_root, folder)
        all_refs.extend(refs)

    # Dry-run: emit FULL preview — moves + all references, categorised
    if args.dry_run:
        print("\n" + "=" * 70)
        print("DRY RUN — nothing will be moved or written")
        print("=" * 70)

        print("\n--- MOVES PLANNED ---")
        for folder, role in sorted(role_map.items()):
            src = repo_root / folder
            status = "would move" if src.exists() else "SOURCE MISSING"
            print(f"  {status}: {folder}/  →  {role}/{folder}/")

        print("\n--- REFERENCES FOUND ---")
        if all_refs:
            by_category: dict[str, list[dict]] = {}
            for hit in all_refs:
                by_category.setdefault(hit["category"], []).append(hit)

            for cat in ("literal_path", "import", "relative_path", "other"):
                hits_in_cat = by_category.get(cat, [])
                if not hits_in_cat:
                    continue
                label = {
                    "literal_path":  "AUTO-FIX  (literal path strings — will be rewritten)",
                    "import":        "REVIEW    (import statement — needs model/human judgment)",
                    "relative_path": "REVIEW    (relative path ../folder — needs model/human judgment)",
                    "other":         "REVIEW    (other mention — needs model/human judgment)",
                }[cat]
                print(f"\n  [{label}]")
                for hit in hits_in_cat:
                    print(f"    {hit['file']}:{hit['line']}  {hit['line_text'][:120]}")
        else:
            print("  (no references to moved folders found in text files)")

        # Tally
        n_auto = sum(1 for h in all_refs if h["category"] == "literal_path")
        n_review = len(all_refs) - n_auto
        print(f"\n--- SUMMARY ---")
        print(f"  Folders to move:          {len(role_map)}")
        print(f"  Total references found:   {len(all_refs)}")
        print(f"  Will be auto-fixed:       {n_auto}  (literal path strings)")
        print(f"  Flagged for review:       {n_review}  (imports / relative paths / other)")
        if n_review:
            print(f"\n  ⚠  {n_review} reference(s) require manual or AI-assisted review.")
            print("     Run /orgkit-migrate (slash command) to apply them interactively.")
        print("\n" + "=" * 70)
        print("DRY RUN COMPLETE — re-run without --dry-run to apply moves + auto-fixes")
        print("=" * 70 + "\n")
        return 0

    # Stage 3: move folders
    records = move_folders(repo_root, role_map, dry_run=False)

    # Stage 4: fix literal-path references only; returns needs_review separately
    fix_summary = fix_path_refs(
        repo_root, records, all_refs=all_refs, dry_run=False, verbose=args.verbose
    )

    write_migration_md(repo_root, records, fix_summary)

    return 0


if __name__ == "__main__":
    sys.exit(main())
