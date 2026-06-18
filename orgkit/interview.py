#!/usr/bin/env python3
"""orgkit.interview — interactive Q&A logic for setup.py.

Holds all prompt/input() calls so setup.py stays clean.
Respects --yes to skip confirmations (for scripted / testable runs).

Functions called by setup.py
-----------------------------
  ask_target(args)            → resolved Path
  ask_mode(args)              → "fresh" | "migrate"
  ask_roles_fresh(args)       → list[{"name":str, "desc":str}]
  ask_roles_for_migrate(...)  → dict[str, str]  {folder: role}
                                (honours --role-map for non-interactive migrate)
  ask_global_claude_md(args)  → str  (content for CLAUDE.md, may be "")
  confirm(prompt, yes)        → bool
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Tiny helpers
# ---------------------------------------------------------------------------

def _input(prompt: str) -> str:
    """input() wrapper so tests can monkeypatch."""
    return input(prompt).strip()


def _yesno(prompt: str, default: bool = True) -> bool:
    suffix = " [Y/n] " if default else " [y/N] "
    raw = _input(prompt + suffix)
    if not raw:
        return default
    return raw.lower().startswith("y")


def confirm(prompt: str, yes: bool) -> bool:
    """Ask the user to confirm, skip if --yes."""
    if yes:
        return True
    return _yesno(prompt)


# ---------------------------------------------------------------------------
# Target detection
# ---------------------------------------------------------------------------

def ask_target(args: Any) -> Path:
    """Resolve and confirm the target repo root."""
    if getattr(args, "target", None):
        p = Path(args.target).resolve()
        print(f"Target repo: {p}")
        return p

    default = Path.cwd().resolve()
    print(f"\nTarget repo root [{default}]: ", end="", flush=True)
    raw = _input("")
    p = Path(raw).resolve() if raw else default
    print(f"Using: {p}")
    return p


# ---------------------------------------------------------------------------
# Mode selection
# ---------------------------------------------------------------------------

def ask_mode(args: Any) -> str:
    """Return 'fresh' or 'migrate'."""
    if getattr(args, "fresh", False):
        return "fresh"
    if getattr(args, "migrate", False):
        return "migrate"

    print("\nSetup mode:")
    print("  1. fresh   — new repo, scaffold roles from scratch")
    print("  2. migrate — existing messy repo, move folders under roles")
    raw = _input("Choice [1/2, default=1]: ")
    return "migrate" if raw.strip() == "2" else "fresh"


# ---------------------------------------------------------------------------
# Fresh mode — role definition
# ---------------------------------------------------------------------------

def parse_roles_json(spec: str) -> list[dict[str, str]]:
    """Parse a comma-safe JSON role spec into a list of {name, desc} dicts.

    ``spec`` is either inline JSON or ``@path/to/file.json`` (the leading ``@``
    means read the JSON from that file). This is the comma-safe transport for
    fresh-mode roles: descriptions are free-text rationale that naturally contain
    commas, which the legacy ``--roles "name:desc,..."`` string shatters into
    phantom roles. JSON carries commas inside string values losslessly.

    Accepted shapes:
      [{"name": "eng", "desc": "Backend, frontend, and infra"}, ...]
      {"eng": "Backend, frontend, and infra", "growth": "Growth and marketing"}

    Raises ValueError on malformed input or a role missing a usable name.
    """
    import json

    spec = spec.strip()
    if spec.startswith("@"):
        path = Path(spec[1:].strip()).expanduser()
        try:
            spec = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"--roles-json file not readable: {path} ({exc})") from exc

    try:
        data = json.loads(spec)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--roles-json is not valid JSON: {exc}") from exc

    result: list[dict[str, str]] = []
    if isinstance(data, dict):
        items = data.items()
    elif isinstance(data, list):
        items = None
    else:
        raise ValueError("--roles-json must be a JSON array of objects or a name→desc object")

    if items is not None:
        for name, desc in items:
            name = str(name).strip()
            if name:
                result.append({"name": name, "desc": str(desc or "").strip()})
    else:
        for entry in data:
            if not isinstance(entry, dict):
                raise ValueError("--roles-json array items must be objects with a 'name' key")
            name = str(entry.get("name", "")).strip()
            if not name:
                raise ValueError("--roles-json entry missing a non-empty 'name'")
            result.append({"name": name, "desc": str(entry.get("desc", "") or "").strip()})

    if not result:
        raise ValueError("--roles-json defined no roles")
    return result


def ask_roles_fresh(args: Any) -> list[dict[str, str]]:
    """Return list of {name, desc} dicts from a flag or interactive Q&A.

    Resolution order:
      1. --roles-json (comma-safe JSON or @file) — preferred, descriptions may
         contain commas.
      2. --roles "name:desc,..." (legacy string transport; descriptions MUST NOT
         contain commas — a comma splits the list).
      3. Interactive prompts.
    """
    # Non-interactive (preferred): comma-safe JSON transport.
    roles_json = getattr(args, "roles_json", None)
    if roles_json:
        return parse_roles_json(roles_json)

    # Non-interactive (legacy): --roles "eng:Engineering team,growth:Growth team"
    # WARNING: splits on ',' so descriptions containing commas break the list.
    roles_flag = getattr(args, "roles", None)
    if roles_flag:
        result: list[dict[str, str]] = []
        for part in roles_flag.split(","):
            part = part.strip()
            if ":" in part:
                name, _, desc = part.partition(":")
                result.append({"name": name.strip(), "desc": desc.strip()})
            elif part:
                result.append({"name": part, "desc": ""})
        return result

    print("\nDefine roles (empty name to finish):")
    roles: list[dict[str, str]] = []
    while True:
        name = _input("  Role name (e.g. dev, marketing, data-science): ")
        if not name:
            if not roles:
                print("  (at least one role required)")
                continue
            break
        desc = _input(f"  One-line description for '{name}': ")
        roles.append({"name": name, "desc": desc})
        print(f"  Added: {name}")

    return roles


# ---------------------------------------------------------------------------
# Migrate mode — folder → role assignment
# ---------------------------------------------------------------------------

def parse_role_map(spec: str) -> dict[str, str]:
    """Parse a "folder:role,folder:role" string into {folder: role}.

    Used by the non-interactive --role-map flag. Blank/role-less entries are
    ignored. Whitespace around folders and roles is stripped.
    """
    result: dict[str, str] = {}
    for part in spec.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        folder, _, role = part.partition(":")
        folder, role = folder.strip(), role.strip()
        if folder and role:
            result[folder] = role
    return result


def ask_roles_for_migrate(
    unmapped_folders: list[str],
    existing_roles: list[str],
    yes: bool,
    preset_map: str | None = None,
) -> dict[str, str]:
    """For each unmapped folder, ask which role it belongs to.

    Returns {folder: role_name}.  Skipping (blank) means leave in place.

    When ``preset_map`` is supplied (the --role-map flag) the assignments are
    taken from it without prompting, making --migrate --yes actually usable in
    scripts. Only folders that are both unmapped and named in the map are moved;
    everything else is left in place.
    """
    if not unmapped_folders:
        print("No unmapped folders found.")
        return {}

    # Non-interactive path: honour --role-map.
    if preset_map:
        parsed = parse_role_map(preset_map)
        unmapped_set = set(unmapped_folders)
        role_map: dict[str, str] = {}
        for folder, role in parsed.items():
            if folder in unmapped_set:
                role_map[folder] = role
            else:
                print(f"  {folder}  →  (in --role-map but not an unmapped folder — skipped)")
        for folder in unmapped_folders:
            if folder not in role_map:
                print(f"  {folder}  →  (not in --role-map — left in place)")
        if role_map:
            new_roles = sorted({r for r in role_map.values() if r not in existing_roles})
            for role in new_roles:
                print(f"  New role '{role}' will be created.")
        return role_map

    if yes:
        print(
            "[setup] --migrate --yes needs an explicit mapping; none supplied. "
            "Re-run with --role-map \"folder:role,...\" to move folders non-interactively."
        )
        return {}

    role_map = {}
    all_roles = list(existing_roles)

    print(f"\nFound {len(unmapped_folders)} unmapped folders.")
    print("For each, enter a role name (existing or new). Press Enter to skip.\n")

    for folder in unmapped_folders:
        if all_roles:
            role_list = ", ".join(all_roles)
            prompt = f"  {folder}  →  role [{role_list}] (or new name, blank=skip): "
        else:
            prompt = f"  {folder}  →  role (blank=skip): "

        raw = _input(prompt)
        if not raw:
            continue
        role = raw.strip()
        role_map[folder] = role
        if role not in all_roles:
            all_roles.append(role)
            print(f"  New role '{role}' will be created.")

    return role_map


# ---------------------------------------------------------------------------
# Global CLAUDE.md seeding
# ---------------------------------------------------------------------------

_CLAUDE_MD_TEMPLATE = """# {repo_name}

## About this repo
_TODO: Describe what this repo is and what lives here._

## Who is working here
_TODO: Team / individual context._

## How to navigate
- Roles are defined in `.org/roles.json`
- Run `python3 .org/sync_org.py` to regenerate `ORG.md`
- Each role has its own brain at `<role>/memory/ROLE.md`

## Key conventions
- Tag insights in any file with `[LESSON]:` `[PATTERN]:` `[GOTCHA]:` `[TOOL]:` — the Stop hook auto-promotes them
- Run `/role-promote <role>` to reconcile pending insights into ROLE.md
"""


def ask_global_claude_md(args: Any, repo_root: Path) -> str:
    """Return content for CLAUDE.md.

    If --yes is set or user skips, return template with repo name filled in.
    """
    repo_name = repo_root.name

    if getattr(args, "yes", False):
        return _CLAUDE_MD_TEMPLATE.format(repo_name=repo_name)

    print("\nSeed a global CLAUDE.md?  (repo-wide context injected into every session)")
    print("Options:")
    print("  1. Use default template (fill in later)")
    print("  2. Enter a one-liner now (will be inserted into template)")
    print("  3. Skip (don't create CLAUDE.md)")
    choice = _input("Choice [1/2/3, default=1]: ").strip() or "1"

    if choice == "3":
        return ""

    content = _CLAUDE_MD_TEMPLATE.format(repo_name=repo_name)

    if choice == "2":
        about = _input("  One-line description of this repo: ")
        content = content.replace(
            "_TODO: Describe what this repo is and what lives here._",
            about or "_TODO: Describe what this repo is and what lives here._",
        )

    return content


# ---------------------------------------------------------------------------
# Standalone test / debug
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Test interview.py interactively")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--target", default=None)
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--migrate", action="store_true")
    ap.add_argument("--roles", default=None)
    ap.add_argument("--roles-json", dest="roles_json", default=None)
    a = ap.parse_args()

    target = ask_target(a)
    mode = ask_mode(a)
    print(f"Mode: {mode}")

    if mode == "fresh":
        roles = ask_roles_fresh(a)
        print(f"Roles: {roles}")
        content = ask_global_claude_md(a, target)
        print(f"CLAUDE.md length: {len(content)}")
    else:
        print("(migrate flow — run via setup.py)")
