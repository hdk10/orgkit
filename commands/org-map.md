---
description: Render a shareable org-map SVG (ORG_MAP.svg) of this repo's org — roles, projects, captured lessons, token savings. Built to screenshot and post.
---

## Steps

1. Generate the map by running, from the repo root:
   `python3 .org/orgmap.py`
   (or, from a fresh orgkit clone, `python3 setup.py --map --target .`)
2. This writes `ORG_MAP.svg` at the repo root. It reads real state: each role's `ROLE.md` size (brain richness), project folder count, captured-insight count (Best practices + Patterns + Gotchas bullets), and reconcile freshness from each `.last_promote` marker, plus an estimated per-session token saving.
3. Tell the user where it is (`ORG_MAP.svg`) and that it's designed to be shared — open it, screenshot it, post it. It carries a small `made with orgkit` watermark, so every share spreads the tool.
4. If a renderer is available (`rsvg-convert`, `cairosvg`, or a headless Chrome), optionally also produce a PNG for easy posting:
   `rsvg-convert -w 1200 ORG_MAP.svg -o ORG_MAP.png`

## Constraints

- Read-only with respect to memory — it only writes `ORG_MAP.svg` (and optionally `ORG_MAP.png`). It never edits `ROLE.md`, `CLAUDE.md`, or any project memory.
- Pure stdlib; no dependencies needed to generate the SVG.
- If `.org/roles.json` is missing, tell the user to run setup first (`python3 setup.py`).
