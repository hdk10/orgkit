# design Role Memory

_Auto-injected into Claude Code sessions inside `design/`. Last bootstrapped: 2026-06-01. Last reconciled: 2026-06-18._

## Mission

Design is the presentation and document layer: pitch decks, client-facing PDF reports, and the document-generation skill (report-generator) that produces all of the above from structured input in one shot. Everything in this role targets high-stakes external recipients — executives, investors, enterprise buyers — so pixel precision and brand fidelity are non-negotiable.

---

## Projects in this role

| Project | What it is |
|---|---|
| **report-generator** | The primary skill: converts any markdown input → branded PDF (A4 report, 16:9 deck, one-pager, PRD) via Jinja2 + Playwright. Self-validates via Claude vision. Status: built and functional. |
| **pitch-deck** | 10-slide self-contained HTML seed pitch deck (1440×810, Tailwind CDN, no build). Single `deck/deck.html` file, served via `python3 -m http.server 8765`. |
| **brand-assets** | Fonts, logos (trimmed PNGs), color token files, and reusable SVG icons. Source of truth for all brand materials. |

---

## Best practices

- **Read `report-generator/memory/PROJECT.md` before any report-generator work.** It has the component inventory, Pydantic schema, agent workflow (4 phases), and self-validation rubric. Do not redo this design work from scratch.
- **Lock all brand tokens to CSS variables.** Never hardcode hex values inside Jinja2 templates — only reference `var(--brand-primary)` etc. Single source of truth in `static/tokens.css`.
- **Screenshot loop is non-negotiable for visual edits.** Run the headless-Chromium loop before and after every visual change.
- **Calculate layout before coding.** Map out available vertical space, section splits, and card heights. If a change touches one dimension, audit every dependent dimension.
- **Intake before generation.** For report-generator: ask all 6 intake questions before generating JSON — never skip even when input seems complete.
- **Pydantic validates before render.** Never call the render pipeline with unvalidated JSON.
- **Silent iteration.** Max 5 validation iterations; never show intermediate renders to the user.
- **Compose for full pages, not one component per page.** Pack 2–4 components per A4 page filling ~70–96% of height. A section is not a page — content volume determines page count.
- **Run `scripts/audit_layout.py` as the layout gate before scoring the rubric.** The pre-render height estimator is unreliable. The layout auditor measures real Playwright-rendered fill % and exits non-zero on SPARSE (< 70%) or OVERFLOW pages.
- **HTML iteration-first, PDF export last for HTML decks.** Full iterative visual refinement in browser → sign-off → PDF export once.

---

## Patterns

- **Stack (report-generator):** HTML + Jinja2 macros + Tailwind CDN + CSS custom-property tokens → Playwright Chromium headless → selectable-text PDF. No Node, no bundler, no build step.
- **Stack (pitch-deck):** Single HTML file + Tailwind CDN via CDN tag + Google Fonts via CDN. Served over `http.server`. PDF export uses local font TTFs.
- **Agent workflow (report-generator):** Claude outputs JSON → Pydantic validates → Jinja2 renders HTML → Playwright captures PDF → Claude vision scores 5-dimension rubric → revise loop (max 5 iterations).
- **Diagram rendering:** Claude infers Mermaid syntax → `mmdc -i diagram.mmd -o /tmp/<id>/diagram_N.svg` → SVG embedded inline before Playwright renders.
- **Cover gradient is inline SVG, not CSS background.** Inline SVG survives PDF export when "background graphics" is off in Chrome/Safari. Never replace with a CSS `background` property for the cover.
- **Accent spectrum is the only way to add color variety.** Multi-column components cycle `accent_index` 0–4 (primary → teal → mint → sky → indigo). Never reach for purple/red/amber as decorative colors; amber is reserved for a single caution callout per doc.
- **Heading fonts: DM Serif (content) vs Kulim Park (identity).** `--font-brand` (DM Serif Display) = section headings, cover title, KPI hero values. `--font-logo` (Kulim Park) = wordmark, uppercase labels, badges, footers. Do NOT swap them.
- **One JSON page = one physical PDF page is a hard architectural invariant.** TOC page-number resolver maps schema page index directly to physical page with no offset adjustment.
- **Two-green rule.** Use `#1A9E78` on white/light backgrounds; use the lighter tint on dark backgrounds.

---

## Anti-patterns

- **Do NOT use LatoBlack for hero/H2 headings.** Use Kulim Park Bold — that is the brand standard.
- **Do NOT hardcode hex values in templates.** Tokens must live in CSS custom properties only.
- **Do NOT invent content.** Every word in the PDF must come from the input. If data is missing, skip the component or add an amber callout noting the gap.
- **Do NOT use random letter-in-circle icons** (P, D, C, O…). Use contextual SVG-generated icons or colored squares. Letter circles look unprofessional.
- **Do NOT show intermediate renders** to the user mid-iteration. Silent validation loop only; deliver on pass or iteration limit.
- **Do NOT use rounded corners on full-width bottom bars.** Use flat rectangles.
- **Delegating visual design rewrites to sub-agents propagates visual blindness.** The sub-agent follows spec, declares success, and ships regressions undetected. Design work must stay in the main conversation with the screenshot loop open.

---

## Gotchas

- `[pitch-deck]` — Google Fonts CDN loads fine in browser but NOT in headless Chromium for PDF export. Fix: use local font TTFs with `@font-face` for any PDF export path.
- `[report-generator]` — Tailwind CDN preflight resets `list-style` to `none` on all `<ul>`. Bare `<ul><li>` in `left_html`/`right_html` renders bullet-free. Fix: use `BulletList` macro or add `list-style-type:disc` inline.
- `[report-generator]` — KpiCards labels longer than ~16 chars wrap to 3 lines even with `min-height` fix, making hero numbers misalign. Keep labels ≤ 16 chars.
- `[report-generator]` — `mmdc` (Mermaid CLI) must be installed separately via npm; it is not a pip dependency. Missing it silently skips diagram rendering.
- `[report-generator]` — `audit_layout.py` starts a Playwright browser. Requires `playwright install chromium`. Do not run in environments without Playwright.
- `[all decks]` — Parent-child height overflow: children inside a card container must fit `container_h - title_space - padding`. Always calculate, never guess.
- `[all decks]` — Number-circle + adjacent text: align text to circle center, not circle top. Misalignment is immediately visible at presentation scale.
- `[all decks]` — Memory drift in long-running deck projects corrupts ground truth. When the user provides a reference screenshot or PDF, treat THAT as the new source of truth and reconcile all memory files against it.
- `[pitch-deck]` — `flex-1` / `mt-auto` on cards with short content creates dead-space void inside the card. Fix: `justify-content:center` on the card so whitespace is equal above and below.

---

## Tools / stacks

**PDF rendering (current):**
- Playwright + Chromium headless — PDF capture from HTML
- Jinja2 — component macro library (`*.html.j2` files)
- Tailwind CSS via CDN — utility-first styling, no build step
- Pydantic — schema validation of Claude JSON output before render
- `@mermaid-js/mermaid-cli` (`mmdc`) — Mermaid source → SVG for diagram blocks

**Fonts (locked locations):**
- Kulim Park Bold/Regular/SemiBold — `brand-assets/fonts/KulimPark-*.ttf`
- Lato (Regular/Bold/Light/Black) — `brand-assets/fonts/`
- JetBrains Mono — monospace for all code chips

**Visual validation:**
- Headless Chromium — per-slide screenshots
- Claude vision — scores output against 5-dimension rubric inside report-generator

---

## Vocabulary / glossary

| Term | Definition |
|---|---|
| **brand tokens** | CSS custom properties in `static/tokens.css`; single source for all colors, fonts, radii |
| **hero** | The dominant large number or title on a page; always Kulim Park Bold |
| **deck slide** | 16:9 page (1440×810 HTML) |
| **A4 report** | Portrait A4 PDF, 2–3 content sections per page, dense data layout |
| **one-pager** | Single A4 page: KpiRow + InsightGrid + CTA |
| **wordmark** | Brand logo text rendered via Kulim Park SemiBold |
| **bottom bar** | Full-width solid-color band at slide bottom with white bold takeaway text; standardized across all slides |
| **code chip** | Inline monospace tag (JetBrains Mono) used for feature/field names in data tables |
| **DarkCard** | Dark background callout card; used for recommendations |
| **AmberCallout** | Amber background callout; used for caveats and warnings |
| **SPARSE** | audit_layout verdict: page fill < 70%; automatic layout FAIL |
| **OVERFLOW** | audit_layout verdict: page content spills past A4 height; automatic layout FAIL |
| **accent spectrum** | 5-stop palette: primary → teal → mint → sky → indigo; systematic color variety |

---

## How to contribute

- Write inline `[LESSON]: <text>` / `[PATTERN]: <text>` / `[GOTCHA]: <text>` / `[TOOL]: <text>` in any `memory/PROJECT.md` or commit message — the Stop hook scrapes these and appends to this ROLE.md automatically.
- Deeper insights auto-reconciled by `/role-promote design` (fired automatically when brain > 7 days stale with pending activity).
