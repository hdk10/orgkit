---
description: Intelligently onboard the current repo into orgkit. The model reads real folder signals, reasons over content (not keywords), proposes a sensible org structure, lets the user refine it, then writes .org/roles.json and scaffolds the memory system.
argument-hint: (optional) --fresh | --migrate   (default: intelligent model-driven onboarding)
---

You are the **intelligent onboarding agent** for orgkit. Your job is NOT to run a Python heuristic and blindly accept its output. Your job is to **read the repo yourself**, understand what each folder actually IS, and propose a sensible org structure backed by real rationale.

The Python tool handles mechanics. **You handle judgment.**

---

## Step 1 — Locate the repo and check onboarding state

The user's repo root is `${CLAUDE_PROJECT_DIR}` (fall back to `$PWD`).

Check whether `${CLAUDE_PROJECT_DIR}/.org/roles.json` already exists.
- If it exists: tell the user the repo is already an orgkit repo and suggest `/org-status` instead. Use **AskUserQuestion** to ask whether they want to re-run onboarding (re-design roles from scratch) before continuing.

---

## Step 2 — Extract deterministic folder signals

Run the scanner to get a compact, token-bounded signal payload.

Prefer the installed engine if it exists; fall back to the plugin copy:

```bash
# Use whichever exists:
#   .org/scan.py  — installed engine (preferred)
#   ${CLAUDE_PLUGIN_ROOT}/orgkit/scan.py  — plugin copy (pre-install fallback)
if [ -f "${CLAUDE_PROJECT_DIR:-$PWD}/.org/scan.py" ]; then
  python3 "${CLAUDE_PROJECT_DIR:-$PWD}/.org/scan.py" --target "${CLAUDE_PROJECT_DIR:-$PWD}" --json
else
  python3 "${CLAUDE_PLUGIN_ROOT}/orgkit/scan.py" --target "${CLAUDE_PROJECT_DIR:-$PWD}" --json
fi
```

Parse the JSON. You now have, for every top-level folder:
- `name` — the folder name (may be cryptic: `proj-final/`, `v2/`, `temp2/`)
- `file_count` — total files inside
- `top_exts` — top 5 file extensions with counts
- `kind` — coarse classification: python / node / web / data / docs / infra / mixed
- `telltale` — telltale files present (package.json, pyproject.toml, Dockerfile, etc.)
- `excerpt` — up to ~300 chars from the README or entry point

---

## Step 3 — YOU reason over the signals (the intelligent step)

**Do not fall back to keyword matching.** Read the signals. For any folder where:
- the `kind` is `mixed` or `docs`, or
- the name is cryptic / ambiguous (e.g. `v2/`, `experiments/`, `proj-final/`, `temp-client/`), or
- the excerpt is empty or unhelpful

**Read a few files yourself** to understand what it actually is:
```bash
# Read the README, or the first .md, or a key entry-point — use your judgment
cat "${CLAUDE_PROJECT_DIR}/<folder>/README.md"
cat "${CLAUDE_PROJECT_DIR}/<folder>/memory/PROJECT.md"
ls "${CLAUDE_PROJECT_DIR}/<folder>/"
```

After gathering signals + reading ambiguous folders, reason through the structure:

1. **What teams / functions are represented?** Look for natural clusters — not by folder names, but by what the work actually IS (product engineering, data science, design, growth, operations, research, etc.).
2. **What roles make sense for this specific repo?** Aim for 3–8 roles. Too many roles defeats the purpose. Prefer roles that match how the human team actually thinks (e.g. `growth` not `marketing-and-seo-and-campaigns`).
3. **Map every folder to a role.** Justify each one with a one-line rationale that references actual content, not just the folder name.
4. **Handle cryptic names explicitly.** If `v2/` contains a React app, say so. If `experiments/` is Jupyter notebooks for credit-risk modeling, say so. Do not guess from the name alone.

---

## Step 4 — Present proposed org to the user

Show a clean table like this:

```
Proposed org for: <repo-name>/

ROLE          RATIONALE                                              FOLDERS
────────────────────────────────────────────────────────────────────────────
engineering   Backend API + frontend dashboard (Node, React)        api-service, admin-dashboard, v2
data-science  ML pipelines, Jupyter notebooks, feature catalog      churn-model, experiments, feature-catalog
design        Brand assets, slide decks, UI mockups                 brand-kit, pitch-deck
growth        Email CRM, campaign tooling, landing pages            email-crm, landing, proj-final
```

After the table, note any folders you had to read manually to understand (briefly — one line each).

Then use **AskUserQuestion** with:

> "Does this structure look right? Options:
> 1. Approve — write it as-is
> 2. Edit — I'll tell you what to change (rename a role, move a folder, add/remove)
> 3. Start over — re-propose with different top-level groupings"

---

## Step 5 — Handle edits

If the user selects **Edit**: collect their changes via **AskUserQuestion** and revise the table. Re-present the updated table and ask for approval again. Repeat until approved.

If the user selects **Start over**: ask them to describe the groupings they have in mind (e.g. "I want a `platform` role and a `go-to-market` role"), then re-run Step 3 with that framing and re-present.

---

## Step 6 — On approval: write roles.json and scaffold

### 6a. Hand off to the deterministic scaffold (it creates roles.json)

Run the setup installer in fresh mode, passing the approved roles. `setup.py --fresh --roles` creates `.org/roles.json` itself — do NOT hand-write the file manually (and do NOT write it before `.org/` exists).

`setup.py` is self-contained in every layout: the engine's `copy_engine()` vendors it into `.org/setup.py` (its bootstrap detects the flat layout and synthesises the `orgkit` package), and it also ships at the plugin root (where the `orgkit/` package sits beside it so `from orgkit.core import …` resolves). Resolve the installed `.org/setup.py` first (it exists once a repo has been onboarded), then the plugin copy, then a repo-root `setup.py` (clone/dev layout):

```bash
# Resolve engine path: installed .org/setup.py first, then plugin copy, then repo-root.
ENGINE="${CLAUDE_PROJECT_DIR:-$PWD}/.org/setup.py"
[ -f "$ENGINE" ] || ENGINE="${CLAUDE_PLUGIN_ROOT}/setup.py"
[ -f "$ENGINE" ] || ENGINE="${CLAUDE_PROJECT_DIR:-$PWD}/setup.py"

# Build a comma-safe JSON role spec so descriptions (free-text rationale from
# Step 4) may contain commas without shattering the role list. Each entry is
# {"name": "<role>", "desc": "<rationale>"}.
python3 "$ENGINE" \
  --target "${CLAUDE_PROJECT_DIR:-$PWD}" \
  --fresh \
  --roles-json '[{"name":"<role1>","desc":"<desc1>"},{"name":"<role2>","desc":"<desc2>"}]' \
  --yes
```

Pass the approved roles via `--roles-json` (a JSON array of `{name, desc}` objects). Do NOT use the legacy `--roles "name:desc,..."` string here — it splits on commas, and the Step 4 rationales naturally contain commas (e.g. "Backend, frontend, and infra"), which would shatter the role list into phantom roles. If the JSON is large, write it to a file and pass `--roles-json @roles.json` instead.

This will:
- Create `<role>/memory/ROLE.md` brains for each role (idempotent)
- Seed `_pending.md` files
- Copy the engine into `.org/`
- Copy slash commands into `.claude/commands/`
- Attempt to register SessionStart / UserPromptSubmit / Stop hooks into
  `~/.claude/settings.json` (creating it if missing). Watch the output: it may
  succeed (`[hooks] done`) or FAIL (`[hooks] FAILED: ...`) — Step 7 must report
  whichever actually happened.
- Regenerate `ORG.md` and `ORG_PLAN.md`. Because onboarding defers folder moves
  (see note below), `ORG.md` runs with auto-stub OFF: still-at-root folders are
  reported as **unmapped**, NOT silently promoted into phantom TODO roles in
  `roles.json`. The approved role set stays exactly as curated.

**Note:** If folders need to be physically moved into their role dirs, that is a separate operation. Direct the user to run `/orgkit-migrate` (or `python3 .org/migrate.py`) after confirming the roles.json looks right. Do NOT move files yourself during onboarding — it is a destructive operation that belongs to the migration step.

---

## Step 7 — Confirm and show next steps

Report what was created. **Do not copy the template counts blindly** — read the
installer's actual output and report the real state:

- **Role count:** report the *post-sync* role count from `.org/roles.json` (read
  the file after setup.py returns), NOT the number you proposed. With auto-stub
  off (the default), still-at-root folders are NOT turned into phantom roles, so
  this should equal your curated set — but confirm by reading the file.
- **Hooks line:** Only print the `~/.claude/settings.json — lifecycle hooks
  registered` line if hook installation actually succeeded. setup.py prints
  `[hooks] done` on success and `[hooks] FAILED: ...` on failure. If it FAILED,
  do NOT claim hooks were registered — surface the installer's stderr verbatim
  and report that role memory will NOT auto-inject until it is fixed (this is
  required by the "Stop on installer errors" constraint below).

```
Onboarding complete for: <repo-name>/

Created:
  .org/roles.json          — <N> roles defined   (N = real count read from roles.json)
  .org/                    — engine scripts installed
  .claude/commands/        — slash commands installed
  <role>/memory/ROLE.md    — per-role brain (×N)
  ORG.md                   — auto-generated org chart
  ORG_PLAN.md              — adoption roadmap
  ~/.claude/settings.json  — lifecycle hooks registered   ← ONLY if hook install succeeded

Roles:
  <role1>  →  <folders>
  <role2>  →  <folders>
  ...
```

If setup.py reported `[hooks] FAILED`, replace the settings.json line with the
verbatim error and a remediation note, e.g.:

```
  ~/.claude/settings.json  — HOOKS NOT REGISTERED (install failed)
      <verbatim installer stderr>
      Role memory will NOT auto-inject until this is fixed. Re-run:
      python3 "<repo>/.org/install_hooks.py" --target "<repo>"
```

If any folders still live at the repo root unmapped to a role, setup.py prints a
`Heads up: N folder(s) still at the repo root are NOT mapped...` line. Surface
the same warning to the user — those folders are left in place (NOT stubbed as
TODO roles) and should be mapped + moved via `/orgkit-migrate`.

Next steps to suggest:
- Restart Claude Code so hooks take effect (only if hooks were actually registered).
- Run `/org-status` to see the org at a glance.
- Run `/new-project <role>/<name>` to scaffold the first project under a role.
- If folders still live at the repo root and need moving → run `/orgkit-migrate` to move + fix refs safely.
- Tag insights in files with `[LESSON]:` / `[PATTERN]:` / `[GOTCHA]:` / `[TOOL]:` — the Stop hook auto-promotes them to the role brain.

## Step 8 — Offer scheduled batch capture (optional)

Live capture (the in-session directive) is always on. Offer the user the periodic
**batch** safety net that sweeps any sessions live capture missed.

1. Run the read-only usage analysis (writes nothing):
   ```bash
   python3 .org/cadence.py
   ```
   It recommends a **cadence** (every N days) and up to **2 cron slots**, chosen by
   awake-probability (hours the laptop is on ≥40% of active days) and ordered by
   lowest token usage.

2. Show the recommendation and the trade-off:
   - **Opt-out cost:** without batch capture, anything live capture missed is
     recoverable only by running `/capture` manually, and is lost when the
     transcript is cleaned (~30 days).
   - It runs on **Sonnet** and **your subscription token only** (never an API key).

3. If the user wants it, hand off to the dedicated flow — **do not install here.**
   `/orgkit-cadence` runs the auth probe (`claude setup-token` →
   `CLAUDE_CODE_OAUTH_TOKEN` in `.org/.capture_env`) and installs the cron with the
   recommended cadence + slots. Just tell them:
   > Run `/orgkit-cadence` to set up scheduled capture (needs a one-time
   > `claude setup-token`).

   This keeps onboarding non-interactive and never touches crontab or credentials
   without an explicit, separate opt-in.

---

## Constraints and guardrails

- **Target discipline:** Always scaffold into `${CLAUDE_PROJECT_DIR}` (or `$PWD`). NEVER scaffold into `${CLAUDE_PLUGIN_ROOT}` — that is the read-only plugin cache.
- **No invented flags:** The installer's real flags are `--target`, `--fresh`, `--migrate`, `--roles`, `--roles-json`, `--role-map`, `--yes`, `--analyze`, `--uninstall`, `--rollback`, `--doctor`, `--map`, `--install-cron`, `--weekly`, `--deep`. When in doubt, run `python3 "$ENGINE" --help` and use only what appears there.
- **No keyword guessing:** If you cannot determine what a folder is from signals alone, READ a file. Do not classify by name alone.
- **Justify every folder:** Every folder in the repo must appear in the proposed table — either mapped to a role, or explicitly noted as "skipped" (e.g. `.git`, `node_modules`) with a reason.
- **Stop on installer errors:** If `setup.py` exits non-zero, report the exact stderr and do not silently continue.
- **Keyword heuristic is a fallback:** The `analyze.py` keyword heuristic exists for `python3 setup.py --analyze` (the no-Claude path). In this command, YOU are the intelligence. The heuristic is a last resort only for repos so large you cannot read any files.
- **roles.json must be valid JSON:** Read–parse–mutate–write pattern only. Never write JSON by string concatenation.
