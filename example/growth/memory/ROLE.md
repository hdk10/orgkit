# growth Role Memory

_Auto-injected into Claude Code sessions inside `growth/`. Last bootstrapped: 2026-06-01. Last reconciled: 2026-06-18._

## Mission

GTM execution layer — produces partner one-pagers, maintains the content calendar, and operates the audience upload tooling used to activate segments for paid campaigns. Everything here is externally facing and must reflect the current brand.

---

## Projects in this role

| Project | What it is |
|---|---|
| **partner-onepagers** | PDF one-pagers pitched to each prospective partner; built via report-generator (design role); source markdown in `input/<partner_slug>/brief.md` |
| **audience-uploader** | Desktop GUI for uploading CSV audiences to ad platforms (Meta Custom Audiences, Google Customer Match); token lifecycle management |
| **content-calendar** | Shared Markdown + CSV tracking content briefs, publish dates, and performance notes |

---

## Best practices

- **Always generate partner one-pagers from the report-generator skill** (design role) — never hand-edit the PDF directly. Edit `input/<slug>/brief.md`, re-run the skill.
- **Pin Meta Graph API version in `.env`** (`META_API_VERSION`). Unpinned calls default to an older version and may behave differently. Update version quarterly.
- **Token lifecycle management** — Meta and Google tokens expire; `token_manager.py` tracks validity and marks tokens expired on auth errors. Always check token status before upload batches.
- **Custom Audience TOS** — TOS must be accepted by the BRAND ad-account admin at the platform's business manager UI, not by the dev or agency. Gate create/upload routes with a TOS check before calling the API.
- **Business Verification before App Review** — start it FIRST for any Meta app; takes 2–5 days and blocks App Review.
- **Gitignore `tokens.json`** — may contain live API tokens. Confirm it is not accidentally staged in any future `git add`.

---

## Gotchas

- `[audience-uploader]` — Meta app type must be **Business** for the Marketing API product to be addable. Self-connecting the company's own Business account via OAuth is blocked when that account owns a BISU (Business Integration System User); test with a separate business account.
- `[audience-uploader]` — Google Ads `membership_life_span` for Customer Match lists is capped at 540 days. Values > 540 return a silent API error during testing.
- `[audience-uploader]` — Google Ads lookalike lists are read-only via API — cannot be removed or closed programmatically. Create + detect only; instruct clients to delete from UI.
- `[audience-uploader]` — OAuth `adwords` + `datamanager` scopes require brand verification + justification + demo video; no CASA security assessment needed. Consent screen stays "Testing" (≤ 100 users) until approved.
- `[partner-onepagers]` — Source content in `input/<slug>/brief.md` is the single source of truth. Any manual edits to the rendered PDF will be overwritten on the next run. Document all content decisions in the brief, not the PDF.

---

## Tools / stacks

- **report-generator** (design role) — one-pager PDF generation; invoke via skill
- **Meta Graph API** (httpx async) — Custom Audiences create/upload/delete/lookalike
- **Google Ads API** (google-ads Python lib, sync → `run_in_threadpool`) — Customer Match create/list/share
- **Google Data Manager API** (`datamanager.googleapis.com`, raw httpx) — Customer Match member population (post-Apr 2026 replacement for the retired `OfflineUserDataJobService`)
- **pywebview** — desktop GUI shell for audience-uploader
- **pandas** — CSV validation and transformation before upload

---

## How to contribute

- Write inline `[LESSON]: <text>` / `[PATTERN]: <text>` / `[GOTCHA]: <text>` / `[TOOL]: <text>` in any `memory/PROJECT.md` — the Stop hook scrapes these and appends to this ROLE.md automatically.
- Deeper insights auto-reconciled by `/role-promote growth`.
