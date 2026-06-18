---
description: Intelligent, read-only preview of how orgkit would organize THIS repo. The model reads your actual folders (not keywords), proposes a sensible org with rationale and an estimated token saving, and writes nothing. The "look before you leap" twin of /orgkit-init.
---

You are giving the user an **intelligent, read-only preview** of how orgkit would organize their repository. This is the teaser people run first — so it must show the *real* intelligence, not a keyword guess. **You read the repo and reason; you write nothing.**

The Python tool handles mechanics. **You handle judgment.** This command is exactly `/orgkit-init`'s reasoning, minus any writing.

---

## Step 1 — Extract deterministic folder signals

The user's repo root is `${CLAUDE_PROJECT_DIR}` (fall back to `$PWD`). Get the token-bounded signal payload.

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

Parse the JSON. For each top-level folder you get: `name`, `file_count`, `top_exts`, `kind` (python/node/web/data/docs/infra/mixed), `telltale` files, and a ~300-char `excerpt`.

---

## Step 2 — YOU reason over the signals (the intelligent step)

**Do not fall back to keyword matching.** For any folder where the `kind` is `mixed`/`docs`, the name is cryptic (`v2/`, `proj-final/`, `temp2/`, `experiments/`), or the excerpt is unhelpful — **read a file or two yourself** to learn what it actually is:

```bash
cat "${CLAUDE_PROJECT_DIR}/<folder>/README.md" 2>/dev/null
ls "${CLAUDE_PROJECT_DIR}/<folder>/"
```

Then reason through the structure:
1. **What functions are actually represented?** Cluster by what the work *is* (product engineering, data science, design, growth, ops, research…), not by folder names.
2. **What 3–8 roles fit THIS repo?** Match how the team actually thinks. Too many roles defeats the purpose.
3. **Map every folder to a role**, each with a one-line rationale grounded in real content.
4. **Resolve cryptic names explicitly** — if `v2/` is a React app, say so; if `experiments/` is credit-risk notebooks, say so.

---

## Step 3 — Estimate the token saving (honest)

From the scan signals, estimate per-session context cost two ways:
- **Dump-everything**: global + all role brains would load every session.
- **Scoped**: global + only the role you're in.

You don't have real ROLE.md content yet (they don't exist), so use folder/size signals as a rough proxy and state it as an **estimate that scales with role count**: with N roles, scoped loads roughly `1/N` of the role memory. Be honest — e.g. "with ~5 roles, expect ~70–80% less per session; with 2 roles, ~30–40%." Never claim a flat 85%.

---

## Step 4 — Present the preview (and stop)

Show a clean table:

```
Preview — how orgkit would organize: <repo-name>/   (nothing has been changed)

ROLE          RATIONALE                                          FOLDERS
──────────────────────────────────────────────────────────────────────────
engineering   Backend API + React dashboard (Node, TS)          api-service, admin-dashboard, v2
data-science  ML pipelines + credit-risk notebooks              churn-model, experiments
design        Brand assets + slide decks                        brand-kit, pitch-deck
growth        Email CRM + landing pages                         email-crm, landing
```

Then:
- One line per folder you had to read manually to classify ("`v2/` → React app, by its `package.json` + `src/App.tsx`").
- The token-saving estimate, framed honestly with its assumption.
- A note that **nothing was written** — this was read-only.

Finally, use **AskUserQuestion**: "Want to set this up for real? → run `/orgkit-init` (it re-confirms with you before writing anything). Or `/orgkit-init` then `/orgkit-migrate` if folders also need physically moving."

---

## Constraints

- **WRITE NOTHING.** No `roles.json`, no scaffolding, no hooks, no file moves. This command is purely a preview. If you catch yourself about to write, stop.
- **Be the intelligence.** Do not shell out to `setup.py --analyze` (that's the keyword-heuristic fallback for the no-Claude path). Read folders and reason, exactly like `/orgkit-init` does.
- **Read, don't guess.** If signals are ambiguous, open a file. Never classify a folder by its name alone.
- **Honest numbers.** The token saving is an estimate that grows with role count — say so; never a flat guaranteed percentage.
- **Target discipline:** operate on `${CLAUDE_PROJECT_DIR}` / `$PWD`, never `${CLAUDE_PLUGIN_ROOT}`.
