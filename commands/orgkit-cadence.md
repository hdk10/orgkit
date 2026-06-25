---
description: Analyze your real Claude Code usage and recommend a capture cadence + time slot, then optionally install a low-impact background batch-capture cron. Read-only analysis; the cron is opt-in.
argument-hint: (none)
---

You are helping the user choose a **capture cadence** — how often, and at what time, an automated batch `/capture` should sweep their roles — grounded in their actual Claude Code usage, not a guess.

Capture has two paths and this command tunes the second:
- **Live capture** (always on) — the model writes lessons to `_pending.md` as it works. Primary.
- **Batch capture** (what this configures) — a periodic sweep that mines any session the live path missed, across **all roles with pending activity**.

## Steps

1. **Run the analysis** (read-only, writes nothing, no API calls):
   ```bash
   python3 .org/cadence.py
   ```
   Show the user the report: their core hours, busiest weekday, the lightest in-range slot, per-role activity, and the recommended cadence + slot.

2. **Ask the one thing the data can't tell you — plan + headroom.** The analysis sees *Claude* activity but not your plan tier or token limits (those aren't readable locally). Ask:
   - Which plan? (Pro / Max / API pay-as-you-go)
   - Do you regularly hit usage limits, or is there headroom?

   Use the answer to sanity-check the slot: if headroom is tight, lean toward a slot well outside their busiest window so batch capture never competes with active work.

3. **Present the recommendation and the trade-off honestly.** Default mechanism is **cron**, deliberately (see below). State the recommended cadence (e.g. "every 2 days") and slot from the analysis.

   The slot is chosen by **awake-probability** (how many distinct days the laptop was on in that hour), NOT token volume — a cron only fires if the machine is on. If even the best hour is awake <60% of days (common for people without a true idle window), **do not pick one slot**: spread several `--slot-hours` across the hours they're most reliably on (e.g. a couple midday + a couple evening). The once-per-cadence guard fires only the first attempt that catches the laptop awake; every other slot no-ops for free. Surface the per-hour awake% so the user understands firing odds.

4. **Explain cron vs launchd — and why cron is the default here:**
   - **launchd** runs a missed job as a *catch-up at next wake*. That's exactly the wrong moment: you wake with a full day and limited tokens, and capture fires across every role before you start. Rejected for that reason.
   - **cron** only fires if the machine is awake at the scheduled minute; if asleep, it simply skips — **no wake-time ambush.** The cost is a missed cycle when you're asleep at that time. We mitigate that with **multiple attempts across the quiet window plus an idempotency guard**: capture runs at most once per cadence period (a `.last_capture_run` marker short-circuits the rest), so extra cron entries are free retries that catch the laptop whenever it happens to be awake — never repeated token spend, never on wake.

5. **Offer the opt-in install**, and state what they lose by declining:
   > Without scheduled batch capture you rely **only** on live capture. Anything the model didn't write live — it didn't comply, or under-valued a session — is recoverable only by manually running `/capture`. If you never do, those lessons rot when the transcript is cleaned (~30 days). Batch is the safety net.

   If they opt in, install the cron with the recommended (or user-adjusted) cadence + slot:
   ```bash
   python3 .org/install_cron.py --cron --cadence-days <N> --slot-hours <h1,h2,...>
   ```
   The installer:
   - resolves absolute `claude` + `python3` paths (cron has a stripped PATH),
   - uses the user's **subscription token only** — `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token`, placed in a 0600 `.org/.capture_env`. **Never an API key** (that bills a separate pay-as-you-go account); the script hard-refuses if `ANTHROPIC_API_KEY` is set,
   - runs an **auth probe** (`claude -p` once) and refuses to install if headless auth fails, telling the user how to fix it,
   - generates a batch script that, per run: checks the `.last_capture_run` guard (exits free if within cadence), then for **each role with un-captured pending activity** runs capture on **Sonnet** (`--model claude-sonnet-4-6`, never Opus), sequentially, logging to a file,
   - touches `.last_capture_run` only on success.

6. **Confirm what was installed** — show the crontab entries, the log path, and how to uninstall (`python3 .org/install_cron.py --uninstall`). Never claim success without showing the actual installed entries.

## Constraints

- **Capture always runs on Sonnet, never the best/Opus model.** It is a high-frequency, low-stakes distillation task; cost matters more than peak quality.
- **Subscription tokens only — never an API key.** Headless capture authenticates with `CLAUDE_CODE_OAUTH_TOKEN` (subscription), never `ANTHROPIC_API_KEY` (separate billing). No surprise bills.
- Analysis is **read-only**. Installing the cron is the only write, and only after explicit opt-in.
- All paths repo-relative; absolute paths only where the scheduler requires them (resolved at install time).
- Honest reporting: the analysis reflects Claude activity, not laptop uptime, and cannot see plan limits — say so.
