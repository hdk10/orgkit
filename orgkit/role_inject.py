#!/usr/bin/env python3
"""SessionStart + UserPromptSubmit hook: inject role memory as additionalContext.

Three levels of injection (in order, when applicable):
  1. Global CLAUDE.md  — always injected if the file exists.
  2. Role ROLE.md      — injected when cwd is inside a role folder OR when the
                         first user prompt mentions a role name / project folder.
  3. Project PROJECT.md — injected when cwd is inside <role>/<project>/.

Also fires a stale-reconcile nudge when a role's brain is overdue for
/role-promote (controlled by PROMOTE_STALE_DAYS / NAG_COOLDOWN_HOURS).

State: /tmp/claude_role_inject_<safe_session_id>.json → {"loaded": role|null, "root_start": bool}

Hook IO contract:
  Input:  JSON on stdin with keys: hook_event_name, session_id, cwd, prompt
  Output: {"hookSpecificOutput": {"hookEventName": <event>, "additionalContext": <str>}}
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from core import (  # noqa: E402
    detect_repo_root,
    load_roles,
    read_marker_ts,
    write_marker,
    safe_read,
    PROMOTE_STALE_DAYS,
    NAG_COOLDOWN_HOURS,
    global_claude_md_path,
    role_md_path,
    project_md_path,
    pending_md_path,
    role_memory_dir,
)

STATE_DIR = Path("/tmp")


# ---------------------------------------------------------------------------
# Throttle / reconcile logic
# ---------------------------------------------------------------------------

def _pending_has_activity(repo_root: Path, role: str) -> bool:
    """True if _pending.md holds any unprocessed queue content.

    Matches org-status.md's definition of "Pending": the file exists and
    contains at least one line that is NOT the drained marker (`_Drained`),
    NOT an HTML/markdown comment, and NOT blank. This covers every producer —
    role_digest's stub line, model-driven /capture markers
    (`<!-- /capture run ... -->`) plus its `[LESSON]:`/`[GOTCHA]:`/`[PATTERN]:`/
    `[TOOL]:` bullets — rather than keying on a single phrase.
    """
    f = pending_md_path(repo_root, role)
    if not f.is_file():
        return False
    try:
        text = f.read_text(encoding="utf-8")
    except Exception:
        return False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("<!--") or line.startswith("#"):
            continue
        # Italic-meta lines (`_Awaiting…_`, `_Drained…_`, `_Reconciled…_`) are
        # seeds/markers, not real activity.
        if line.startswith("_"):
            continue
        return True
    return False


def reconcile_due(repo_root: Path, role: str) -> bool:
    """Stale brain + pending activity + not recently nagged → True."""
    mem = role_memory_dir(repo_root, role)
    now = time.time()
    last_promote = read_marker_ts(mem / ".last_promote")
    last_nag = read_marker_ts(mem / ".last_nag")
    if (now - last_nag) < NAG_COOLDOWN_HOURS * 3600:
        return False
    if (now - last_promote) < PROMOTE_STALE_DAYS * 86400:
        return False
    return _pending_has_activity(repo_root, role)


def _reconcile_marker_path(repo_root: Path, role: str) -> Path:
    """Path of the cron/launchd-written .reconcile_due marker for a role.

    install_cron.py's generated _cron_reconcile.sh touches this file for every
    stale role. This is the reader side of that contract — without it the cron
    marker write would be dead work (see install_cron.py docstring).
    """
    return role_memory_dir(repo_root, role) / ".reconcile_due"


def consume_reconcile_marker(repo_root: Path, role: str) -> bool:
    """If a cron-written .reconcile_due marker exists, delete it and return True.

    Presence of the marker means a background cron/launchd sweep flagged this
    role as stale between sessions. We consume it exactly once: deleting it here
    stops the directive from re-firing on every subsequent SessionStart until
    the next sweep writes a fresh marker.
    """
    marker = _reconcile_marker_path(repo_root, role)
    if not marker.exists():
        return False
    try:
        marker.unlink()
    except OSError:
        # Couldn't remove it (permissions/race); still treat as a trigger so the
        # reconcile happens, but never crash the hook over it.
        pass
    return True


def mark_nagged(repo_root: Path, role: str) -> None:
    write_marker(role_memory_dir(repo_root, role) / ".last_nag")


def autofire_directive(role: str) -> str:
    return (
        "\n\n---\n\n"
        "## Auto-reconcile due (role memory maintenance)\n\n"
        f"This role's brain hasn't been reconciled in over {PROMOTE_STALE_DAYS} days and "
        f"`{role}/memory/_pending.md` has unprocessed activity. "
        f"As the FIRST step this session — before the user's main request — run the "
        f"slash command `/role-promote {role}`. "
        "This merges pending insights into ROLE.md and drains the queue. "
        "Then proceed normally with the user's request.\n"
    )


# ---------------------------------------------------------------------------
# Session state (per-session, idempotent within a session)
# ---------------------------------------------------------------------------

def _state_path(session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "default")
    return STATE_DIR / f"orgkit_role_inject_{safe}.json"


def _load_state(p: Path) -> dict:
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_state(p: Path, st: dict) -> None:
    try:
        p.write_text(json.dumps(st), encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Context builders
# ---------------------------------------------------------------------------

def _read_global_claude_md(repo_root: Path) -> str:
    p = global_claude_md_path(repo_root)
    if not p.is_file():
        return ""
    content = safe_read(p)
    if not content.strip():
        return ""
    return (
        "# Global context injected: CLAUDE.md\n\n"
        "_This is the repo-wide CLAUDE.md for this project. It applies to every session._\n\n"
        "---\n\n"
        + content
    )


def _read_role_md(repo_root: Path, role: str) -> str:
    p = role_md_path(repo_root, role)
    body = safe_read(p, "(no ROLE.md found — empty role memory)")
    return (
        f"# Role memory injected: {role}\n\n"
        f"_The session is operating inside `{role}/`. Below is `{role}/memory/ROLE.md`, "
        f"the accumulated team knowledge for this role. Use it as context. "
        f"Update via tagged `[LESSON]:`/`[PATTERN]:`/`[GOTCHA]:`/`[TOOL]:` lines._\n\n"
        "---\n\n"
        + body
    )


def _read_project_md(repo_root: Path, role: str, project: str) -> str:
    p = project_md_path(repo_root, role, project)
    if not p.is_file():
        return ""
    body = safe_read(p)
    if not body.strip():
        return ""
    return (
        f"# Project memory injected: {role}/{project}\n\n"
        f"_Below is `{role}/{project}/memory/PROJECT.md`, the session-specific project notes._\n\n"
        "---\n\n"
        + body
    )


def _build_full_context(
    repo_root: Path,
    role: str,
    project: str | None,
) -> str:
    """Assemble all three injection levels into one additionalContext string."""
    parts: list[str] = []

    global_ctx = _read_global_claude_md(repo_root)
    if global_ctx:
        parts.append(global_ctx)

    parts.append(_read_role_md(repo_root, role))

    if project:
        proj_ctx = _read_project_md(repo_root, role, project)
        if proj_ctx:
            parts.append(proj_ctx)

    return "\n\n---\n\n".join(parts)


def _root_chooser_context(repo_root: Path, roles: dict) -> str:
    """Emit a chooser when the session started at the repo root."""
    global_ctx = _read_global_claude_md(repo_root)
    lines: list[str] = []

    if global_ctx:
        lines.append(global_ctx)
        lines.append("")

    lines += [
        "# No role auto-loaded",
        "",
        "Session started at the repo root — no specific role folder detected.",
        "Infer the role from the user's first request, then read the appropriate ROLE.md.",
        "",
        "## Roles available",
        "",
    ]
    for name, meta in sorted(roles.items()):
        lines.append(f"- **{name}** — {meta.get('desc', '').strip()}")
    lines += [
        "",
        "## How a role gets loaded",
        "",
        f"Once the role is clear from chat, read `<repo>/<role>/memory/ROLE.md`.",
        "The UserPromptSubmit hook also auto-injects ROLE.md the first time the "
        "user's prompt mentions a role name or one of its project folder names.",
    ]
    return "\n".join(lines)


def _match_role_in_prompt(prompt: str, roles: dict) -> str | None:
    """Return the first role matched by name or folder-name in prompt text."""
    text = (prompt or "").lower()
    # Exact role-name word boundary
    for name in roles:
        if re.search(rf"\b{re.escape(name.lower())}\b", text):
            return name
    # Folder names inside each role
    for name, meta in roles.items():
        for folder in meta.get("folders", []):
            if re.search(rf"\b{re.escape(folder.lower())}\b", text):
                return name
    return None


# ---------------------------------------------------------------------------
# Emit
# ---------------------------------------------------------------------------

def _emit(event: str, context: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": context,
        }
    }))


# ---------------------------------------------------------------------------
# Main hook handler
# ---------------------------------------------------------------------------

def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # malformed input — silent exit so hook doesn't break session

    hook_event = payload.get("hook_event_name", "SessionStart")
    session_id = payload.get("session_id", "default")
    cwd = payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()

    repo_root = detect_repo_root()
    roles = load_roles(repo_root)
    if not roles:
        return 0  # no roles configured — nothing to inject

    sp = _state_path(session_id)
    st = _load_state(sp)

    # ------------------------------------------------------------------
    # SessionStart
    # ------------------------------------------------------------------
    if hook_event == "SessionStart":
        try:
            rel = Path(cwd).resolve().relative_to(repo_root.resolve())
            inside = True
            parts = rel.parts
        except ValueError:
            inside = False
            parts = ()

        if inside and parts and parts[0] in roles:
            role = parts[0]
            # Detect project level: <role>/<project>/...
            project: str | None = None
            if len(parts) >= 2 and not parts[1].startswith("."):
                candidate_proj = parts[1]
                # Confirm there's a PROJECT.md (or at minimum the dir exists)
                proj_dir = repo_root / role / candidate_proj
                if proj_dir.is_dir():
                    project = candidate_proj

            st.update({"loaded": role, "root_start": False, "project": project})
            _save_state(sp, st)

            ctx = _build_full_context(repo_root, role, project)
            # Two independent triggers fire the reconcile directive:
            #   1. reconcile_due(): this session recomputed staleness live.
            #   2. a .reconcile_due marker left by the background cron/launchd
            #      sweep (install_cron.py). consume_reconcile_marker() reads it
            #      and deletes it so it fires exactly once per sweep.
            marker_fired = consume_reconcile_marker(repo_root, role)
            if reconcile_due(repo_root, role) or marker_fired:
                ctx += autofire_directive(role)
                mark_nagged(repo_root, role)

            _emit("SessionStart", ctx)
            return 0

        if inside:
            # cwd is inside the repo but not in a role sub-directory
            st.update({"loaded": None, "root_start": True, "project": None})
            _save_state(sp, st)
            _emit("SessionStart", _root_chooser_context(repo_root, roles))
        return 0

    # ------------------------------------------------------------------
    # UserPromptSubmit  — fire only once per session, only from root start
    # ------------------------------------------------------------------
    if hook_event == "UserPromptSubmit":
        if st.get("loaded"):
            return 0  # already loaded a role this session
        if not st.get("root_start"):
            return 0  # didn't start at root — shouldn't happen
        prompt = payload.get("user_prompt") or payload.get("prompt") or ""
        role = _match_role_in_prompt(prompt, roles)
        if not role:
            return 0
        st["loaded"] = role
        st["project"] = None  # can't infer project from prompt alone
        _save_state(sp, st)
        _emit("UserPromptSubmit", _build_full_context(repo_root, role, None))
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
