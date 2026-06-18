---
description: Model-driven lesson capture — read recent work in a role, distill genuine insights, append tagged bullets to _pending.md. The real capture path; complements the Stop-hook's cheap regex fast-lane.
argument-hint: [role]
---

You are capturing lessons from recent work in the **$ARGUMENTS** role's projects. This is **model-driven** distillation — you read actual changed files and synthesise concrete, specific insights. No tag-hunting; no generic platitudes.

## Why this exists

The Stop hook (`role_digest.py`) only regex-scrapes pre-written `[LESSON]/[PATTERN]/[GOTCHA]/[TOOL]` tags. Nobody writes those tags consistently, so that fast-lane is usually empty. `/capture` is the real path: the model reads actual work output **and the session conversation** and distills lessons from both, then feeds them into `_pending.md` for `/role-promote` to reconcile.

**The richest lessons usually aren't in the code diff — they're in the conversation** ("we tried X, it broke because Y, so we switched to Z"). The Stop hook records a pointer to the session transcript in `_pending.md`; this command reads that transcript so the *reasoning*, *decisions*, and *dead-ends* get captured, not just the final code.

## Steps

1. **Validate role.** Read `.org/roles.json`. If `$ARGUMENTS` is empty or not listed under `roles`, print the valid role names and stop.

2. **Find recent work.** In parallel, gather signals:

   a. Read `$ARGUMENTS/memory/_pending.md` — the stub queue contains lines like  
      `[timestamp] <role>: N files changed (path1, path2, ...) — run /capture then /role-promote`  
      Extract the file paths listed there (these are the files touched since last digest).

   b. Run `git -C . log --oneline --since="14 days ago" -- "$ARGUMENTS/"` to find recent commit messages (quick signal, not the main source).

   c. Run `git -C . diff --name-only HEAD~10 HEAD -- "$ARGUMENTS/" 2>/dev/null || git -C . diff --name-only HEAD~5 HEAD -- "$ARGUMENTS/"` to find recently changed files not yet in _pending.md (fall back gracefully if the range doesn't exist).

   d. **Extract transcript pointers.** Scan `_pending.md` for lines like  
      `<role>: session transcript at <path> — /capture to mine the conversation`  
      Collect those transcript `<path>` values (the Stop hook records them). These are the session conversations to mine — this is where the "why" lives.

   Combine and deduplicate the file lists from (a) and (c). Skip binary files, venvs, `__pycache__`, `.db`, `.csv`, `.xlsx`, `.parquet`, lock files, and anything under `memory/` (to avoid feedback loops). Skip files that don't exist.

3. **Read the source material — code AND conversation.**

   **Code:** for each changed file identified:
   - Read the file directly.
   - If a changed file has a sibling `memory/PROJECT.md`, read that too — it often contains the most condensed context.
   - Also read `$ARGUMENTS/memory/ROLE.md` so you can deduplicate against what's already there.

   **Conversation (the important part):** for each transcript path from 2(d), run:
   ```bash
   python3 .org/transcript.py "<transcript-path>"
   ```
   This emits the clean USER/ASSISTANT dialogue (tool-call noise stripped, token-bounded). Read it and look for the things a diff can never show: **decisions and their rationale, trade-offs weighed, approaches tried and abandoned, gotchas hit and how they were resolved, constraints the user stated.** This is usually the richest source of lessons.

   > **Caveat:** transcript pointers in `_pending.md` only exist if `role_digest.py` is installed as a Stop hook AND files changed during the session that wrote those entries. If no pointers are found, skip step 2(d) silently, proceed with diffs/files alone, and note in your report: "No transcript pointers found — capturing from diffs and files only."

   If there are no changed files AND no transcript, report: `No recent work found in $ARGUMENTS/ — nothing to capture.` and stop.

4. **Distill genuine lessons.** Think carefully about what you just read. For each insight ask:
   - Is this **specific and concrete**? ("always use `httpx.AsyncClient` with `http2=True` for fan-out; sync client-per-call costs ~65 ms extra TLS overhead per call" is good. "use good libraries" is not.)
   - Is this **non-obvious**? Something a future session would actually benefit from knowing?
   - Is this **new** — not already present in ROLE.md in substance?
   - Does it trace to something **actually in the files** read, not general knowledge?

   Only keep insights that pass all four checks. It is fine — good, even — to find zero new insights if the work didn't surface anything non-obvious.

   Insights may come from the **code** (attribute `_(from `$ARGUMENTS/<path>`)_`) or from the **conversation** (attribute `_(from session conversation, <ISO date>)_`). Conversation-sourced lessons — a decision and its reason, an approach that failed, a constraint the user insisted on — are often the most valuable; capture them.

   Classify each insight:
   - `[LESSON]` — a concrete best practice or convention that worked
   - `[PATTERN]` — a reusable approach or architecture decision
   - `[GOTCHA]` — a subtle, non-obvious thing that bit you or that you avoided by knowing it
   - `[TOOL]` — a library, CLI, or service that's genuinely useful — include what it does and any quirks

5. **Deduplicate against ROLE.md.** For each candidate insight, check if the substance is already in `$ARGUMENTS/memory/ROLE.md`. If it is, skip it (or note it's already there). Near-duplicates count — don't add "use venvs per project" if ROLE.md already says "per-project venv for every Python project".

6. **Append to `_pending.md`.** If there are new insights:

   Open `$ARGUMENTS/memory/_pending.md` and append a block like:

   ```
   <!-- /capture run <ISO date> -->
   [LESSON]: <specific concrete lesson> _(from `$ARGUMENTS/<path>`)_
   [GOTCHA]: <the non-obvious thing> _(from `$ARGUMENTS/<path>`)_
   [PATTERN]: <reusable approach> _(from `$ARGUMENTS/<path>`)_
   [TOOL]: <library/CLI — what it does, any quirks> _(from `$ARGUMENTS/<path>`)_
   ```

   Only append the tag types that actually have new content. Do not fabricate placeholders.

   If there are **no new insights** (either no changed files, or all insights already in ROLE.md), append one line:
   ```
   <!-- /capture run <ISO date> — no new insights found -->
   ```
   and say so clearly in your report.

7. **Report.** Output a concise summary:
   - Files read (count + list)
   - Insights captured (count, each on one line with its tag and source)
   - Insights skipped as already-in-ROLE.md (brief mention)
   - Whether `/role-promote $ARGUMENTS` is now worth running

## Constraints

- **Never fabricate.** Every bullet must trace to something actually read in step 3.
- **Concrete over volume.** Three sharp bullets beat ten vague ones. If in doubt, leave it out.
- **All paths are repo-relative.** Never use absolute `/Users/...` paths.
- **Do not modify ROLE.md directly.** Write to `_pending.md` only. Let `/role-promote` do the reconcile and dedup pass before anything lands in ROLE.md.
- **Do not write into `memory/` files for other roles** — only `$ARGUMENTS/memory/_pending.md`.
- This command uses the session's own model — **no `ANTHROPIC_API_KEY` needed**.
