#!/usr/bin/env python3
"""orgkit.analyze — read-only repo teaser / viral hook.

Scans a target repo, proposes a role grouping based on transparent heuristics,
prints a proposed org-chart tree, and estimates token savings.

CHANGES NOTHING.  Safe to run on any repo.

Usage:
  python3 setup.py --analyze [--target PATH]
  python3 .org/analyze.py [--target PATH]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from core import detect_repo_root, load_roles_cfg  # noqa: E402

# ---------------------------------------------------------------------------
# Skip lists (read-only; shared with core.EXCLUDE_DIRS but we define locally
# so analyze.py can run standalone before engine is installed)
# ---------------------------------------------------------------------------
_SKIP_DIRS: frozenset[str] = frozenset({
    ".git", ".org", "node_modules", "venv", ".venv", "__pycache__",
    ".claude", ".bun", ".cache", "dist", "build", "out", ".next",
    "target",   # Rust/Java
})

# ---------------------------------------------------------------------------
# Heuristic keyword → role mapping
# Checked in order; first match wins.  Folder name is lowercased before check.
# ---------------------------------------------------------------------------
_KEYWORD_RULES: list[tuple[frozenset[str], str]] = [
    # Engineering / product
    (frozenset({"web", "app", "api", "site", "service", "backend",
                "frontend", "server", "ui", "bot", "sdk", "lib",
                "client", "gateway", "infra", "deploy", "devops",
                "platform", "mobile", "ios", "android"}), "engineering"),
    # Data science / ML
    (frozenset({"model", "ml", "data", "notebook", "analysis", "analytics",
                "experiment", "feature", "pipeline", "etl", "stats",
                "ai", "llm", "rag", "train", "predict", "score"}), "data-science"),
    # Design
    (frozenset({"design", "deck", "slide", "brand", "ux", "ui",
                "figma", "creative", "asset", "mockup", "prototype",
                "style", "theme", "visual"}), "design"),
    # Marketing / growth
    (frozenset({"marketing", "growth", "seo", "content", "blog",
                "campaign", "crm", "email", "social", "ads",
                "copy", "landing", "funnel"}), "marketing"),
    # Research
    (frozenset({"research", "survey", "report", "study", "insight",
                "literature", "review", "analysis", "audit"}), "research"),
    # Strategy / business
    (frozenset({"strategy", "biz", "business", "finance", "legal",
                "pitch", "investor", "revenue", "ops", "operations",
                "market", "partner", "sales", "bd", "corp"}), "strategy"),
    # Admin / HR / org
    (frozenset({"admin", "hr", "people", "hiring", "recruit",
                "onboard", "process", "policy", "compliance"}), "admin"),
]


# ---------------------------------------------------------------------------
# Heuristic classifier
# ---------------------------------------------------------------------------

def _classify_folder(name: str, all_names: list[str]) -> str:
    """Return a suggested role name for a single folder.

    Heuristic order:
    1. Shared name-prefix with >=2 other folders  → prefix becomes role.
    2. Keyword match (case-insensitive substring check in _KEYWORD_RULES).
    3. Fallback: "misc".
    """
    lower = name.lower()

    # Keyword match (most reliable; check before prefix clustering)
    for keywords, role in _KEYWORD_RULES:
        for kw in keywords:
            if kw in lower:
                return role

    # Prefix cluster: find longest common prefix shared with >=1 other folder
    peers = [n for n in all_names if n != name]
    best_prefix = ""
    for peer in peers:
        # Find common prefix length
        common = os.path.commonprefix([lower, peer.lower()])
        # Only meaningful if prefix >= 3 chars and doesn't consume the full name
        if len(common) >= 3 and len(common) < len(lower):
            if len(common) > len(best_prefix):
                best_prefix = common
    if best_prefix:
        return best_prefix.rstrip("-_").strip()

    return "misc"


def _propose_grouping(folders: list[str]) -> dict[str, list[str]]:
    """Return {role: [folder, ...]} grouping via heuristics."""
    groups: dict[str, list[str]] = {}
    for f in sorted(folders):
        role = _classify_folder(f, folders)
        groups.setdefault(role, []).append(f)
    return groups


# ---------------------------------------------------------------------------
# Token-savings estimator
# ---------------------------------------------------------------------------

_CHARS_PER_TOKEN = 4.0

# Rough size of a global CLAUDE.md
_EST_GLOBAL_MD_BYTES = 2_000
# Minimum role memory estimate when we have nothing to measure
_EST_ROLE_MD_BYTES = 800


def _count_files_in_dir(d: Path, max_depth: int = 3) -> int:
    """Count non-hidden files up to max_depth levels deep."""
    count = 0
    try:
        for root, dirs, files in os.walk(d):
            # Prune hidden + skip dirs
            dirs[:] = [
                x for x in dirs
                if not x.startswith(".") and x not in _SKIP_DIRS
            ]
            rel = Path(root).relative_to(d)
            if len(rel.parts) >= max_depth:
                dirs.clear()
            count += len([f for f in files if not f.startswith(".")])
    except PermissionError:
        pass
    return count


def _measure_role_md(repo_root: Path, role: str) -> int:
    """Return byte size of ROLE.md for role if it exists, else 0."""
    p = repo_root / role / "memory" / "ROLE.md"
    try:
        return p.stat().st_size if p.exists() else 0
    except OSError:
        return 0


def _estimate_token_savings(
    repo_root: Path,
    groups: dict[str, list[str]],
) -> dict[str, Any]:
    """Estimate tokens/session for dump-all vs scoped access.

    Returns a dict with keys:
      dump_tok    — estimated tokens if everything is dumped each session
      scoped_tok  — tokens for global context + one average role
      pct_saved   — integer percentage reduction
      pct_label   — human-readable label: e.g. "47%" or "~83% est. (scales with role count)"
      notes       — list of human-readable explanation strings
      has_real_content — True when actual ROLE.md files contributed to the estimate
    """
    # Global CLAUDE.md size
    global_md = repo_root / "CLAUDE.md"
    if global_md.exists():
        global_bytes = global_md.stat().st_size
    else:
        global_bytes = _EST_GLOBAL_MD_BYTES

    # Per-role memory sizes: prefer measured ROLE.md; estimate when absent
    role_sizes: dict[str, int] = {}
    roles_with_real_content: int = 0
    for role in groups:
        measured = _measure_role_md(repo_root, role)
        if measured:
            role_sizes[role] = measured
            roles_with_real_content += 1
        else:
            # Estimate from file count in that role's folders
            total_files = 0
            for f in groups[role]:
                d = repo_root / f
                if d.is_dir():
                    total_files += _count_files_in_dir(d)
            role_sizes[role] = max(_EST_ROLE_MD_BYTES, total_files * 40)  # ~40 bytes of context per file

    total_role_bytes = sum(role_sizes.values())
    avg_role_bytes = total_role_bytes // max(len(role_sizes), 1)

    dump_bytes = global_bytes + total_role_bytes
    scoped_bytes = global_bytes + avg_role_bytes

    dump_tok = max(1, round(dump_bytes / _CHARS_PER_TOKEN))
    scoped_tok = max(1, round(scoped_bytes / _CHARS_PER_TOKEN))
    pct_saved = round(100 * (1 - scoped_tok / dump_tok)) if dump_tok else 0

    # Build an honest label.
    # When no actual ROLE.md files exist, the formula is driven purely by
    # role count (reduces to 1 - 1/N) — show it as an estimate, not a fact.
    has_real_content = roles_with_real_content > 0
    if has_real_content:
        pct_label = f"{pct_saved}% smaller"
        basis_note = f"({roles_with_real_content} of {len(role_sizes)} roles have measured ROLE.md)"
    else:
        pct_label = f"~{pct_saved}% smaller (estimate, scales with role count)"
        basis_note = "(no ROLE.md files yet; estimate uses file-count heuristics + defaults)"

    notes = [
        f"Global CLAUDE.md: ~{global_bytes / _CHARS_PER_TOKEN:.0f} tok",
        f"Role memories: {len(role_sizes)} roles, avg ~{avg_role_bytes / _CHARS_PER_TOKEN:.0f} tok each  {basis_note}",
        f"Total all-roles: ~{total_role_bytes / _CHARS_PER_TOKEN:.0f} tok",
    ]
    return {
        "dump_tok": dump_tok,
        "scoped_tok": scoped_tok,
        "pct_saved": pct_saved,
        "pct_label": pct_label,
        "has_real_content": has_real_content,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# Pretty printer
# ---------------------------------------------------------------------------

def _box(text: str, width: int = 60) -> str:
    line = "─" * width
    return f"╭{line}╮\n│  {text:<{width - 2}}│\n╰{line}╯"


def _num(n: int) -> str:
    """Format large numbers with commas."""
    return f"{n:,}"


def run_analyze(target_root: Path) -> int:
    """Main entry point. Returns 0 on success, 1 on error."""
    repo_root = target_root.resolve()
    repo_name = repo_root.name

    print()
    print(_box(f"orgkit analyze  ·  {repo_name}"))
    print()
    print("Scanning top-level folders (read-only — nothing will be changed)...")
    print()

    # ---- Discover folders -----------------------------------------------
    skip = _SKIP_DIRS | {"memory"}
    all_top = sorted(
        p.name for p in repo_root.iterdir()
        if p.is_dir()
        and not p.name.startswith(".")
        and p.name not in skip
    )

    if not all_top:
        print("  (no top-level folders found — is this the right directory?)")
        return 1

    # Check existing roles.json to understand current state
    existing_cfg = load_roles_cfg(repo_root)
    existing_roles: dict = existing_cfg.get("roles", {})
    already_mapped_folders: set[str] = {
        f for r in existing_roles.values() for f in r.get("folders", [])
    }
    existing_role_names: set[str] = set(existing_roles.keys())

    # Folders genuinely unaccounted for: neither a role name nor inside any
    # role's folders list. In fresh-scaffold mode a role's `folders` is empty
    # and the role name itself IS the folder, so a folder that matches a role
    # name is already mapped and must NOT be treated as unmapped.
    unmapped = [
        f for f in all_top
        if f not in already_mapped_folders and f not in existing_role_names
    ]
    already_in_roles = [
        f for f in all_top
        if f in existing_role_names or f in already_mapped_folders
    ]

    # ---- Build display groups -------------------------------------------
    display_groups: dict[str, list[str]] = {}

    if existing_roles:
        # Repo already onboarded: trust roles.json. Do NOT re-run the keyword
        # heuristic over folders that are already mapped — that would re-bucket
        # real roles into bogus heuristic names. Treat an empty `folders` list
        # as "the role name is its own sole folder" (fresh-scaffold mode).
        for role_name, role_info in existing_roles.items():
            folders_in_role = list(role_info.get("folders", []))
            if not folders_in_role and (repo_root / role_name).is_dir():
                folders_in_role = [role_name]
            display_groups[role_name] = folders_in_role

        # Only propose grouping for folders that are genuinely unaccounted for.
        if unmapped:
            proposed = _propose_grouping(unmapped)
            for role, folders in proposed.items():
                display_groups.setdefault(role, []).extend(folders)
    else:
        # No roles.json yet: pure heuristic suggestion over everything.
        proposed = _propose_grouping(all_top)
        for role, folders in proposed.items():
            display_groups[role] = folders

    # ---- Print proposed org chart ----------------------------------------
    print("┌─ Proposed Org Chart " + "─" * 38 + "┐")
    print(f"│  {repo_name}/")

    heuristic_note = "(existing roles.json)" if existing_roles else "(heuristic suggestion — edit to taste)"

    for role in sorted(display_groups.keys()):
        folders_list = sorted(display_groups[role])
        status = "(existing)" if role in existing_role_names else "(proposed)"
        print(f"│  ├── {role}/  {status}")
        for i, f in enumerate(folders_list):
            connector = "│  │   └── " if i == len(folders_list) - 1 else "│  │   ├── "
            print(f"{connector}{f}/")
    print("│")
    print(f"└─ {heuristic_note}")
    print()

    # Print raw folders found
    print(f"Folders scanned: {len(all_top)}")
    if already_in_roles:
        print(f"  Already in role dirs: {', '.join(sorted(already_in_roles))}")
    if unmapped:
        print(f"  Not yet mapped: {', '.join(sorted(unmapped))}")
    print()

    # ---- Token savings estimate ------------------------------------------
    savings = _estimate_token_savings(repo_root, display_groups)

    print("┌─ Token Savings Estimate " + "─" * 34 + "┐")
    print(f"│  Approach                     Tokens / session (estimate)")
    print(f"│  {'─' * 52}")
    print(f"│  Dump everything (global + all roles)   ≈ {_num(savings['dump_tok']):>9} tok")
    print(f"│  Scoped session (global + 1 role)       ≈ {_num(savings['scoped_tok']):>9} tok")
    print(f"│  Savings                                 {savings['pct_label']}")
    print(f"│")
    for note in savings["notes"]:
        print(f"│  · {note}")
    print(f"│")
    print(f"│  * Estimate uses ~{_CHARS_PER_TOKEN:.0f} chars/token. Actual depends on content.")
    if not savings.get("has_real_content"):
        print(f"│  * No ROLE.md files found — % is a projection, not measured.")
    print(f"└─ Token counts are approximate" + "─" * 29 + "┘")
    print()

    # ---- Heuristic explanation -------------------------------------------
    print("How this grouping was suggested:")
    print("  1. Keyword matching: folders containing words like 'web', 'api', 'app'")
    print("     → engineering;  'model', 'ml', 'data' → data-science;  etc.")
    print("  2. Name-prefix clustering: folders sharing a common prefix are grouped.")
    print("  3. Everything else → misc.")
    print()
    print("  This is a SUGGESTION. The real grouping is in .org/roles.json —")
    print("  edit it freely after setup. The heuristic is just a starting point.")
    print()

    # ---- CTA ----------------------------------------------------------------
    if existing_roles:
        print("  orgkit is already installed in this repo.")
        print("  Run `python3 .org/sync_org.py` to refresh ORG.md.")
    else:
        print("  Nothing was changed.")
        print()
        print("  To set this up for real:")
        roles_suggestion = ",".join(
            f"{r}:{r.capitalize()} team"
            for r in sorted(display_groups.keys())[:3]
        )
        print(f'    python3 setup.py --target "{repo_root}" --fresh \\')
        print(f'      --roles "{roles_suggestion}" --yes')
    print()

    return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="orgkit analyze — read-only org-chart teaser (changes nothing)"
    )
    ap.add_argument("--target", default=None, help="Target repo root (default: cwd)")
    args = ap.parse_args()
    root = Path(args.target).resolve() if args.target else detect_repo_root()
    return run_analyze(root)


if __name__ == "__main__":
    sys.exit(main())
