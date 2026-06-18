# Roadmap

Honest and near-term. Items leave here when they actually work well. Vote by opening or +1'ing an issue.

## Shipped

So you can see what's real, not promised:

- **Intelligent onboarding** — `/orgkit-init` reads your repos and proposes an org (real reasoning, not keyword matching).
- **Safe migration** — `/orgkit-migrate` moves folders, fixes path refs, has the model fix the imports/relative paths regex can't, and seeds a `ROLE.md` per role + `CLAUDE.md` in one step. Never deletes.
- **Conversation-aware capture** — `/capture` and `/role-promote` mine git diffs *and* the session transcript, not just pre-written tags.
- **Reconcile** — merge / de-dupe / declutter + rebuilds the role's index, protected by a shrink-guard and a `.bak`.
- **Shareable org-map** — `/org-map`.
- **Recovery** — `--analyze` (read-only), `--doctor`, `--uninstall` / `--deep`, `--rollback`. Backups + dry-run throughout.
- **Plugin packaging** + a `tests/smoke.py` end-to-end suite.

## Near-term

| Item | Status |
|---|---|
| CI — GitHub Actions running the smoke suite on PRs + a green badge | planned |
| Windows verification — path separators; `--install-cron` is Unix-only by design | needs testing |
| Fresher capture — optional nudge to run `/capture` in-session, not only at reconcile | planned |
| Reconcile tuning — very large role brains, smarter de-dupe thresholds | ongoing |
| More `--doctor` checks — stale pending queues, orphaned `PROJECT.md`, dormant roles | planned |
| Cleanups — remove dead code paths; wire-or-remove the `templates/*.tmpl` files | planned |

## Medium-term

- **Team / shared role brains** — a committed `ROLE.md` a small team shares. Needs clean merge handling first.
- **Cross-role index** — "which role owns X?" across the whole org.
- **Marketplace discovery** — get listed where Claude Code users look for plugins.

## Not planned (on purpose)

- **External dependencies.** Stdlib-only is the point: runs anywhere Python runs, zero install friction.
- **Vector / semantic search.** That's mem0 / Letta territory. orgkit is structured, curated, role-scoped memory. Use both if you need both.
- **A web UI.** `/org-status`, `/org-map`, and `--analyze` are the dashboard, right in your terminal and Claude Code.

→ See [CONTRIBUTING.md](CONTRIBUTING.md) to get involved.
