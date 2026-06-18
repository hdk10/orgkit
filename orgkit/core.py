#!/usr/bin/env python3
"""orgkit.core — shared primitives for all orgkit engine scripts.

Every other orgkit module imports from here. Nothing in this module
imports from sibling orgkit files (no circular deps).

Key responsibilities
--------------------
- Repo-root detection (3-strategy cascade, no hardcoded paths)
- roles.json load / save with safe error handling
- Marker file read / write (JSON {ts, iso} blobs)
- Role / folder enumeration helpers
- Path-discipline helpers used by migrate.py and rewrite logic
- Shared throttle constants consumed by role_inject and role_digest
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Throttle constants (role_inject + role_digest consume these)
# ---------------------------------------------------------------------------
PROMOTE_STALE_DAYS: int = 7
NAG_COOLDOWN_HOURS: int = 24

# ---------------------------------------------------------------------------
# File-walk include / exclude sets (role_digest + migrate share these)
# ---------------------------------------------------------------------------
INCLUDE_EXTS: frozenset[str] = frozenset({
    ".md", ".py", ".sh", ".js", ".ts", ".tsx",
    ".yaml", ".yml", ".toml", ".html", ".ipynb",
    ".json",
})
EXCLUDE_DIRS: frozenset[str] = frozenset({
    ".git", "node_modules", "venv", ".venv", "__pycache__",
    ".code-review-graph", "out", "dist", "build", ".next",
    ".bun", ".cache", ".org",
})

# Files to skip when walking for path-rewrites
LOCK_FILES: frozenset[str] = frozenset({
    "package-lock.json", "bun.lock", "yarn.lock",
    "poetry.lock", "uv.lock",
})

MAX_FILE_BYTES: int = 500_000  # skip files larger than 500 KB


# ---------------------------------------------------------------------------
# Repo-root detection  (NO hardcoded /Users/ paths anywhere)
# ---------------------------------------------------------------------------

def detect_repo_root() -> Path:
    """Return the absolute path to the target repo root.

    Strategy (first match wins):
    1. $CLAUDE_PROJECT_DIR env var — AUTHORITATIVE. If it contains
       .org/roles.json, use it unconditionally.  Scripts that need a
       specific target should set this variable or pass ``--target``.
    2. Walk *up* from cwd looking for .org/roles.json.
    3. Walk *up* from __file__ ONLY as a tiebreaker for the case where
       this module lives at <repo>/.org/core.py inside the target repo.
       If strategies 2 and 3 disagree (e.g. orgkit is running from inside
       a *different* repo's source tree), cwd wins — Strategy 3 is
       skipped when Strategy 2 already produced a result.

    WARNING: Strategy 3 can resolve to a PARENT repo's .org/roles.json
    when orgkit's source tree lives inside another orgkit-managed repo
    (e.g. claude-code/dev/orgkit/).  Always run scripts from the target
    repo or set $CLAUDE_PROJECT_DIR / pass --target to be explicit.

    Never crashes: falls back to cwd so callers get a usable Path.
    """
    # Strategy 1 – env var (authoritative)
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        p = Path(env).resolve()
        if (p / ".org" / "roles.json").exists():
            return p

    # Strategy 2 – walk up from cwd
    cwd = Path.cwd().resolve()
    cwd_result: Path | None = None
    for candidate in [cwd, *cwd.parents]:
        if (candidate / ".org" / "roles.json").exists():
            cwd_result = candidate
            break

    # If cwd walk found something, prefer it — skip Strategy 3 entirely
    # to avoid resolving to a parent repo when orgkit lives inside it.
    if cwd_result is not None:
        return cwd_result

    # Strategy 3 – walk up from this file's location (only when cwd gave
    # no result, i.e. we are genuinely outside any orgkit-managed repo).
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / ".org" / "roles.json").exists():
            return candidate

    # Fallback – cwd (caller will handle missing roles.json gracefully)
    return cwd


# ---------------------------------------------------------------------------
# roles.json helpers
# ---------------------------------------------------------------------------

def roles_file(repo_root: Path) -> Path:
    return repo_root / ".org" / "roles.json"


def load_roles_cfg(repo_root: Path) -> dict[str, Any]:
    """Load full roles.json as a dict.  Returns {} on error."""
    rf = roles_file(repo_root)
    if not rf.exists():
        return {}
    try:
        with rf.open(encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        _warn(f"[orgkit.core] Could not read {rf}: {exc}")
        return {}


def load_roles(repo_root: Path) -> dict[str, Any]:
    """Return only the 'roles' sub-dict (most callers only need this)."""
    return load_roles_cfg(repo_root).get("roles", {})


def save_roles_cfg(repo_root: Path, cfg: dict[str, Any]) -> None:
    """Write roles.json atomically-ish (write then replace)."""
    rf = roles_file(repo_root)
    rf.parent.mkdir(parents=True, exist_ok=True)
    tmp = rf.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(rf)
    except Exception as exc:
        _warn(f"[orgkit.core] Could not write {rf}: {exc}")
        tmp.unlink(missing_ok=True)


def default_roles_cfg() -> dict[str, Any]:
    """Seed structure for a brand-new repo."""
    return {
        "_meta": {
            "description": "Role → folder mapping. Edit manually or via setup.py. sync_org.py regenerates ORG.md.",
            "exclude_dirs": [".claude", ".code-review-graph", ".git", ".planning", ".org", "memory"],
        },
        "roles": {},
    }


# ---------------------------------------------------------------------------
# Marker file helpers  (JSON blobs: {"ts": float, "iso": str})
# ---------------------------------------------------------------------------

def read_marker_ts(path: Path) -> float:
    """Return the float timestamp stored in a marker file. 0.0 if missing/invalid."""
    if not path.exists():
        return 0.0
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
        return float(data.get("ts", 0.0))
    except Exception:
        return 0.0


def write_marker(path: Path) -> None:
    """Write {ts, iso} marker atomically.  Creates parent dirs if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    try:
        path.write_text(
            json.dumps({"ts": now, "iso": datetime.fromtimestamp(now).isoformat()}),
            encoding="utf-8",
        )
    except Exception as exc:
        _warn(f"[orgkit.core] Could not write marker {path}: {exc}")


# ---------------------------------------------------------------------------
# Role / folder enumeration
# ---------------------------------------------------------------------------

def list_roles(repo_root: Path) -> list[str]:
    """Sorted list of role names from roles.json."""
    return sorted(load_roles(repo_root).keys())


def list_role_folders(repo_root: Path, role: str) -> list[str]:
    """Folders listed under a role in roles.json."""
    return load_roles(repo_root).get(role, {}).get("folders", [])


def list_top_dirs(repo_root: Path, exclude: set[str] | None = None) -> list[str]:
    """Top-level non-hidden directories in repo_root, minus excluded names."""
    excl = exclude or set()
    return sorted(
        p.name for p in repo_root.iterdir()
        if p.is_dir() and not p.name.startswith(".") and p.name not in excl
    )


def role_memory_dir(repo_root: Path, role: str) -> Path:
    return repo_root / role / "memory"


def role_md_path(repo_root: Path, role: str) -> Path:
    return role_memory_dir(repo_root, role) / "ROLE.md"


def pending_md_path(repo_root: Path, role: str) -> Path:
    return role_memory_dir(repo_root, role) / "_pending.md"


def project_md_path(repo_root: Path, role: str, project: str) -> Path:
    return repo_root / role / project / "memory" / "PROJECT.md"


def global_claude_md_path(repo_root: Path) -> Path:
    return repo_root / "CLAUDE.md"


# ---------------------------------------------------------------------------
# Path-discipline helpers (used by migrate.py rewrite logic)
# ---------------------------------------------------------------------------

def derive_abs_prefix(repo_root: Path) -> str:
    """Return the absolute string prefix for path-rewrite patterns.

    e.g. /Users/alice/projects/myrepo  (no trailing slash)
    Never hardcoded — derived from the detected repo root.
    """
    return str(repo_root)


def derive_repo_prefix(repo_root: Path) -> str:
    """Return the repo directory name for relative path-rewrite patterns.

    e.g. for /Users/alice/projects/myrepo → 'myrepo'
    """
    return repo_root.name


# ---------------------------------------------------------------------------
# File-walk helper
# ---------------------------------------------------------------------------

def walk_rewritable_files(root: Path, extra_exclude: set[str] | None = None) -> list[Path]:
    """Return all text files under root eligible for path-rewriting.

    Prunes EXCLUDE_DIRS, LOCK_FILES, and files > MAX_FILE_BYTES.
    """
    excl = EXCLUDE_DIRS | (extra_exclude or set())
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dirnames[:] = [d for d in dirnames if d not in excl]
        for fn in filenames:
            if fn in LOCK_FILES:
                continue
            ext = os.path.splitext(fn)[1].lower()
            if ext not in INCLUDE_EXTS:
                continue
            full = Path(dirpath) / fn
            try:
                if full.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            files.append(full)
    return files


# ---------------------------------------------------------------------------
# Tiny internal helpers
# ---------------------------------------------------------------------------

def _warn(msg: str) -> None:
    import sys
    print(msg, file=sys.stderr)


def safe_read(path: Path, default: str = "") -> str:
    """Read a text file, return default on any error."""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return default


def safe_write(path: Path, content: str) -> bool:
    """Write text to a file, creating parents. Return True on success."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return True
    except Exception as exc:
        _warn(f"[orgkit.core] Could not write {path}: {exc}")
        return False


if __name__ == "__main__":
    root = detect_repo_root()
    print(f"Detected repo root: {root}")
    roles = load_roles(root)
    print(f"Roles ({len(roles)}): {', '.join(sorted(roles))}")
