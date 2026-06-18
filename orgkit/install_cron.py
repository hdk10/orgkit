#!/usr/bin/env python3
"""Optional stale-role reminder installer.

Installs a cron job (Linux) or launchd plist (macOS) whose ONLY job is
to detect stale roles and write a marker file so that the NEXT interactive
Claude Code session reconciles them automatically via the Stop / SessionStart
hooks.

WHAT THIS DOES:
  For each role whose ROLE.md was last reconciled more than PROMOTE_STALE_DAYS
  ago, it touches <role>/memory/.reconcile_due.  That marker is picked up by
  role_inject.py / role_digest.py on your next real session.

WHAT THIS DOES NOT DO:
  - It does NOT run the model headless.
  - `claude -p "/role-promote <role>"` does NOT execute slash commands in
    print mode — the model just receives the literal text as a prompt, so
    the command is silently ignored.  We removed that call entirely.
  - It is not a substitute for an interactive session; it is only a reminder.

TECHNICAL NOTES:
  - `python3` is NOT on launchd's/cron's default PATH.  The generated
    script embeds the absolute python3 path resolved at install time via
    shutil.which(), exactly like the claude-bin resolution.
  - `mkdir -p` is called before any touch/write so the marker directory
    always exists.  Exit codes are checked; failures are logged and the
    script exits non-zero.

Usage:
  python3 .org/install_cron.py                        # install weekly (default)
  python3 .org/install_cron.py --weekly               # explicit weekly
  python3 .org/install_cron.py --uninstall            # remove installed entry
  python3 .org/install_cron.py --target /path/repo    # target a specific repo
"""
from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from core import detect_repo_root  # noqa: E402

# Cron schedule: Sunday 03:00
CRON_SCHEDULE_WEEKLY = "0 3 * * 0"

# launchd plist label and path
_PLIST_LABEL = "com.orgkit.role-promote"
_PLIST_DIR = Path.home() / "Library" / "LaunchAgents"
_PLIST_PATH = _PLIST_DIR / f"{_PLIST_LABEL}.plist"

# Cron comment marker so we can find + remove the entry later
_CRON_MARKER = "# orgkit-role-promote"

_INSTALL_NOTICE = """\
[install_cron] WHAT THIS SCHEDULES
  A lightweight shell script runs on the configured schedule (default: weekly).
  Its ONLY job: detect roles whose ROLE.md is stale and write a
  .reconcile_due marker file.  Your NEXT interactive Claude Code session
  reads that marker and reconciles the role.  No headless model call is made.

  Why not `claude -p "/role-promote <role>"`?
    Claude Code's -p (print) mode does NOT execute slash commands; the model
    receives the text as a literal prompt and ignores it.  So we don't attempt
    it — only marker files are written.

  What actually runs:
    python3 (absolute path embedded at install time via shutil.which)
    reading .org/core.py to detect staleness, then touching the marker.
"""


# ---------------------------------------------------------------------------
# macOS launchd
# ---------------------------------------------------------------------------

def _launchd_plist_content(repo_root: Path, weekly: bool) -> str:
    """Build a launchd plist XML string for the reconcile job."""
    # WeeklyInterval = 7 days in seconds
    interval_seconds = 7 * 24 * 3600 if weekly else 24 * 3600

    script = repo_root / ".org" / "_cron_reconcile.sh"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{_PLIST_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>{script}</string>
  </array>
  <key>StartInterval</key>
  <integer>{interval_seconds}</integer>
  <key>RunAtLoad</key>
  <false/>
  <key>StandardOutPath</key>
  <string>/tmp/orgkit_cron.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/orgkit_cron_err.log</string>
</dict>
</plist>
"""


def _install_launchd(repo_root: Path, weekly: bool) -> int:
    script = _write_reconcile_script(repo_root)
    _PLIST_DIR.mkdir(parents=True, exist_ok=True)
    _PLIST_PATH.write_text(_launchd_plist_content(repo_root, weekly), encoding="utf-8")

    # Unload first (idempotent)
    subprocess.run(["launchctl", "unload", str(_PLIST_PATH)], capture_output=True)
    result = subprocess.run(["launchctl", "load", str(_PLIST_PATH)], capture_output=True)
    if result.returncode == 0:
        freq = "weekly" if weekly else "daily"
        print(f"[install_cron] launchd plist installed ({freq}): {_PLIST_PATH}")
        print(f"[install_cron] reconcile script: {script}")
        print(f"[install_cron] logs: /tmp/orgkit_cron.log and /tmp/orgkit_cron_err.log")
        return 0
    else:
        print(f"[install_cron] launchctl load failed: {result.stderr.decode()}", file=sys.stderr)
        return 1


def _uninstall_launchd() -> int:
    if not _PLIST_PATH.exists():
        print("[install_cron] No plist found — nothing to remove.")
        return 0
    subprocess.run(["launchctl", "unload", str(_PLIST_PATH)], capture_output=True)
    _PLIST_PATH.unlink()
    print(f"[install_cron] removed: {_PLIST_PATH}")
    return 0


# ---------------------------------------------------------------------------
# Linux cron
# ---------------------------------------------------------------------------

def _cron_line(repo_root: Path, weekly: bool) -> str:
    schedule = CRON_SCHEDULE_WEEKLY if weekly else "0 3 * * *"
    script = repo_root / ".org" / "_cron_reconcile.sh"
    return f"{schedule} /bin/bash {script} >> /tmp/orgkit_cron.log 2>&1 {_CRON_MARKER}"


def _install_cron(repo_root: Path, weekly: bool) -> int:
    script = _write_reconcile_script(repo_root)
    line = _cron_line(repo_root, weekly)

    # Read current crontab
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    existing = result.stdout if result.returncode == 0 else ""

    # Check idempotency
    if _CRON_MARKER in existing:
        print("[install_cron] cron entry already present — skipping.")
        return 0

    new_crontab = existing.rstrip("\n") + f"\n{line}\n"
    proc = subprocess.run(["crontab", "-"], input=new_crontab, text=True, capture_output=True)
    if proc.returncode == 0:
        freq = "weekly" if weekly else "daily"
        print(f"[install_cron] cron entry installed ({freq}).")
        print(f"[install_cron] reconcile script: {script}")
        print(f"[install_cron] logs: /tmp/orgkit_cron.log")
        return 0
    else:
        print(f"[install_cron] crontab write failed: {proc.stderr}", file=sys.stderr)
        return 1


def _uninstall_cron() -> int:
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if result.returncode != 0 or _CRON_MARKER not in result.stdout:
        print("[install_cron] No orgkit cron entry found — nothing to remove.")
        return 0
    filtered = "\n".join(
        line for line in result.stdout.splitlines() if _CRON_MARKER not in line
    ) + "\n"
    proc = subprocess.run(["crontab", "-"], input=filtered, text=True, capture_output=True)
    if proc.returncode == 0:
        print("[install_cron] cron entry removed.")
        return 0
    print(f"[install_cron] crontab write failed: {proc.stderr}", file=sys.stderr)
    return 1


# ---------------------------------------------------------------------------
# Shared: reconcile shell script
# ---------------------------------------------------------------------------

def _resolve_python3_bin() -> str:
    """Return the absolute path to python3, or raise RuntimeError if not found.

    cron/launchd inherit a minimal PATH that does NOT include the shell's
    nvm/pyenv shims or /usr/local/bin entries.  We resolve the path at
    install time and embed it in the generated script so the Python
    staleness check actually runs under cron/launchd.
    """
    found = shutil.which("python3")
    if found:
        return str(Path(found).resolve())
    raise RuntimeError(
        "python3 not found on PATH.  Cannot generate a working cron script."
    )


def _write_reconcile_script(repo_root: Path) -> Path:
    """Write <repo>/.org/_cron_reconcile.sh.

    MARKER-ONLY: for each stale role, writes
    <role>/memory/.reconcile_due so the next interactive Claude Code
    session (via role_inject.py / Stop hook) triggers reconciliation.

    Does NOT call the claude CLI — `claude -p` does not execute slash
    commands in print mode, so attempting headless /role-promote is
    futile and misleading.

    The absolute python3 path is resolved at install time (shutil.which)
    and embedded in the script because cron/launchd do not inherit the
    shell PATH.  The marker directory is created with mkdir -p before
    any touch so we never silently fail.
    """
    script = repo_root / ".org" / "_cron_reconcile.sh"
    python3_abs = _resolve_python3_bin()

    content = f"""#!/bin/bash
# orgkit stale-role reminder — generated by install_cron.py
# PURPOSE: write .reconcile_due markers for stale roles so the NEXT
#          interactive Claude Code session reconciles them.
# THIS SCRIPT DOES NOT RUN THE MODEL HEADLESS.
#   `claude -p "/role-promote <role>"` passes the text as a literal
#   prompt in print mode; slash commands are NOT executed.  We do not
#   attempt it.
# DO NOT EDIT — re-run install_cron.py to regenerate.

set -uo pipefail
REPO_ROOT="{repo_root}"
# python3 absolute path resolved at install time (cron/launchd have no shell PATH)
PYTHON3="{python3_abs}"

echo "[orgkit-cron] starting stale-role marker sweep $(date -u +%Y-%m-%dT%H:%M:%SZ)"

if [ ! -x "$PYTHON3" ]; then
  echo "[orgkit-cron] ERROR: python3 not executable at $PYTHON3 — aborting" >&2
  exit 1
fi

# Detect stale roles using the installed orgkit core
STALE_ROLES=$(
  "$PYTHON3" - <<'PYEOF'
import sys, time
sys.path.insert(0, "{repo_root}/.org")
from core import load_roles, read_marker_ts, PROMOTE_STALE_DAYS
import pathlib
repo_root = pathlib.Path("{repo_root}")
roles = load_roles(repo_root)
now = time.time()
stale = []
for role in sorted(roles):
    mem = repo_root / role / "memory"
    ts = read_marker_ts(mem / ".last_promote")
    if (now - ts) >= PROMOTE_STALE_DAYS * 86400:
        stale.append(role)
print("\\n".join(stale))
PYEOF
)
PYTHON_EXIT=$?
if [ $PYTHON_EXIT -ne 0 ]; then
  echo "[orgkit-cron] ERROR: staleness check exited $PYTHON_EXIT — aborting" >&2
  exit $PYTHON_EXIT
fi

if [ -z "$STALE_ROLES" ]; then
  echo "[orgkit-cron] no stale roles — nothing to do."
  exit 0
fi

echo "[orgkit-cron] stale roles: $STALE_ROLES"

ERRORS=0
for role in $STALE_ROLES; do
  MARKER="{repo_root}/$role/memory/.reconcile_due"
  MARKER_DIR="$(dirname "$MARKER")"

  # Ensure the directory exists before touching the marker
  if ! mkdir -p "$MARKER_DIR"; then
    echo "[orgkit-cron] ERROR: could not create $MARKER_DIR" >&2
    ERRORS=$((ERRORS + 1))
    continue
  fi

  if touch "$MARKER"; then
    echo "[orgkit-cron] wrote marker: $MARKER"
    echo "[orgkit-cron]   -> next interactive Claude Code session will reconcile $role"
  else
    echo "[orgkit-cron] ERROR: could not write marker: $MARKER" >&2
    ERRORS=$((ERRORS + 1))
  fi
done

echo "[orgkit-cron] done $(date -u +%Y-%m-%dT%H:%M:%SZ)"
if [ $ERRORS -gt 0 ]; then
  echo "[orgkit-cron] WARNING: $ERRORS marker write(s) failed — check logs" >&2
  exit 1
fi
exit 0
"""
    script.write_text(content, encoding="utf-8")
    script.chmod(0o755)
    return script


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def install(
    target_root: Path | None = None,
    weekly: bool = True,
    uninstall: bool = False,
) -> int:
    """Install or uninstall the stale-role reminder job.

    The installed script writes .reconcile_due markers for stale roles so
    that the next interactive Claude Code session reconciles them.  It does
    NOT attempt any headless model call.

    Returns 0 on success, 1 on error.
    """
    repo_root = target_root or detect_repo_root()

    if not uninstall:
        print(_INSTALL_NOTICE)

        # Verify python3 is resolvable — required for the generated script.
        try:
            py3 = _resolve_python3_bin()
            print(f"[install_cron] python3 found at: {py3}")
            print(f"  This absolute path will be embedded in the generated script.")
        except RuntimeError as exc:
            print(f"[install_cron] ERROR: {exc}", file=sys.stderr)
            return 1

    system = platform.system()

    if system == "Darwin":
        if uninstall:
            return _uninstall_launchd()
        return _install_launchd(repo_root, weekly)
    else:
        # Linux (and anything else — fallback to cron)
        if uninstall:
            return _uninstall_cron()
        return _install_cron(repo_root, weekly)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Install/uninstall the orgkit stale-role reminder (cron/launchd). "
            "Writes .reconcile_due markers for stale roles so the NEXT interactive "
            "Claude Code session reconciles them.  Does NOT run the model headless "
            "(`claude -p` does not execute slash commands in print mode)."
        )
    )
    ap.add_argument("--target", default=None, help="Target repo root (default: auto-detect)")
    ap.add_argument("--weekly", action="store_true", default=True, help="Weekly schedule (default)")
    ap.add_argument("--daily", action="store_true", help="Daily schedule instead of weekly")
    ap.add_argument("--uninstall", action="store_true", help="Remove the installed job")
    args = ap.parse_args()

    target = Path(args.target).resolve() if args.target else None
    weekly = not args.daily
    return install(target_root=target, weekly=weekly, uninstall=args.uninstall)


if __name__ == "__main__":
    sys.exit(main())
