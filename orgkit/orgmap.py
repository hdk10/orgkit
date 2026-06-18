#!/usr/bin/env python3
"""orgmap.py — render the org as a shareable SVG org chart.

Reads roles.json + each role's ROLE.md + project folders and emits a refined,
screenshot-ready org chart to <repo>/ORG_MAP.svg. Pure stdlib (SVG is just text)
— no rendering dependencies for the user.

Layout is a real top-down org chart: a Global brain node at the top, role
"departments" on lanes below it, and project sub-nodes beneath each role, joined
by hairline connectors. Refined editorial styling (warm paper, deep-green accent).

Usage:
  python3 .org/orgmap.py [--target PATH] [--out PATH] [--title STR]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    from core import (
        detect_repo_root, load_roles, role_md_path, role_memory_dir,
        global_claude_md_path, read_marker_ts,
    )
except ImportError:
    from orgkit.core import (
        detect_repo_root, load_roles, role_md_path, role_memory_dir,
        global_claude_md_path, read_marker_ts,
    )

STALE_DAYS = 7

SERIF = "'Iowan Old Style', 'Palatino Linotype', Palatino, Georgia, 'Times New Roman', serif"
SANS = "ui-sans-serif, -apple-system, 'Segoe UI', Inter, Roboto, Helvetica, Arial, sans-serif"
MONO = "ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, monospace"

# Single editorial palette (warm paper, deep green accent).
PALETTE = {
    "bg": "#F6F4EF", "bg2": "#EFECE4",
    "panel": "#FFFFFF", "panel2": "#FBFAF7",
    "line": "#D8D3C7", "border": "#E0DBD0",
    "ink": "#1B1A17", "muted": "#8C8676", "faint": "#B7B1A3",
    "accent": "#1E6B4F", "accent_soft": "#DCE9E2",
    "fresh": "#1E6B4F", "stale": "#B7791F", "cold": "#B7B1A3",
    "title_font": SERIF,
}


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def _truncate(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _section_count(text: str, header: str) -> int:
    m = re.search(rf"^##\s+{re.escape(header)}.*?$(.*?)(^##\s|\Z)",
                  text, re.MULTILINE | re.DOTALL)
    if not m:
        return 0
    return len(re.findall(r"^\s*-\s+\S", m.group(1), re.MULTILINE))


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _brain_kb(chars: int) -> str:
    if chars <= 0:
        return "empty"
    if chars < 1000:
        return f"{chars}b"
    return f"~{chars // 1000}k"


def gather(repo_root: Path) -> dict:
    import time
    roles = load_roles(repo_root)
    now = time.time()
    cards = []
    total_chars = 0
    total_projects = 0
    for name, meta in roles.items():
        rm = role_md_path(repo_root, name)
        text = ""
        if rm.is_file():
            try:
                text = rm.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                text = ""
        chars = len(text)
        total_chars += chars
        role_dir = repo_root / name
        projects = []
        if role_dir.is_dir():
            projects = sorted(p.name for p in role_dir.iterdir()
                              if p.is_dir() and p.name != "memory" and not p.name.startswith("."))
        total_projects += len(projects)
        insights = (_section_count(text, "Gotchas") + _section_count(text, "Best practices")
                    + _section_count(text, "Patterns"))
        ts = read_marker_ts(role_memory_dir(repo_root, name) / ".last_promote")
        fresh = "cold" if ts <= 0 else ("stale" if (now - ts) > STALE_DAYS * 86400 else "fresh")
        cards.append({
            "name": name, "desc": (meta.get("desc") or "").strip(),
            "chars": chars, "projects": projects, "n_projects": len(projects),
            "insights": insights, "fresh": fresh,
        })
    gpath = global_claude_md_path(repo_root)
    gchars = gpath.stat().st_size if gpath.is_file() else 0
    avg_role = (total_chars / len(cards)) if cards else 0
    dump = (gchars + total_chars) / 4
    scoped = (gchars + avg_role) / 4

    # Savings label: only show a confident % when we have real ROLE.md content
    # to measure.  With little/no content the formula collapses to 1 - 1/N
    # (pure role-count artefact) which would be misleading.
    _MIN_REAL_CHARS = 200  # below this we consider it "no real content"
    has_real_content = total_chars >= _MIN_REAL_CHARS
    if dump > 0 and has_real_content:
        savings_pct = int(round(100 * (dump - scoped) / dump))
        savings_label = f"{savings_pct}%"
    elif dump > 0:
        # Formula would just reflect role count; label it as an estimate
        savings_pct = int(round(100 * (dump - scoped) / dump))
        savings_label = f"~{savings_pct}% est."
    else:
        savings_pct = 0
        savings_label = "n/a"

    return {
        "roles": cards, "n_roles": len(cards), "n_projects": total_projects,
        "n_insights": sum(c["insights"] for c in cards),
        "savings": savings_pct, "savings_label": savings_label,
        "global_chars": gchars,
    }


def render_svg(data: dict, title: str,
               repo_url: str = "github.com/hdk10/orgkit") -> str:
    T = PALETTE
    cards = data["roles"]
    n = max(1, len(cards))
    cols = min(3, n)
    rows = (n + cols - 1) // cols

    W = 1280
    M = 72
    content_w = W - 2 * M
    lane_w = content_w / cols
    node_w = int(min(lane_w - 28, 360))
    node_h = 96

    # project sub-nodes are only drawn on a single-row chart; reserve room for them
    max_proj = max((c["n_projects"] for c in cards), default=0)
    show_projects = (rows == 1 and max_proj > 0)
    pill_rows = min(3, max_proj) + (1 if max_proj > 3 else 0)
    pill_extra = (24 + pill_rows * 30 + 6) if show_projects else 0
    row_gap = node_h + 56               # multi-row vertical spacing

    title_top = 72
    global_y = 188
    global_w, global_h = 460, 66
    bus_y = global_y + global_h + 46
    rows_top = bus_y + 40
    nodes_h = (rows - 1) * row_gap + node_h
    foot = 96
    H = int(rows_top + nodes_h + pill_extra + foot)

    o = []
    o.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}" font-family="{SANS}">')
    o.append('<defs>'
             f'<linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">'
             f'<stop offset="0" stop-color="{T["bg"]}"/><stop offset="1" stop-color="{T["bg2"]}"/>'
             f'</linearGradient></defs>')
    o.append(f'<rect width="{W}" height="{H}" fill="url(#bg)"/>')

    def line(x1, y1, x2, y2, w=1.25):
        o.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{T["line"]}" stroke-width="{w}"/>')

    def hexglyph(cx, cy, r, color, sw=2.0):
        import math
        pts = []
        for k in range(6):
            a = math.pi / 180 * (60 * k - 90)
            pts.append(f"{cx + r * math.cos(a):.1f},{cy + r * math.sin(a):.1f}")
        o.append(f'<polygon points="{" ".join(pts)}" fill="none" stroke="{color}" stroke-width="{sw}"/>')

    # ---------- header ----------
    o.append(f'<text x="{M}" y="{title_top}" font-size="13" letter-spacing="3" '
             f'fill="{T["muted"]}" font-family="{MONO}">ORG MEMORY MAP</text>')
    o.append(f'<text x="{M}" y="{title_top + 44}" font-size="40" font-weight="600" '
             f'fill="{T["ink"]}" font-family="{T["title_font"]}">{_esc(_truncate(title, 30))}</text>')
    # slim stat line, accent numbers
    stats = [(str(data["n_roles"]), "roles"), (str(data["n_projects"]), "projects"),
             (str(data["n_insights"]), "lessons"),
             (data.get("savings_label", f'{data["savings"]}%'), "leaner / session")]
    sx = M
    sy = title_top + 78
    parts = []
    for big, small in stats:
        parts.append((big, small))
    cx = sx
    for i, (big, small) in enumerate(parts):
        o.append(f'<text x="{cx}" y="{sy}" font-size="18" font-weight="700" fill="{T["accent"]}" '
                 f'font-family="{MONO}">{_esc(big)}</text>')
        bw = 11 * len(big)
        o.append(f'<text x="{cx + bw + 6}" y="{sy}" font-size="14" fill="{T["muted"]}">{_esc(small)}</text>')
        sw = bw + 6 + 8 * len(small) + 26
        cx += sw
        if i < len(parts) - 1:
            o.append(f'<text x="{cx - 16}" y="{sy}" font-size="14" fill="{T["faint"]}">·</text>')

    # ---------- global node (top of the chart) ----------
    gx = (W - global_w) // 2
    o.append(f'<rect x="{gx}" y="{global_y}" width="{global_w}" height="{global_h}" rx="14" '
             f'fill="{T["panel"]}" stroke="{T["border"]}"/>')
    hexglyph(gx + 34, global_y + global_h / 2, 13, T["accent"], 2.0)
    o.append(f'<text x="{gx + 60}" y="{global_y + 28}" font-size="16" font-weight="600" '
             f'fill="{T["ink"]}">Global memory</text>')
    o.append(f'<text x="{gx + 60}" y="{global_y + 48}" font-size="12.5" fill="{T["muted"]}" '
             f'font-family="{MONO}">CLAUDE.md · {_brain_kb(data["global_chars"])} · loads every session</text>')

    # ---------- connectors: global -> bus -> lanes ----------
    cx_center = W // 2
    line(cx_center, global_y + global_h, cx_center, bus_y)            # drop to bus
    lane_centers = [int(M + lane_w * c + lane_w / 2) for c in range(cols)]
    if cols > 1:
        line(lane_centers[0], bus_y, lane_centers[-1], bus_y)        # horizontal bus
    fresh_color = {"fresh": T["fresh"], "stale": T["stale"], "cold": T["cold"]}

    max_chars = max([c["chars"] for c in cards], default=1) or 1

    # ---------- role nodes + project sub-nodes ----------
    for idx, c in enumerate(cards):
        r, col = divmod(idx, cols)
        spine_x = lane_centers[col]
        node_x = spine_x - node_w // 2
        node_y = rows_top + r * row_gap
        # connector from bus (row 0) or from row above
        if r == 0:
            line(spine_x, bus_y, spine_x, node_y)
        # node card
        o.append(f'<rect x="{node_x}" y="{node_y}" width="{node_w}" height="{node_h}" rx="13" '
                 f'fill="{T["panel"]}" stroke="{T["border"]}"/>')
        # freshness accent edge (subtle)
        fc = fresh_color[c["fresh"]]
        o.append(f'<rect x="{node_x}" y="{node_y}" width="4" height="{node_h}" rx="2" fill="{fc}"/>')
        o.append(f'<text x="{node_x + 20}" y="{node_y + 32}" font-size="18" font-weight="600" '
                 f'fill="{T["ink"]}">{_esc(_truncate(c["name"], 18))}</text>')
        o.append(f'<text x="{node_x + 20}" y="{node_y + 52}" font-size="12" fill="{T["muted"]}">'
                 f'{_esc(_truncate(c["desc"] or "—", int(node_w / 7.8)))}</text>')
        # slim brain meter
        bar_y = node_y + 66
        bar_w = node_w - 40
        fill = max(6, int(bar_w * (c["chars"] / max_chars)))
        o.append(f'<rect x="{node_x + 20}" y="{bar_y}" width="{bar_w}" height="5" rx="2.5" fill="{T["bg2"]}"/>')
        o.append(f'<rect x="{node_x + 20}" y="{bar_y}" width="{fill}" height="5" rx="2.5" fill="{T["accent"]}"/>')
        o.append(f'<text x="{node_x + 20}" y="{bar_y + 22}" font-size="11" fill="{T["faint"]}" '
                 f'font-family="{MONO}">{_brain_kb(c["chars"])} brain · {_plural(c["insights"], "lesson")}</text>')

        # project sub-nodes (only when single row, to keep it clean)
        if rows == 1 and c["projects"]:
            py = node_y + node_h + 18
            line(spine_x, node_y + node_h, spine_x, py - 4)
            shown = c["projects"][:3]
            for j, proj in enumerate(shown):
                label = _truncate(proj, 16)
                pw = min(node_w, 11 + 7 * len(label) + 16)
                px = spine_x - pw // 2
                yy = py + j * 30
                if j > 0:
                    line(spine_x, py + (j - 1) * 30 + 22, spine_x, yy)
                o.append(f'<rect x="{px}" y="{yy}" width="{pw}" height="22" rx="11" '
                         f'fill="{T["panel2"]}" stroke="{T["border"]}"/>')
                o.append(f'<circle cx="{px + 13}" cy="{yy + 11}" r="2.4" fill="{T["accent"]}"/>')
                o.append(f'<text x="{px + 22}" y="{yy + 15}" font-size="11.5" fill="{T["muted"]}" '
                         f'font-family="{MONO}">{_esc(label)}</text>')
            if len(c["projects"]) > 3:
                yy = py + 3 * 30
                o.append(f'<text x="{spine_x}" y="{yy + 12}" font-size="11" fill="{T["faint"]}" '
                         f'text-anchor="middle">+{len(c["projects"]) - 3} more</text>')

    # ---------- footer ----------
    fy = H - 40
    line(M, fy - 20, W - M, fy - 20, 1)
    hexglyph(M + 11, fy - 4, 9, T["accent"], 1.6)
    o.append(f'<text x="{M + 28}" y="{fy}" font-size="13.5" font-weight="600" fill="{T["ink"]}">made with orgkit</text>')
    o.append(f'<text x="{W - M}" y="{fy}" font-size="12.5" fill="{T["muted"]}" text-anchor="end" '
             f'font-family="{MONO}">{_esc(repo_url)}</text>')
    o.append('</svg>')
    return "\n".join(o)


def run(repo_root: Path, out_path: Path | None, title: str | None,
        repo_url: str = "github.com/hdk10/orgkit") -> Path:
    data = gather(repo_root)
    svg = render_svg(data, title or repo_root.name, repo_url=repo_url)
    out = out_path or (repo_root / "ORG_MAP.svg")
    out.write_text(svg, encoding="utf-8")
    savings_display = data.get("savings_label", f"{data['savings']}%")
    print(f"[orgmap] wrote {out}  ({data['n_roles']} roles, {data['n_projects']} projects, "
          f"{data['n_insights']} lessons, {savings_display} leaner)")
    print("[orgmap] open it, screenshot it, post it — it's built to be shared.")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Render a shareable org-chart SVG.")
    ap.add_argument("--target", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--title", type=str, default=None)
    ap.add_argument("--repo-url", type=str, default="github.com/hdk10/orgkit")
    args = ap.parse_args(argv)
    repo_root = args.target.resolve() if args.target else detect_repo_root()
    if repo_root is None:
        print("orgmap: could not find a repo root (no .org/roles.json).", file=sys.stderr)
        return 1
    run(repo_root, args.out, args.title, repo_url=args.repo_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
