#!/usr/bin/env python3
"""orgkit.cadence — read-only activity analysis to recommend a capture cadence.

WHAT THIS IS
------------
A setup-time helper. It reads Claude Code's own session transcripts
(`~/.claude/projects/**/*.jsonl`) with DuckDB and derives, for THIS repo:

  - when you actually use Claude Code (active hours / weekdays, local time),
  - the lightest window *inside* your active range (best slot to schedule a
    background batch capture so it doesn't compete with live work),
  - how fast each role accumulates capturable work (→ a cadence in days).

It then prints a recommended cadence + time slot. It WRITES NOTHING and makes
no network/API calls — it only reads local transcripts. The numbers feed the
`/orgkit-cadence` command's nudge so the user picks a schedule grounded in real
usage, not a guess.

WHY DuckDB: transcripts are hundreds of newline-delimited JSON files; DuckDB
globs + parses + aggregates them in one query, streaming, far faster than a
hand-rolled Python loop (repo standard for tabular/JSON work).

Honest limits (surfaced in the report, not hidden):
  - Timestamps are UTC; converted to local via the machine's current offset.
  - We see *Claude* activity, not laptop uptime — "active" means "using Claude".
  - Plan tier / hard token limits are NOT in any local file; the caller asks the
    user for those. This tool only ranks *relative* busy vs quiet windows.

Usage:
  python3 .org/cadence.py            # analyse, print recommendation
  python3 .org/cadence.py --json     # machine-readable summary for the command
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from core import detect_repo_root, load_roles  # noqa: E402

# Columns we force on every transcript so heterogeneous files union cleanly.
_NDJSON_COLUMNS = (
    "columns={"
    "timestamp:'VARCHAR', cwd:'VARCHAR', type:'VARCHAR', "
    "message:'STRUCT(usage STRUCT("
    "input_tokens BIGINT, output_tokens BIGINT, "
    "cache_read_input_tokens BIGINT, cache_creation_input_tokens BIGINT))'"
    "}, "
    "ignore_errors=true, maximum_object_size=20000000"
)


def _local_offset_seconds() -> int:
    off = datetime.now().astimezone().utcoffset()
    return int(off.total_seconds()) if off else 0


def _transcript_glob(repo_root: Path) -> str:
    """Glob matching this repo's transcripts.

    Claude Code stores each session under ~/.claude/projects/<cwd-with-slashes-
    as-dashes>/<id>.jsonl. Every cwd inside the repo shares the repo root's
    encoded prefix, so one glob captures the repo root project plus every role
    and project subdir. We still filter by cwd in SQL as a safety net.
    """
    encoded = str(repo_root).replace("/", "-")
    return str(Path.home() / ".claude" / "projects" / f"{encoded}*" / "*.jsonl")


def _run_duck_csv(sql: str) -> list[dict]:
    """Run SQL via the duckdb CLI, return rows as dicts (CSV mode)."""
    duck = shutil.which("duckdb")
    if not duck:
        raise RuntimeError("duckdb CLI not found on PATH")
    proc = subprocess.run(
        [duck, "-csv", "-c", sql],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"duckdb failed: {proc.stderr.strip()}")
    out = proc.stdout.strip()
    if not out:
        return []
    return list(csv.DictReader(io.StringIO(out)))


def analyse(repo_root: Path) -> dict:
    glob = _transcript_glob(repo_root)
    off = _local_offset_seconds()
    root_s = str(repo_root).replace("'", "''")
    # ts_local: timestamp string ends in 'Z' (UTC); cast drops the zone but the
    # wall-clock value is UTC, so adding the local offset yields local time.
    base = (
        f"WITH base AS ("
        f"  SELECT (timestamp::TIMESTAMP + INTERVAL ({off}) SECOND) AS ts_local, "
        f"         cwd, "
        f"         COALESCE(message.usage.input_tokens,0) "
        f"           + COALESCE(message.usage.output_tokens,0) AS tok "
        f"  FROM read_ndjson('{glob}', {_NDJSON_COLUMNS}) "
        f"  WHERE type='assistant' AND timestamp IS NOT NULL "
        f"        AND cwd LIKE '{root_s}%' "
        f"), roled AS ("
        f"  SELECT *, "
        f"    split_part(substr(cwd, length('{root_s}/')+1), '/', 1) AS role "
        f"  FROM base"
        f") "
    )

    hours = _run_duck_csv(
        base
        + "SELECT extract('hour' FROM ts_local) AS hour, "
          "count(*) AS msgs, sum(tok) AS tokens, "
          "count(DISTINCT cast(ts_local AS DATE)) AS days "
          "FROM roled GROUP BY 1 ORDER BY 1;"
    )
    roles = _run_duck_csv(
        base
        + "SELECT role, count(*) AS msgs, "
          "count(DISTINCT cast(ts_local AS DATE)) AS active_days, "
          "max(ts_local) AS last_active "
          "FROM roled WHERE role <> '' GROUP BY 1 ORDER BY msgs DESC;"
    )
    span = _run_duck_csv(
        base
        + "SELECT count(DISTINCT cast(ts_local AS DATE)) AS active_days, "
          "min(cast(ts_local AS DATE)) AS first_day, "
          "max(cast(ts_local AS DATE)) AS last_day, "
          "count(*) AS total_msgs FROM roled;"
    )
    weekdays = _run_duck_csv(
        base
        + "SELECT dayname(ts_local) AS dow, count(*) AS msgs "
          "FROM roled GROUP BY 1 ORDER BY msgs DESC;"
    )
    return {
        "glob": glob,
        "offset_seconds": off,
        "hours": hours,
        "roles": roles,
        "span": span[0] if span else {},
        "weekdays": weekdays,
    }


def recommend(data: dict, valid_roles: set[str]) -> dict:
    """Derive recommended cron slots + cadence from the aggregates.

    Slots: EVERY hour the laptop is awake on >=40% of active days (a cron only
    fires if the machine is on, so awake-probability gates candidacy). We return
    all of them as the multi-attempt set — the once-per-cadence guard means extra
    slots are free retries. They are ORDERED by token usage ascending, so the
    first slot is the least-competition hour (tiebreak = lower token usage).
    Cadence: from how many distinct (role, day) work-events accrue per active
    day; more churn → capture more often.
    """
    AWAKE_THRESHOLD = 0.40  # candidate if laptop on >= this fraction of active days
    MAX_SLOTS = 2           # cap cron attempts; keeps the crontab tidy
    total_days = int((data.get("span") or {}).get("active_days") or 0)
    hours = [
        {"hour": int(h["hour"]), "msgs": int(h["msgs"]),
         "tokens": int(h["tokens"] or 0), "days": int(h.get("days") or 0)}
        for h in data["hours"]
    ]
    rec_slot = None
    rec_awake_pct = None
    rec_slots: list[int] = []
    slot_detail: list[dict] = []
    active_lo = active_hi = None
    if hours and total_days:
        # Candidates: awake on >= 40% of all active days (absolute, not relative
        # to the busiest hour — matches "40% chance the laptop is on then").
        candidates = [h for h in hours if h["days"] / total_days >= AWAKE_THRESHOLD]
        # Tiebreak / ordering: lowest token usage first = least competition.
        candidates.sort(key=lambda h: h["tokens"])
        if candidates:
            # Cap to the MAX_SLOTS lowest-token hours — these become the cron
            # attempts. The once-per-cadence guard means 2 is plenty: two shots
            # at catching the laptop awake, but capture still runs only once.
            chosen = candidates[:MAX_SLOTS]
            rec_slots = [h["hour"] for h in chosen]
            slot_detail = [
                {"hour": h["hour"], "tokens": h["tokens"],
                 "awake_pct": round(100 * h["days"] / total_days)}
                for h in chosen
            ]
            best = chosen[0]
            rec_slot = best["hour"]
            rec_awake_pct = round(100 * best["days"] / total_days)
            active_lo = min(h["hour"] for h in candidates)
            active_hi = max(h["hour"] for h in candidates)

    # Cadence from per-role churn over the observed span.
    span = data.get("span") or {}
    active_days = int(span.get("active_days") or 0)
    repo_roles = [r for r in data["roles"] if r["role"] in valid_roles]
    role_day_events = sum(int(r["active_days"]) for r in repo_roles)
    events_per_day = (role_day_events / active_days) if active_days else 0.0

    if events_per_day >= 2.5:
        cadence_days, why = 2, "high churn (multiple roles worked most days)"
    elif events_per_day >= 1.0:
        cadence_days, why = 3, "moderate churn"
    else:
        cadence_days, why = 7, "light churn (aligns with the 7-day promote gate)"

    return {
        "recommended_slot_hour": rec_slot,
        "recommended_slot_awake_pct": rec_awake_pct,
        "recommended_slots": rec_slots,
        "slot_detail": slot_detail,
        "awake_threshold_pct": int(AWAKE_THRESHOLD * 100),
        "active_range": (active_lo, active_hi),
        "events_per_day": round(events_per_day, 2),
        "cadence_days": cadence_days,
        "cadence_reason": why,
        "repo_roles": repo_roles,
    }


def _fmt_hour(h) -> str:
    if h is None:
        return "n/a"
    return f"{int(h):02d}:00"


def print_report(data: dict, rec: dict) -> None:
    span = data.get("span") or {}
    print("# orgkit capture-cadence analysis\n")
    if not data["hours"]:
        print("No Claude Code transcripts found for this repo yet — "
              "use the repo for a few sessions, then re-run.")
        return
    print(f"Observed: {span.get('total_msgs','?')} assistant turns across "
          f"{span.get('active_days','?')} active days "
          f"({span.get('first_day','?')} → {span.get('last_day','?')}), local time.\n")

    lo, hi = rec["active_range"]
    total_days = int(span.get("active_days") or 0)
    thr = rec.get("awake_threshold_pct", 40)
    print("## When you work")
    print(f"- Candidate hours (laptop on ≥{thr}% of active days, local): "
          f"{_fmt_hour(lo)}–{_fmt_hour(hi)}")
    top_dow = data["weekdays"][0]["dow"] if data["weekdays"] else "?"
    print(f"- Busiest weekday: {top_dow}\n")

    print(f"## Cron slots (≥{thr}% awake, fewest tokens first — capped at 2)")
    detail = rec.get("slot_detail") or []
    if detail:
        for d in detail:
            print(f"  {_fmt_hour(d['hour'])}  awake ~{d['awake_pct']}%  "
                  f"tokens {d['tokens']:,}")
        slots = ",".join(str(h) for h in rec["recommended_slots"])
        print(f"\n  → --slot-hours {slots}  (first = least competition; "
              f"guard runs capture once per cadence)")
    else:
        print("  (not enough data to pick slots)")
    print()

    print("## Hour histogram (local) — bar = turns, ●N = days laptop was on")
    peak = max(int(h["msgs"]) for h in data["hours"])
    for h in data["hours"]:
        m = int(h["msgs"])
        d = int(h.get("days") or 0)
        pct = round(100 * d / total_days) if total_days else 0
        bar = "█" * max(1, round(40 * m / peak)) if m else ""
        print(f"  {int(h['hour']):02d}:00 {bar} {m}  ●{d}/{total_days} ({pct}%)")
    print()

    print("## Per-role activity (this repo)")
    if rec["repo_roles"]:
        for r in rec["repo_roles"]:
            print(f"- {r['role']}: {r['msgs']} turns, "
                  f"{r['active_days']} active days, last {r['last_active']}")
    else:
        print("- (no role-scoped activity detected yet)")
    print()

    print("## Recommendation")
    print(f"- **Cadence: every {rec['cadence_days']} days** "
          f"({rec['cadence_reason']}; ~{rec['events_per_day']} role-days/day)")
    slots = rec.get("recommended_slots") or []
    if slots:
        slot_s = ",".join(f"{h:02d}:00" for h in slots)
        print(f"- **Slots: {slot_s}** — the 2 lowest-token hours among those "
              f"≥{thr}% awake. Both installed as cron attempts; the "
              f"once-per-cadence guard fires only the first that catches the "
              f"laptop awake, the other no-ops for free.")
    else:
        print("- **Slots: none** — not enough activity yet to pick reliable hours.")
    print()
    print("> Note: 'awake' = the laptop was running Claude that hour; a cron only "
          "fires if the machine is on. Plan tier and token limits aren't readable "
          "locally — the setup step asks you for those to judge true headroom.")


def main() -> int:
    ap = argparse.ArgumentParser(description="orgkit capture-cadence analysis")
    ap.add_argument("--json", action="store_true",
                    help="emit machine-readable JSON instead of a report")
    ap.add_argument("--target", help="repo root (default: auto-detect)")
    args = ap.parse_args()

    repo_root = Path(args.target).resolve() if args.target else detect_repo_root()
    valid_roles = set(load_roles(repo_root).keys())

    try:
        data = analyse(repo_root)
    except RuntimeError as exc:
        print(f"cadence analysis failed: {exc}", file=sys.stderr)
        return 1

    rec = recommend(data, valid_roles)
    if args.json:
        print(json.dumps({"data": data, "recommendation": rec}, default=str, indent=2))
    else:
        print_report(data, rec)
    return 0


if __name__ == "__main__":
    sys.exit(main())
