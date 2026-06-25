#!/usr/bin/env python3
"""Headless batch-capture installer (cron, multi-attempt, Sonnet-only).

WHAT THIS INSTALLS
------------------
A cron job that, on a cadence you choose, runs `/capture <role>` HEADLESSLY for
every role that has un-captured pending activity — mining the sessions the
live-capture path missed. It is the safety net under live capture, not a
replacement for it.

DESIGN DECISIONS (and why)
--------------------------
- **cron, NOT launchd.** launchd runs a missed job as a catch-up *at next wake* —
  exactly when you start your day with limited tokens and a full task list. We
  refuse that ambush. cron only fires if the machine is awake at the scheduled
  minute and otherwise just skips. (This reverses the old launchd default.)
- **Multiple attempts + idempotency guard.** Because cron skips when asleep, we
  schedule several entries across a quiet window (e.g. 02:00–05:00). A repo-level
  `.org/.last_capture_run` marker makes capture run AT MOST once per cadence
  period — every extra cron entry is a free retry that no-ops instantly if the
  period's run already happened. Many shots at catching the laptop awake, zero
  repeated token spend, never on wake.
- **All roles, but only pending ones.** Each run captures every role whose
  `_pending.md` holds un-captured activity; roles with nothing new are skipped,
  so token use tracks real work.
- **Sonnet only, never Opus.** Capture is high-frequency distillation; the
  generated `claude -p` call pins `--model claude-sonnet-4-6`.
- **Slash commands DO work in `-p`.** Confirmed against current Claude Code docs,
  so we invoke `/capture <role>` directly (the old "can't run slash commands"
  note was outdated).

AUTH (the real constraint)
--------------------------
cron runs in a stripped env: the macOS Keychain is locked and `~/.claude`
credentials are not auto-discovered. The generated script sources a 0600 env
file `.org/.capture_env` for a `CLAUDE_CODE_OAUTH_TOKEN` from `claude
setup-token`.

**Subscription tokens only — never an API key.** We deliberately do NOT accept
`ANTHROPIC_API_KEY`: that bills pay-as-you-go on a separate Anthropic API
account. `CLAUDE_CODE_OAUTH_TOKEN` draws on the user's existing Pro/Max
subscription, the same pool an interactive session uses, so capture never
produces a surprise bill. (Note: we never pass `--bare`, since bare mode ignores
the OAuth token.)

This installer does NOT write your secret — it creates a template you fill in —
and it runs an auth probe before installing, refusing to install if headless
auth fails.

Usage:
  python3 .org/install_cron.py --cron --cadence-days 2 --slot-hours 2,3,4,5
  python3 .org/install_cron.py --uninstall
  python3 .org/install_cron.py --target /path/repo --cron --cadence-days 3
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from core import detect_repo_root  # noqa: E402

_CRON_MARKER = "# orgkit-capture"
_LOG_PATH = "/tmp/orgkit_capture.log"
_ENV_FILENAME = ".capture_env"
_SCRIPT_FILENAME = "_capture_batch.sh"
_LASTRUN_FILENAME = ".last_capture_run"
_LOCK_FILENAME = ".capture_lock"
_AUTHMARK_FILENAME = ".capture_auth_expired"  # cron writes; role_inject nags on it

# Tools the headless capture genuinely needs — nothing more. dontAsk mode
# rejects anything not listed instead of prompting.
_ALLOWED_TOOLS = "Read,Edit,Write,Grep,Glob,Task,Bash(git *),Bash(python3 *)"


# ---------------------------------------------------------------------------
# Binary resolution (cron/launchd inherit no shell PATH)
# ---------------------------------------------------------------------------

def _resolve_bin(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f"{name} not found on PATH — cannot generate a working cron job.")
    return str(Path(found).resolve())


# ---------------------------------------------------------------------------
# Env-file template (we never write the secret ourselves)
# ---------------------------------------------------------------------------

def _ensure_env_template(repo_root: Path) -> Path:
    env_path = repo_root / ".org" / _ENV_FILENAME
    if env_path.exists():
        return env_path
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(
        "# orgkit headless-capture credential — sourced by the cron script.\n"
        "# cron cannot read the macOS Keychain, so set this and keep the file\n"
        "# private (chmod 600). Do NOT commit it.\n"
        "#\n"
        "# Subscription token ONLY (draws on your Pro/Max plan, NOT a separate\n"
        "# API bill). Generate it once with:  claude setup-token\n"
        "#   export CLAUDE_CODE_OAUTH_TOKEN=...\n"
        "#\n"
        "# Do NOT use ANTHROPIC_API_KEY here — that bills pay-as-you-go on a\n"
        "# separate API account, which this tool intentionally avoids.\n",
        encoding="utf-8",
    )
    env_path.chmod(0o600)
    _ensure_org_gitignore(env_path.parent)
    return env_path


def _ensure_org_gitignore(org_dir: Path) -> None:
    """Make sure the secret + generated runtime files are never committed."""
    gi = org_dir / ".gitignore"
    needed = [_ENV_FILENAME, _LASTRUN_FILENAME, _LOCK_FILENAME,
              _SCRIPT_FILENAME, _AUTHMARK_FILENAME]
    existing = gi.read_text(encoding="utf-8").splitlines() if gi.is_file() else []
    have = set(existing)
    add = [n for n in needed if n not in have]
    if add:
        with gi.open("a", encoding="utf-8") as fh:
            if existing and existing[-1].strip():
                fh.write("\n")
            fh.write("\n".join(add) + "\n")


# ---------------------------------------------------------------------------
# Generated batch script
# ---------------------------------------------------------------------------

def _write_capture_script(repo_root: Path, cadence_days: int) -> Path:
    script = repo_root / ".org" / _SCRIPT_FILENAME
    python3_abs = _resolve_bin("python3")
    claude_abs = _resolve_bin("claude")
    cadence_secs = cadence_days * 86400
    env_file = repo_root / ".org" / _ENV_FILENAME
    lastrun = repo_root / ".org" / _LASTRUN_FILENAME
    lockdir = repo_root / ".org" / _LOCK_FILENAME
    authmark = repo_root / ".org" / _AUTHMARK_FILENAME

    content = f"""#!/bin/bash
# orgkit headless batch capture — generated by install_cron.py. DO NOT EDIT.
# Runs /capture <role> on Sonnet for each role with pending activity, at most
# once per cadence period. Safe to schedule at several quiet hours: extra runs
# no-op via the cadence guard below.
set -uo pipefail

REPO_ROOT="{repo_root}"
PYTHON3="{python3_abs}"
CLAUDE="{claude_abs}"
ENV_FILE="{env_file}"
LASTRUN="{lastrun}"
LOCKDIR="{lockdir}"
AUTHMARK="{authmark}"
LOG="{_LOG_PATH}"
CADENCE_SECS={cadence_secs}

log() {{ echo "[orgkit-capture] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }}

# --- credentials (cron has no Keychain) — subscription OAuth token ONLY ---
[ -f "$ENV_FILE" ] && set -a && . "$ENV_FILE" && set +a
if [ -n "${{ANTHROPIC_API_KEY:-}}" ]; then
  log "ERROR: ANTHROPIC_API_KEY is set — refusing (would bill a separate API account)."
  log "       Use CLAUDE_CODE_OAUTH_TOKEN (claude setup-token) instead. See $ENV_FILE"; exit 1
fi
if [ -z "${{CLAUDE_CODE_OAUTH_TOKEN:-}}" ]; then
  log "ERROR: no CLAUDE_CODE_OAUTH_TOKEN — run 'claude setup-token', then set it in $ENV_FILE"
  touch "$AUTHMARK"   # next interactive session nags the user to fix auth
  exit 1
fi

# --- cadence guard: run at most once per period ---
if [ -f "$LASTRUN" ]; then
  LAST=$(cat "$LASTRUN" 2>/dev/null || echo 0)
  NOW=$(date +%s)
  if [ $((NOW - LAST)) -lt "$CADENCE_SECS" ]; then
    log "within cadence (${{CADENCE_SECS}}s) — skipping, no tokens spent."; exit 0
  fi
fi

# --- single-run lock (atomic mkdir) ---
if ! mkdir "$LOCKDIR" 2>/dev/null; then
  log "another capture run holds the lock — skipping."; exit 0
fi
trap 'rmdir "$LOCKDIR" 2>/dev/null' EXIT

# --- roles with un-captured pending activity ---
PENDING_ROLES=$("$PYTHON3" - <<PYEOF
import sys, pathlib
sys.path.insert(0, "{repo_root}/.org")
from core import load_roles, pending_md_path
repo = pathlib.Path("{repo_root}")
out = []
for role in sorted(load_roles(repo)):
    p = pending_md_path(repo, role)
    if not p.is_file():
        continue
    for raw in p.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith(("#", "<!--", "_")):
            continue
        out.append(role); break
print(" ".join(out))
PYEOF
)
if [ $? -ne 0 ]; then log "ERROR: pending-role scan failed"; exit 1; fi
if [ -z "$PENDING_ROLES" ]; then log "no roles with pending activity — done."; exit 0; fi

log "capturing roles: $PENDING_ROLES"
cd "$REPO_ROOT" || {{ log "ERROR: cannot cd $REPO_ROOT"; exit 1; }}

FAIL=0
AUTHFAIL=0
for role in $PENDING_ROLES; do
  log "/capture $role (sonnet)"
  OUT=$(mktemp)
  if "$CLAUDE" -p "/capture $role" \\
        --model claude-sonnet-4-6 \\
        --permission-mode dontAsk \\
        --allowedTools "{_ALLOWED_TOOLS}" \\
        --output-format json > "$OUT" 2>&1; then
    cat "$OUT" >> "$LOG"
  else
    RC=$?
    cat "$OUT" >> "$LOG"
    # Distinguish auth/token expiry from other failures so we can surface it.
    if grep -qiE 'unauthorized|authentication|invalid[ _-]?token|token .*expired|expired .*token|\\b401\\b|oauth|please (run|log ?in)|setup-token|not logged in' "$OUT"; then
      AUTHFAIL=1; log "AUTH failure capturing $role — token missing/expired"
    else
      FAIL=$((FAIL+1)); log "ERROR: capture failed for $role (rc=$RC)"
    fi
  fi
  rm -f "$OUT"
done

if [ "$AUTHFAIL" -eq 1 ]; then
  # Do NOT stamp the cadence marker: this period did NOT really run, so the next
  # awake slot retries — but more importantly the next interactive session nags.
  touch "$AUTHMARK"
  log "auth expired — cadence NOT stamped; fix with 'claude setup-token' then update $ENV_FILE"
  exit 1
fi

# Auth worked → clear any stale expiry marker, then stamp the cadence period
# (even on a non-auth partial failure: we genuinely attempted this period).
rm -f "$AUTHMARK"
date +%s > "$LASTRUN"
log "done ($FAIL failures)"
[ $FAIL -eq 0 ] || exit 1
exit 0
"""
    script.write_text(content, encoding="utf-8")
    script.chmod(0o755)
    return script


# ---------------------------------------------------------------------------
# Auth probe — refuse to install if headless auth can't work
# ---------------------------------------------------------------------------

def _auth_probe(repo_root: Path) -> bool:
    """Run one trivial headless call to confirm cron-style auth works.

    Sources the env file the same way the generated script will, so we test the
    exact credential path cron uses.
    """
    claude = shutil.which("claude")
    if not claude:
        print("[install_cron] ERROR: claude CLI not found on PATH.", file=sys.stderr)
        return False
    env_file = repo_root / ".org" / _ENV_FILENAME
    # Mirror the cron script exactly: subscription token only, no API key.
    cmd = (
        f'unset ANTHROPIC_API_KEY; '
        f'set -a; [ -f "{env_file}" ] && . "{env_file}"; set +a; '
        f'if [ -z "${{CLAUDE_CODE_OAUTH_TOKEN:-}}" ]; then '
        f'echo "no CLAUDE_CODE_OAUTH_TOKEN" >&2; exit 3; fi; '
        f'"{claude}" -p "reply with: ok" --model claude-sonnet-4-6 '
        f'--permission-mode dontAsk --allowedTools "Read" --output-format json'
    )
    proc = subprocess.run(["/bin/bash", "-c", cmd], capture_output=True, text=True, timeout=120)
    if proc.returncode == 0:
        print("[install_cron] auth probe OK — headless capture authenticates on your subscription.")
        return True
    print("[install_cron] ERROR: headless auth probe failed.", file=sys.stderr)
    print(f"  Fix: run `claude setup-token` (uses your subscription), then put "
          f"CLAUDE_CODE_OAUTH_TOKEN in {env_file} and re-run. Do NOT use an API key.",
          file=sys.stderr)
    if proc.stderr.strip():
        print(f"  detail: {proc.stderr.strip()[:300]}", file=sys.stderr)
    return False


# ---------------------------------------------------------------------------
# crontab management (multiple slot hours, one marker)
# ---------------------------------------------------------------------------

def _cron_lines(repo_root: Path, slot_hours: list[int]) -> list[str]:
    script = repo_root / ".org" / _SCRIPT_FILENAME
    return [
        f"0 {h} * * * /bin/bash {script} >> {_LOG_PATH} 2>&1 {_CRON_MARKER}"
        for h in slot_hours
    ]


def _read_crontab() -> str:
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def _write_crontab(text: str) -> bool:
    p = subprocess.run(["crontab", "-"], input=text, text=True, capture_output=True)
    if p.returncode != 0:
        print(f"[install_cron] crontab write failed: {p.stderr}", file=sys.stderr)
        return False
    return True


def _install(repo_root: Path, cadence_days: int, slot_hours: list[int]) -> int:
    try:
        _resolve_bin("python3")
        _resolve_bin("claude")
    except RuntimeError as exc:
        print(f"[install_cron] ERROR: {exc}", file=sys.stderr)
        return 1

    env_file = _ensure_env_template(repo_root)
    print(f"[install_cron] credential file: {env_file} (chmod 600; fill in before this works)")

    if not _auth_probe(repo_root):
        return 1
    # Fresh valid token → clear any stale expiry marker from a previous failure.
    (repo_root / ".org" / _AUTHMARK_FILENAME).unlink(missing_ok=True)

    script = _write_capture_script(repo_root, cadence_days)

    # Idempotent: strip any prior orgkit-capture lines, then add the new set.
    existing = "\n".join(
        ln for ln in _read_crontab().splitlines() if _CRON_MARKER not in ln
    ).strip("\n")
    lines = _cron_lines(repo_root, slot_hours)
    new_tab = (existing + "\n" if existing else "") + "\n".join(lines) + "\n"
    if not _write_crontab(new_tab):
        return 1

    print(f"[install_cron] installed capture cron: cadence every {cadence_days} day(s), "
          f"attempts at hours {','.join(f'{h:02d}:00' for h in slot_hours)} (local).")
    print(f"[install_cron] batch script: {script}")
    print(f"[install_cron] log: {_LOG_PATH}")
    print("[install_cron] crontab entries:")
    for ln in lines:
        print(f"    {ln}")
    print("[install_cron] uninstall with: python3 .org/install_cron.py --uninstall")
    return 0


def _uninstall() -> int:
    cur = _read_crontab()
    if _CRON_MARKER not in cur:
        print("[install_cron] no orgkit-capture cron entries — nothing to remove.")
        return 0
    filtered = "\n".join(ln for ln in cur.splitlines() if _CRON_MARKER not in ln).strip("\n")
    filtered = (filtered + "\n") if filtered else ""
    if not _write_crontab(filtered):
        return 1
    print("[install_cron] removed orgkit-capture cron entries.")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_slot_hours(s: str) -> list[int]:
    hours = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        h = int(part)
        if not 0 <= h <= 23:
            raise ValueError(f"slot hour out of range 0–23: {h}")
        hours.append(h)
    if not hours:
        raise ValueError("no valid slot hours given")
    return sorted(set(hours))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Install/uninstall the orgkit headless batch-capture cron "
                    "(Sonnet-only, multi-attempt, run-once-per-cadence)."
    )
    ap.add_argument("--target", default=None, help="repo root (default: auto-detect)")
    ap.add_argument("--cron", action="store_true", help="install the capture cron")
    ap.add_argument("--cadence-days", type=int, default=2,
                    help="run capture at most once per this many days (default 2)")
    ap.add_argument("--slot-hours", default="12,21",
                    help="comma-separated local hours to attempt (max ~2); pick hours "
                         "you're reliably awake (run /orgkit-cadence for your numbers). "
                         "The once-per-cadence guard fires only the first that catches "
                         "the laptop on (default 12,21)")
    ap.add_argument("--uninstall", action="store_true", help="remove the capture cron")
    args = ap.parse_args()

    repo_root = Path(args.target).resolve() if args.target else detect_repo_root()

    if args.uninstall:
        return _uninstall()
    if not args.cron:
        ap.error("pass --cron to install, or --uninstall to remove.")
    try:
        slot_hours = _parse_slot_hours(args.slot_hours)
    except ValueError as exc:
        print(f"[install_cron] ERROR: {exc}", file=sys.stderr)
        return 1
    if args.cadence_days < 1:
        print("[install_cron] ERROR: --cadence-days must be >= 1", file=sys.stderr)
        return 1
    return _install(repo_root, args.cadence_days, slot_hours)


if __name__ == "__main__":
    sys.exit(main())
