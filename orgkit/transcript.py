#!/usr/bin/env python3
"""transcript.py — extract clean conversation text from a Claude Code session.

A session transcript is a `.jsonl` file (one JSON object per line) in
`~/.claude/projects/<project>/<session-id>.jsonl`. Most lines are noise
(tool calls, attachments, mode flags). This helper keeps only the actual
dialogue — what the user said and what the assistant said back — so the
model can mine it for lessons (decisions, trade-offs, gotchas) without
paying for tool-call spam.

Pure stdlib. Token-bounded: caps output to the most recent --max-chars
(default 40k) and notes truncation, since recent work is most relevant
for capture.

Usage:
  python3 .org/transcript.py <session.jsonl> [--max-chars N]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def _text_from_content(content) -> str:
    """Pull human-readable text out of a message.content (str or block list)."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                parts.append(block.get("text", ""))
            # skip tool_use / tool_result / thinking / images — that's the noise
        return "\n".join(p for p in parts if p).strip()
    return ""


def extract(jsonl_path: Path, max_chars: int = 40_000) -> str:
    """Return cleaned 'USER:/ASSISTANT:' dialogue, newest-capped to max_chars."""
    turns: list[str] = []
    try:
        f = jsonl_path.open(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"(could not read transcript: {e})"
    with f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = obj.get("type")
            if t not in ("user", "assistant"):
                continue
            msg = obj.get("message")
            if not isinstance(msg, dict):
                continue
            text = _text_from_content(msg.get("content"))
            if not text:
                continue
            who = "USER" if t == "user" else "ASSISTANT"
            turns.append(f"{who}: {text}")

    body = "\n\n".join(turns)
    if len(body) > max_chars:
        tail = body[-max_chars:]
        # Advance to the first clean turn boundary so we never start mid-sentence.
        # Turns are joined with "\n\n" and each starts with "USER:" or "ASSISTANT:".
        boundary = re.search(r"\n\nUSER:|\n\nASSISTANT:", tail)
        if boundary:
            tail = tail[boundary.start() + 2:]  # +2 to skip the leading \n\n
        body = "[…earlier conversation truncated…]\n\n" + tail
    return body


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Extract clean dialogue from a session transcript.")
    ap.add_argument("path", type=Path, help="Path to the session .jsonl")
    ap.add_argument("--max-chars", type=int, default=40_000)
    args = ap.parse_args(argv)
    if not args.path.is_file():
        print(f"transcript: not found: {args.path}", file=sys.stderr)
        return 1
    sys.stdout.write(extract(args.path, args.max_chars))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
