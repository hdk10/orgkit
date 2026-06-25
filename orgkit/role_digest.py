#!/usr/bin/env python3
"""Stop hook: tag-scrape [LESSON]/[PATTERN]/[GOTCHA]/[TOOL] from changed files.

IMPORTANT — two-tier capture model
------------------------------------
This script is the CHEAP FAST LANE only. It runs on every session Stop in
< 1 second with no API calls and no model involvement. It does ONE thing:
regex-scrape pre-written [LESSON]/[PATTERN]/[GOTCHA]/[TOOL] tags from files
that changed since .last_digest. In practice, almost nobody writes those tags,
so this fast lane usually captures nothing.

The REAL capture path is model-driven:
  /capture <role>     — model reads actual changed files, distills genuine
                        insights, appends tagged bullets to _pending.md
  /role-promote <role> — sonnet subagent mines diffs + PROJECT.md files for
                         lessons, integrates them into ROLE.md, rebuilds the
                         index of where everything lives, dedupes, declutters

Use this script's output (the _pending.md stub-queue entry) as a signal that
something changed, NOT as a substitute for model-driven synthesis. The stub
queue exists so /capture and /role-promote know which files to read.

Two entrypoints
---------------
scrape  (default, called from Stop hook):
  - Cheap, instant — no API call, no external deps.
  - Scans files modified since .last_digest marker in each role.
  - Extracts lines matching the four tag types (if any exist in the files).
  - Appends deduplicated tag bullets directly to ROLE.md when found.
  - Queues a brief "files touched" stub entry in _pending.md so that
    /capture and /role-promote know which files changed.
  - Updates .last_digest marker.

Note: the subagent digest mode from the original reference has been
intentionally removed. orgkit reconciles via /capture (model distills from
real work) and /role-promote (sonnet subagent reconciles + rebuilds index).
No ANTHROPIC_API_KEY needed for either command.

Usage:
  python3 .org/role_digest.py           # scrape all roles (Stop hook)
  python3 .org/role_digest.py scrape    # explicit scrape
"""
from __future__ import annotations

import argparse
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
    write_marker,
    safe_read,
    EXCLUDE_DIRS,
    INCLUDE_EXTS,
    role_memory_dir,
    role_md_path,
    pending_md_path,
)

# ---------------------------------------------------------------------------
# Tag regex + section mapping
# ---------------------------------------------------------------------------
TAG_RE = re.compile(
    r"\[(LESSON|PATTERN|GOTCHA|TOOL)\]:\s*(.+?)(?:$|\n)",
    re.MULTILINE,
)

SECTION_MAP: dict[str, str] = {
    "LESSON": "## Best practices",
    "PATTERN": "## Patterns",
    "GOTCHA": "## Gotchas",
    "TOOL": "## Tools / stacks",
}


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def _files_modified_since(role_dir: Path, since_ts: float):
    """Yield files inside role_dir with mtime > since_ts."""
    for dirpath, dirnames, filenames in os.walk(role_dir):
        # Skip excluded dirs AND nested git repos (vendored/standalone projects
        # like a cloned tool — their doc examples must not be scraped as lessons).
        dirnames[:] = [
            d for d in dirnames
            if d not in EXCLUDE_DIRS and not (Path(dirpath) / d / ".git").exists()
        ]
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if ext not in INCLUDE_EXTS:
                continue
            full = Path(dirpath) / fn
            try:
                if full.stat().st_mtime > since_ts:
                    yield full
            except OSError:
                continue


# ---------------------------------------------------------------------------
# ROLE.md tag insertion
# ---------------------------------------------------------------------------

def _append_tag_to_role_md(role_md: Path, tag: str, text: str, src_file: Path, repo_root: Path) -> bool:
    """Append a tagged insight bullet under the correct section in ROLE.md.

    Idempotent: if the exact bullet text already exists, skip.
    Returns True if a new bullet was written.
    """
    if not role_md.is_file():
        return False
    content = role_md.read_text(encoding="utf-8")
    section_header = SECTION_MAP.get(tag)
    if not section_header:
        return False

    try:
        rel_src = src_file.relative_to(repo_root)
    except ValueError:
        rel_src = src_file

    bullet = f"- {text.strip()} _(from `{rel_src}`)_"
    if bullet in content:
        return False  # already present — idempotent

    # Find the section header, capture its body up to the next --- or ## boundary,
    # then insert the bullet BEFORE that boundary (i.e., inside the section).
    # Using DOTALL so .* crosses newlines; the alternation \n---|\n## stops at
    # whichever section terminator comes first.
    pattern = re.compile(
        rf"({re.escape(section_header)}\n)(.*?)(\n---|\n##)",
        re.DOTALL,
    )
    m = pattern.search(content)
    if m:
        # m.group(2) is the section body; insert after its last non-blank line
        section_body = m.group(2)
        stripped_len = len(section_body.rstrip("\n"))
        insert_pos = m.start(2) + stripped_len
        new_content = content[:insert_pos] + f"\n{bullet}" + content[insert_pos:]
    else:
        # Section is at end of file (no --- or ## terminator follows)
        if section_header in content:
            new_content = content.rstrip("\n") + f"\n{bullet}\n"
        else:
            return False  # section missing — don't fabricate structure

    role_md.write_text(new_content, encoding="utf-8")
    return True


# ---------------------------------------------------------------------------
# _pending.md helpers
# ---------------------------------------------------------------------------

def _append_to_pending(pending_md: Path, line: str) -> None:
    if not pending_md.exists():
        pending_md.parent.mkdir(parents=True, exist_ok=True)
        pending_md.write_text(
            "# Pending insights\n\n",
            encoding="utf-8",
        )
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    with pending_md.open("a", encoding="utf-8") as fh:
        fh.write(f"- [{ts}] {line}\n")


def _pending_has_transcript(pending_md: Path, transcript_path: str) -> bool:
    """True if this transcript path is already queued in _pending.md.

    The Stop hook fires on EVERY assistant turn, so without this guard a long
    session would append the same transcript pointer dozens of times. Dedupe by
    the path string — one pointer per transcript per role is all /capture needs.
    """
    if not pending_md.is_file():
        return False
    try:
        return transcript_path in pending_md.read_text(encoding="utf-8")
    except Exception:
        return False


def _assistant_text_from_transcript(transcript_path: str) -> str:
    """Concatenate the text of all assistant turns in a session transcript.

    The transcript is newline-delimited JSON; assistant rows carry
    `message.content`, which is either a string or a list of blocks where text
    blocks have a `text` field. We only need assistant text — that's where the
    live-capture directive tells the model to emit its [TAG]: lines.
    """
    p = Path(transcript_path)
    if not p.is_file():
        return ""
    chunks: list[str] = []
    try:
        for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except Exception:
                continue
            if not isinstance(row, dict) or row.get("type") != "assistant":
                continue
            msg = row.get("message") or {}
            content = msg.get("content")
            if isinstance(content, str):
                chunks.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and isinstance(block.get("text"), str):
                        chunks.append(block["text"])
    except OSError:
        return ""
    return "\n".join(chunks)


def _scrape_transcript_to_pending(transcript_path: str, pend_md: Path) -> int:
    """Harvest [TAG]: lines the model emitted in its replies into _pending.md.

    This is the consumer side of the live-capture directive (design B): the model
    just *says* the tagged line; this deterministically captures it. Deduped by
    bullet text so the per-turn Stop hook re-scanning the whole transcript never
    creates duplicates. Returns the count of new bullets queued.
    """
    text = _assistant_text_from_transcript(transcript_path)
    if not text:
        return 0
    existing = ""
    if pend_md.is_file():
        try:
            existing = pend_md.read_text(encoding="utf-8")
        except Exception:
            existing = ""
    new = 0
    seen_this_run: set[str] = set()
    for m in TAG_RE.finditer(text):
        tag = m.group(1)
        body = m.group(2).strip()
        if not body:
            continue
        bullet = f"[{tag}]: {body} _(from session conversation)_"
        if bullet in seen_this_run or bullet in existing:
            continue
        seen_this_run.add(bullet)
        _append_to_pending(pend_md, bullet)
        new += 1
    return new


def _active_role(repo_root: Path, cwd: str | None, roles: dict) -> str | None:
    """Resolve the role the session is operating in, from its cwd.

    Mirrors role_inject.py's SessionStart logic: the first path component under
    the repo root that names a role. Returns None when the session started at
    the repo root or outside any role — there's no single role to attribute a
    zero-edit conversation to in that case.
    """
    if not cwd:
        return None
    try:
        parts = Path(cwd).resolve().relative_to(repo_root.resolve()).parts
    except (ValueError, OSError):
        return None
    if parts and parts[0] in roles:
        return parts[0]
    return None


# ---------------------------------------------------------------------------
# Main scrape logic
# ---------------------------------------------------------------------------

def tag_scrape_all(
    repo_root: Path,
    transcript_path: str | None = None,
    cwd: str | None = None,
) -> None:
    """Tag-scrape all roles.  Runs in well under 1 second for typical repos.

    If `transcript_path` is given (the Stop hook passes it on stdin), a pointer
    to the session transcript is queued in `_pending.md` so `/capture` and
    `/role-promote` can mine the actual conversation (decisions, trade-offs,
    gotchas), not just file diffs. The pointer is queued for:
      - every role whose files changed this session, AND
      - the *active* role (resolved from `cwd`) even when ZERO files changed —
        this is what lets a discussion-only session (we corrected Claude, chose
        an approach, edited nothing) still leave its conversation for capture.
    Deduped by transcript path, so the per-turn Stop hook never floods the queue.
    """
    roles = load_roles(repo_root)
    active_role = _active_role(repo_root, cwd, roles)
    summary: list[tuple[str, int, int]] = []

    for role in sorted(roles.keys()):
        role_dir = repo_root / role
        if not role_dir.is_dir():
            continue

        mem_dir = role_memory_dir(repo_root, role)
        if not mem_dir.is_dir():
            continue

        marker = mem_dir / ".last_digest"
        r_md = role_md_path(repo_root, role)
        pend_md = pending_md_path(repo_root, role)

        since_ts = read_marker_ts(marker)
        if since_ts == 0.0:
            # First run — set baseline and skip (avoid massive backfill on first install)
            write_marker(marker)
            continue

        new_tags = 0
        files_changed: list[Path] = []

        for fpath in _files_modified_since(role_dir, since_ts):
            # Skip the memory files themselves to avoid feedback loops
            if mem_dir in fpath.parents or fpath == r_md or fpath == pend_md:
                continue
            files_changed.append(fpath)
            text = safe_read(fpath)
            for m in TAG_RE.finditer(text):
                tag = m.group(1)
                body = m.group(2).strip()
                if _append_tag_to_role_md(r_md, tag, body, fpath, repo_root):
                    new_tags += 1

        if files_changed:
            # Queue a brief entry in _pending.md for later /role-promote
            file_list = ", ".join(
                str(f.relative_to(repo_root)) for f in files_changed[:5]
            )
            more = f" +{len(files_changed) - 5} more" if len(files_changed) > 5 else ""
            _append_to_pending(
                pend_md,
                f"{role}: {len(files_changed)} files changed "
                f"({file_list}{more}) — run /capture then /role-promote",
            )

        # Queue the transcript pointer when files changed in this role OR this is
        # the active role with no edits (discussion-only session). Deduped by path
        # so the per-turn Stop hook leaves at most one pointer per transcript.
        queued_transcript = False
        if transcript_path and (files_changed or role == active_role):
            if not _pending_has_transcript(pend_md, transcript_path):
                _append_to_pending(
                    pend_md,
                    f"{role}: session transcript at {transcript_path} "
                    f"— /capture to mine the conversation (decisions, gotchas, why)",
                )
                queued_transcript = True

        # Live capture (design B): harvest [TAG]: lines the model emitted in its
        # replies this session into _pending.md. Only for the active role's own
        # transcript; deduped so re-scanning every turn never duplicates.
        live_tags = 0
        if transcript_path and role == active_role:
            live_tags = _scrape_transcript_to_pending(transcript_path, pend_md)

        if files_changed or queued_transcript or live_tags:
            summary.append((role, len(files_changed), new_tags + live_tags))

        write_marker(marker)

    for role, n_files, n_tags in summary:
        print(
            f"[role_digest] {role}: {n_files} files changed, {n_tags} tags captured "
            f"(file tags→ROLE.md, live reply tags→_pending.md)",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="orgkit role_digest — tag-scrape changed files into ROLE.md"
    )
    sub = ap.add_subparsers(dest="mode")
    sub.add_parser("scrape", help="tag-scrape all roles (default)")
    args = ap.parse_args()

    mode = args.mode or "scrape"
    if mode == "scrape":
        repo_root = detect_repo_root()
        # The Stop hook passes a JSON payload on stdin with the transcript path.
        # Best-effort: read it if present so we can point /capture at the
        # conversation. Never block or crash when run manually (no stdin).
        transcript_path = None
        cwd = None
        try:
            if not sys.stdin.isatty():
                payload = json.load(sys.stdin)
                if isinstance(payload, dict):
                    transcript_path = payload.get("transcript_path")
                    cwd = payload.get("cwd")
        except Exception:
            transcript_path = None
            cwd = None
        cwd = cwd or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
        tag_scrape_all(repo_root, transcript_path=transcript_path, cwd=cwd)
    else:
        print(f"Unknown mode: {mode}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
