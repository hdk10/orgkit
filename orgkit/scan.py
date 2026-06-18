#!/usr/bin/env python3
"""orgkit.scan — deterministic repo signal extractor (no LLM, token-bounded).

Emits structured signals for each top-level folder so the session model can
reason about a real repo structure without reading whole files.

Usage:
  python3 .org/scan.py [--target PATH] [--json]
  python3 orgkit/scan.py [--target PATH] [--json]

Output (--json):
  {
    "repo": "<name>",
    "scanned": <N>,
    "truncated": false,
    "folders": [
      {
        "name": "my-proj",
        "file_count": 42,
        "top_exts": [[".py", 18], [".md", 5], ...],
        "kind": "python",
        "telltale": ["pyproject.toml", "requirements.txt"],
        "excerpt": "My project does X..."
      },
      ...
    ]
  }

Without --json: human-readable table.

Design constraints:
  - stdlib only (no third-party deps).
  - No LLM calls. Fast + safe — handles unreadable files gracefully.
  - Excerpts capped at ~300 chars.
  - Max 40 folders emitted; notes truncation if more.
  - Skips: .git, .org, .claude, node_modules, venv, dotfiles.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Folders to skip at the top level
_SKIP_TOP: frozenset[str] = frozenset({
    ".git", ".org", ".claude", ".bun", ".cache", ".next",
    "node_modules", "venv", ".venv", "__pycache__",
    "dist", "build", "out", "target",
})

# Max top-level folders to emit (keep output token-bounded)
_MAX_FOLDERS = 40

# Max chars for the excerpt field
_EXCERPT_CHARS = 300

# Max bytes to read from a file when hunting for an excerpt
_EXCERPT_READ_BYTES = 4_096

# Max file-walk depth when counting files
_MAX_WALK_DEPTH = 6

# Telltale files that signal the project kind / stack
_TELLTALE_FILES: frozenset[str] = frozenset({
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "setup.py",
    "setup.cfg",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "index.html",
    "Makefile",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "build.gradle",
    "tsconfig.json",
    ".env.example",
    "next.config.js",
    "next.config.ts",
    "vite.config.js",
    "vite.config.ts",
    "tailwind.config.js",
    "tailwind.config.ts",
    "hardhat.config.js",
})

# Extension → kind mapping (majority-rules classification)
# More specific groupings first
_EXT_KIND: dict[str, str] = {
    # Python
    ".py":      "python",
    ".pyx":     "python",
    ".pxd":     "python",
    ".ipynb":   "python",   # notebooks are python-ecosystem
    # JavaScript / TypeScript / Node
    ".js":      "node",
    ".mjs":     "node",
    ".cjs":     "node",
    ".ts":      "node",
    ".tsx":     "node",
    ".jsx":     "node",
    # Web (HTML/CSS — counted separately if JS is absent)
    ".html":    "web",
    ".htm":     "web",
    ".css":     "web",
    ".scss":    "web",
    ".sass":    "web",
    ".less":    "web",
    # Data
    ".csv":     "data",
    ".parquet": "data",
    ".json":    "data",
    ".jsonl":   "data",
    ".ndjson":  "data",
    ".sql":     "data",
    ".db":      "data",
    ".sqlite":  "data",
    # Docs
    ".md":      "docs",
    ".rst":     "docs",
    ".txt":     "docs",
    ".pdf":     "docs",
    ".docx":    "docs",
    # Config / infra (shell, yaml, docker)
    ".sh":      "infra",
    ".bash":    "infra",
    ".zsh":     "infra",
    ".yaml":    "infra",
    ".yml":     "infra",
    ".toml":    "infra",
    ".ini":     "infra",
    ".cfg":     "infra",
    ".conf":    "infra",
    # Rust, Go, Java etc. → catch-all "code"
    ".rs":      "code",
    ".go":      "code",
    ".java":    "code",
    ".kt":      "code",
    ".swift":   "code",
    ".c":       "code",
    ".cpp":     "code",
    ".h":       "code",
    ".rb":      "code",
    ".php":     "code",
    ".cs":      "code",
}

# Kind priority when there is a tie or near-tie (higher = wins)
_KIND_PRIORITY: dict[str, int] = {
    "python": 10,
    "node":   10,
    "web":     8,
    "data":    7,
    "infra":   5,
    "code":    6,
    "docs":    3,
    "mixed":   0,
}

# If the winner kind accounts for < this fraction of typed files → "mixed"
_MIXED_THRESHOLD = 0.40


# ---------------------------------------------------------------------------
# File-walk helpers
# ---------------------------------------------------------------------------

def _walk_folder(path: Path) -> tuple[int, dict[str, int]]:
    """Return (total_file_count, {ext: count}) for a folder tree.

    - Skips unreadable dirs/files gracefully.
    - Caps walk depth at _MAX_WALK_DEPTH.
    - Counts hidden files but not hidden *directories*.
    """
    total = 0
    ext_counts: dict[str, int] = {}
    base_depth = str(path).count(os.sep)

    try:
        for dirpath, dirnames, filenames in os.walk(path, topdown=True, onerror=lambda _: None):
            # Prune hidden dirs and known noise dirs
            current_depth = str(dirpath).count(os.sep) - base_depth
            if current_depth >= _MAX_WALK_DEPTH:
                dirnames.clear()
                continue
            dirnames[:] = [
                d for d in dirnames
                if not d.startswith(".")
                and d not in _SKIP_TOP
            ]

            for fn in filenames:
                total += 1
                ext = os.path.splitext(fn)[1].lower()
                if ext:
                    ext_counts[ext] = ext_counts.get(ext, 0) + 1
    except PermissionError:
        pass

    return total, ext_counts


def _top_exts(ext_counts: dict[str, int], n: int = 5) -> list[tuple[str, int]]:
    """Return top N extensions by count as list of [ext, count] pairs."""
    sorted_exts = sorted(ext_counts.items(), key=lambda kv: kv[1], reverse=True)
    return sorted_exts[:n]


def _classify_kind(ext_counts: dict[str, int]) -> str:
    """Determine the primary kind from extension counts."""
    # Map each ext to a kind, accumulate counts per kind
    kind_counts: dict[str, int] = {}
    typed_total = 0
    for ext, cnt in ext_counts.items():
        kind = _EXT_KIND.get(ext)
        if kind:
            kind_counts[kind] = kind_counts.get(kind, 0) + cnt
            typed_total += cnt

    if not kind_counts:
        return "mixed"

    # Special case: if both "node" and "web" exist, web wins only if no .js/.ts
    # (pure HTML sites vs JS apps). Here we just let counts rule.

    best_kind = max(kind_counts, key=lambda k: (kind_counts[k], _KIND_PRIORITY.get(k, 0)))
    best_count = kind_counts[best_kind]

    if typed_total == 0:
        return "mixed"

    fraction = best_count / typed_total
    if fraction < _MIXED_THRESHOLD:
        return "mixed"

    return best_kind


def _detect_telltale(path: Path) -> list[str]:
    """Return list of telltale filenames present directly in this folder."""
    found: list[str] = []
    try:
        for entry in path.iterdir():
            if entry.is_file() and entry.name in _TELLTALE_FILES:
                found.append(entry.name)
    except PermissionError:
        pass
    return sorted(found)


# ---------------------------------------------------------------------------
# Excerpt extraction
# ---------------------------------------------------------------------------

def _read_excerpt(path: Path, cap: int = _EXCERPT_CHARS) -> str:
    """Read up to `cap` chars from a file, stripping markdown syntax noise."""
    try:
        raw = path.read_bytes()[:_EXCERPT_READ_BYTES].decode("utf-8", errors="replace")
    except Exception:
        return ""

    lines: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        # Skip blank lines, YAML front-matter markers, heading markers only (#)
        if not stripped:
            continue
        if stripped in ("---", "===", "+++"):
            continue
        # Keep the line (with heading markers, URLs, etc — model can parse)
        lines.append(stripped)

    text = " ".join(lines)
    return text[:cap].rstrip()


def _find_excerpt(folder: Path) -> str:
    """Find the best excerpt for a folder.

    Priority:
    1. README.md (or README.rst / readme.md case variants)
    2. First *.md file alphabetically
    3. First Python / JS / TS entry point by convention:
       main.py, app.py, index.js, index.ts, __main__.py
    4. Any .py / .js / .ts file alphabetically
    5. Empty string if nothing found.
    """
    # 1. README variants
    for name in ("README.md", "Readme.md", "readme.md", "README.rst", "README.txt"):
        candidate = folder / name
        if candidate.is_file():
            exc = _read_excerpt(candidate)
            if exc:
                return exc

    # 2. First .md file
    md_files: list[Path] = []
    try:
        md_files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".md")
    except PermissionError:
        pass
    for mf in md_files:
        exc = _read_excerpt(mf)
        if exc:
            return exc

    # 3. Conventional entry points
    for name in ("main.py", "app.py", "__main__.py", "index.js", "index.ts", "index.tsx", "server.py"):
        candidate = folder / name
        if candidate.is_file():
            exc = _read_excerpt(candidate)
            if exc:
                return exc

    # 4. First .py / .js / .ts file
    for ext in (".py", ".js", ".ts", ".tsx"):
        code_files: list[Path] = []
        try:
            code_files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ext)
        except PermissionError:
            pass
        for cf in code_files:
            exc = _read_excerpt(cf)
            if exc:
                return exc

    return ""


# ---------------------------------------------------------------------------
# Core scanner
# ---------------------------------------------------------------------------

def scan_repo(target: Path) -> dict[str, Any]:
    """Scan target repo and return structured signal dict."""
    target = target.resolve()

    # Enumerate top-level folders to scan
    candidates: list[Path] = []
    try:
        for entry in sorted(target.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith("."):
                continue
            if entry.name in _SKIP_TOP:
                continue
            candidates.append(entry)
    except PermissionError:
        pass

    truncated = len(candidates) > _MAX_FOLDERS
    to_scan = candidates[:_MAX_FOLDERS]

    folders: list[dict[str, Any]] = []
    for folder in to_scan:
        file_count, ext_counts = _walk_folder(folder)
        top = _top_exts(ext_counts, 5)
        kind = _classify_kind(ext_counts)
        telltale = _detect_telltale(folder)
        excerpt = _find_excerpt(folder)

        folders.append({
            "name":       folder.name,
            "file_count": file_count,
            "top_exts":   [[ext, cnt] for ext, cnt in top],
            "kind":       kind,
            "telltale":   telltale,
            "excerpt":    excerpt,
        })

    return {
        "repo":      target.name,
        "path":      str(target),
        "scanned":   len(folders),
        "truncated": truncated,
        "folders":   folders,
    }


# ---------------------------------------------------------------------------
# Human-readable output
# ---------------------------------------------------------------------------

def _print_human(result: dict[str, Any]) -> None:
    repo = result["repo"]
    folders = result["folders"]
    truncated = result["truncated"]

    print(f"\norgkit scan  ·  {repo}/")
    print("─" * 60)

    if not folders:
        print("  (no top-level folders found)")
        return

    for f in folders:
        name = f["name"]
        kind = f["kind"]
        count = f["file_count"]
        telltale_str = ", ".join(f["telltale"]) if f["telltale"] else "—"
        exts_str = "  ".join(f"{e}({c})" for e, c in f["top_exts"]) or "—"
        excerpt = f["excerpt"]

        print(f"\n  {name}/")
        print(f"    kind      : {kind}")
        print(f"    files     : {count}")
        print(f"    top exts  : {exts_str}")
        print(f"    telltale  : {telltale_str}")
        if excerpt:
            # Wrap long excerpts
            words = excerpt.split()
            line = "    excerpt   : "
            col = len(line)
            parts: list[str] = []
            cur = line
            for w in words:
                if col + len(w) + 1 > 78 and parts:
                    parts.append(cur)
                    cur = "               " + w
                    col = len(cur)
                else:
                    cur = cur + (" " if parts or cur != line else "") + w
                    col += len(w) + 1
            parts.append(cur)
            print("\n".join(parts))

    print()
    if truncated:
        print(f"  [NOTE] More than {_MAX_FOLDERS} folders found — output truncated.")
    print(f"  Folders scanned: {result['scanned']}")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="scan.py",
        description=(
            "orgkit scan — deterministic repo signal extractor. "
            "Emits structured signals for each top-level folder so the "
            "session model can reason about real repo structure."
        ),
    )
    ap.add_argument(
        "--target",
        default=None,
        help="Target repo root to scan (default: cwd)",
    )
    ap.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit compact JSON (default: human-readable table)",
    )
    return ap.parse_args()


def main() -> int:
    args = _parse_args()
    target = Path(args.target).resolve() if args.target else Path.cwd().resolve()

    if not target.is_dir():
        print(f"[scan] ERROR: target is not a directory: {target}", file=sys.stderr)
        return 1

    result = scan_repo(target)

    if args.as_json:
        # Compact but readable JSON — one folder per "block"
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    else:
        _print_human(result)

    return 0


if __name__ == "__main__":
    sys.exit(main())
