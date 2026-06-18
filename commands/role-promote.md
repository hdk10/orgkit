---
description: Autonomously reconcile a role's memory — merge pending + recent learnings into ROLE.md, dedupe and declutter. The auto-promotion step fired when a role is stale (>7 days) with pending activity.
argument-hint: <role>
---

You are the **autonomous reconciler** for the **$ARGUMENTS** role's memory. No human review gate — you both decide and write. Do it carefully: ROLE.md is a long-lived org brain injected into every session in this role, so signal density matters more than volume.

This is a full RECONCILE, not an append. You are rewriting the brain to be better than you found it.

## Steps

1. **Validate role.** Read `.org/roles.json`. If `$ARGUMENTS` is empty or not listed under `roles`, print the valid role names and stop. The roles.json lives at `.org/roles.json` (relative to repo root, which is your cwd).

2. **Delegate the rewrite to a sonnet subagent** (keep the main thread lean). Pass it this exact task:

   > You are rewriting the role brain for **$ARGUMENTS**. Read these files:
   >
   > - `$ARGUMENTS/memory/ROLE.md` — the current brain (what we already know).
   > - `$ARGUMENTS/memory/_pending.md` — two kinds of content live here:
   >   1. Stub-queue lines ("N files changed (paths)") — tell you WHICH files changed; read those files directly to extract insight.
   >   2. Tagged bullets already written by `/capture` (`[LESSON]`/`[PATTERN]`/`[GOTCHA]`/`[TOOL]`) — these are ready to integrate; still dedupe against ROLE.md.
   >   3. Transcript pointers ("session transcript at `<path>`") — the Stop hook records where each session's conversation lives.
   > - For each changed project path listed in the stub queue: read `<path>/memory/PROJECT.md` and the referenced changed files.
   > - For each transcript pointer: run `python3 .org/transcript.py "<path>"` to get the clean conversation, and mine it for the **reasoning a diff can't show** — decisions and why, trade-offs, approaches tried and abandoned, gotchas hit, constraints the user stated. This is often the richest source.
   > - Run `git log --oneline --since="14 days ago" -- $ARGUMENTS/` and read any project files touched in recent commits that weren't already covered above.
   > - **Mine for new lessons** — don't wait for pre-written tags. As you read changed files, PROJECT.md files, and the conversation, actively ask: what concrete, non-obvious decisions were made? What gotchas are baked in? What patterns emerge? What tools were chosen and why? Surface the insights the author didn't tag.
   >
   > Produce a SINGLE fully rewritten `ROLE.md` that:
   >
   > - **Integrates** genuinely new, concrete, role-worthy lessons mapped to the right section:
   >   `[LESSON]` → Best practices | `[PATTERN]` → Patterns | `[GOTCHA]` → Gotchas | `[TOOL]` → Tools / stacks.
   >   Each promoted bullet ends with `_(from $ARGUMENTS/<path>)_`.
   > - **Dedupes**: collapse near-duplicate bullets into one sharper line. Never two bullets saying essentially the same thing.
   > - **Declutters**: delete stale, superseded, contradicted, vague, or bloated entries. Tighten wordy bullets. If a new lesson supersedes an old one, replace it — don't stack.
   > - **Preserves** every UNIQUE piece of substantive knowledge. Do not drop a real lesson just to shorten.
   > - Keeps section order and the "How to contribute" footer intact.
   > - Refreshes the `Last reconciled:` header date to today.
   >
   > **Rebuild the Index of where everything lives** (the section after "Open questions"):
   >
   > Walk every project directory under `$ARGUMENTS/` that has a `memory/PROJECT.md`. For each project:
   > - Read its `memory/PROJECT.md`.
   > - Identify the 1–3 most important entry-point files (e.g. `main.py`, `service.py`, `scrape.py`, the primary config, the key notebook).
   > - Write one row per "task a future session might need to do" that traces to a specific file:
   >
   >   | If you need to... | Look at | Why |
   >   |---|---|---|
   >   | do X | `$ARGUMENTS/<project>/<file>` | one-line reason — what makes this file the answer |
   >
   > - Also write a **Key files across projects** list:
   >
   >   ```
   >   - `$ARGUMENTS/<project>/` — one-sentence description of the project
   >     - `<entry-point-file>` — what it does
   >     - `<config-or-memory-file>` — what it does
   >   ```
   >
   > Aim for completeness over brevity: every active project under `$ARGUMENTS/` should appear. Remove rows for projects whose folders no longer exist on disk. Keep the section compact — one row per useful "find it" task, not one row per file.
   >
   > Before writing: copy old `$ARGUMENTS/memory/ROLE.md` → `$ARGUMENTS/memory/ROLE.md.bak` (overwrite any prior bak).
   > Then write the rewritten content to `$ARGUMENTS/memory/ROLE.md`.
   >
   > Return exactly one line: `added=<N> merged=<M> removed=<R> old_lines=<L1> new_lines=<L2>`

3. **Shrink-guard** (you, main thread, after subagent returns): parse the subagent's summary line. If `new_lines < 0.60 * old_lines`, the reconcile was too aggressive — restore `$ARGUMENTS/memory/ROLE.md.bak` over `$ARGUMENTS/memory/ROLE.md` and report:
   `REVERTED: $ARGUMENTS reconcile shrunk <L1>→<L2> lines (<pct>% of original), which is below the 60% floor. Inspect ROLE.md.bak to debug.`
   Then stop. Otherwise, keep the new file.

4. **Drain `_pending.md`**: open `$ARGUMENTS/memory/_pending.md`. Remove all stub-queue lines (the "N files changed" / path-list pattern) AND the "session transcript at ..." pointer lines (you've now mined them). Preserve any hand-written non-stub real bullets (leave them, they haven't been promoted yet). Append one line at the bottom:
   `_Drained <today ISO date>. Reconciled into ROLE.md._`

5. **Stamp the throttle marker** so the SessionStart hook won't re-fire for 7 days:
   ```
   python3 -c "import json,datetime,pathlib; pathlib.Path('$ARGUMENTS/memory/.last_promote').write_text(json.dumps({'ts': datetime.datetime.now().timestamp(), 'iso': datetime.datetime.now().isoformat()}))"
   ```

6. **Report** one line:
   `$ARGUMENTS reconciled: +<A> added, ~<M> merged, -<R> removed (<L1> → <L2> lines).`

## Constraints

- Uses the session's own model + a sonnet subagent — **no `ANTHROPIC_API_KEY` needed**.
- All paths are repo-relative (cwd = repo root). Never use absolute `/Users/...` paths — this ships to other people's machines.
- Idempotent: running twice with no new activity should be a near-no-op (subagent finds nothing new, line count stable, shrink-guard passes).
- Never fabricate. Every promoted bullet must trace to real file content the subagent actually read.
- Run this as a quick background step; do not derail whatever the user originally asked for.
