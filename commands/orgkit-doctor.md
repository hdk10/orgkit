---
description: Diagnose and interactively fix broken orgkit state — verifies the .org/ engine, roles.json, role brains, hook registration, and ORG.md, then offers to repair what is broken. Pass --dry-run to report only without fixing.
---

You are running orgkit's **doctor** — a diagnostic and repair command for the current repo's org-memory setup. It checks whether the engine, config, role brains, and hooks are all wired correctly, flags anything broken, and — with your confirmation — **repairs** broken states (removes dangling hooks, restores roles.json, re-registers missing hooks). The repair pass only runs after you confirm; a `--dry-run` report-only mode is also available.

## Steps

1. **Locate the doctor module.** Prefer the engine installed in the repo; fall back to the plugin copy.
   ```bash
   DOCTOR_PY="${CLAUDE_PROJECT_DIR:-$PWD}/.org/doctor.py"
   if [ ! -f "$DOCTOR_PY" ]; then
     DOCTOR_PY="${CLAUDE_PLUGIN_ROOT}/orgkit/doctor.py"
   fi
   ```
   The user's repo root is `${CLAUDE_PROJECT_DIR}` (fall back to `$PWD`).

2. **Diagnose first (report-only, no changes).** Always run a `--dry-run` pass to get the diagnosis. This never prompts and never mutates anything, so it is safe to run from a non-interactive shell:
   ```bash
   python3 "$DOCTOR_PY" --target "${CLAUDE_PROJECT_DIR:-$PWD}" --dry-run
   ```
   This checks for: a valid `.org/roles.json`, the engine scripts under `.org/`, each role's `memory/ROLE.md` brain, registered lifecycle hooks, and a current `ORG.md`. The dry-run prints the diagnosis table and lists the fixable issues without applying any of them.

3. **Present the diagnostics** as a checklist (PASS / WARN / FAIL per check), then summarize the overall health and the single most important fix if anything failed.

4. **Confirm before repairing.** If — and only if — the dry-run reported one or more fixable issues, use the **AskUserQuestion** tool to ask whether to apply the fixes (list exactly what will change: e.g. "remove N dangling hooks", "restore roles.json", "re-register hooks"). Do not apply anything without an explicit yes. If the user passed `--dry-run` themselves, stop here and skip the repair pass entirely.

5. **Apply fixes non-interactively (only on confirmation).** doctor.py's interactive prompt relies on a TTY stdin that this tool does not have, so apply with `--yes` rather than the bare invocation:
   ```bash
   python3 "$DOCTOR_PY" --target "${CLAUDE_PROJECT_DIR:-$PWD}" --yes
   ```
   This re-diagnoses and applies every fixable repair without prompting. Report what changed (it prints a settings.json backup filename for any hook edits).

6. **Suggest remedies** based on what failed:
   - No `.org/roles.json` → run `/orgkit-init` to onboard.
   - Hooks missing → re-run `/orgkit-init` (it re-registers hooks idempotently).
   - Stale role brains / pending content → `/orgkit:org-status` then `/orgkit:role-promote <role>`.

## Constraints

- **Diagnose, confirm, then repair.** Never run the bare `python3 "$DOCTOR_PY" --target ...` form: with no `--yes`/`--dry-run` it falls into an interactive `input()` prompt that has no TTY here, so it would diagnose and then apply zero fixes. Always run `--dry-run` first to report, and only run `--yes` after the user confirms via AskUserQuestion.
- **The repair pass mutates state** (removes dangling hooks, restores roles.json, re-registers hooks, edits `~/.claude/settings.json` with a timestamped backup). Get explicit confirmation before the `--yes` pass; tell the user what will change.
- Target the user's repo via `--target "${CLAUDE_PROJECT_DIR:-$PWD}"`, never `${CLAUDE_PLUGIN_ROOT}`.
- If `doctor.py` is not present in either location, report that clearly and fall back to `/orgkit:org-status` for a structural view.
