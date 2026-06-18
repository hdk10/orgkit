# pitch-deck — Project Memory

_Last updated: 2026-06-18_

## What it is

10-slide self-contained HTML seed pitch deck (1440×810 px, Tailwind CDN, no build step). Used for investor and enterprise buyer conversations. Single `deck/deck.html` file; served locally via `python3 -m http.server 8765`.

## Status

- v4 locked and approved (10 slides).
- PDF export working with local font TTFs.
- Open: slide 8 (competitive landscape) content needs update.

## Serving

```bash
cd deck
python3 -m http.server 8765
# open http://localhost:8765/deck.html
```

## PDF export

```bash
# Uses local Lato + Kulim Park TTFs — NOT CDN fonts
python3 export_pdf.py  # headless Chromium via Playwright
```

**Do NOT use CDN fonts for PDF export.** Google Fonts loads fine in the browser but fails in headless Chromium. Always use local `@font-face` TTFs for any PDF export path.

## Slide inventory

| Slide | Title |
|-------|-------|
| 1 | Cover — Company name + tagline |
| 2 | The problem |
| 3 | Our solution |
| 4 | How it works (3-step diagram) |
| 5 | Traction + metrics |
| 6 | Why now |
| 7 | Team |
| 8 | Competitive landscape ← needs update |
| 9 | Business model |
| 10 | Ask + use of funds |

## Versioning

- Minor visual tweaks: edit `deck.html` in-place; note in commit message.
- Content/structure changes: copy to `versions/v<N>/deck.html` before editing.
- **Never edit `versions/vN/` in place** once archived.

## Content spec

`reference/content-spec.md` is the single source of truth for all slide content. Edit here first, then mirror into `deck.html`. Never directly edit content in HTML without updating the spec.

---

## Lessons captured inline

[GOTCHA]: Google Fonts CDN (fonts.googleapis.com) loads fine in browser but NOT in headless Chromium during PDF export. First PDF export attempt produced an all-fallback-font output. Fix: download Lato + Kulim Park TTFs into `deck/fonts/` and reference via `@font-face` for the export stylesheet.

[PATTERN]: HTML iteration-first, PDF export last. Do all visual refinement in-browser; sign off with the user; then run the one-shot PDF export. Exporting after every iteration wastes time and creates confusion about which PDF is current.

[LESSON]: `flex-1` / `mt-auto` on cards with short content creates a dead-space void inside the card, making it look half-empty. Fix: use `justify-content: center` on the card container so whitespace is distributed equally above and below the content.

[GOTCHA]: Memory drift in long-running deck projects corrupts ground truth. When the user provides a reference screenshot or PDF mid-project, treat THAT as the new source of truth and reconcile `content-spec.md` against it — not the other way around.
