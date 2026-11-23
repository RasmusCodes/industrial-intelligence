"""Static SVG figures for the README, case studies and the website cover (no plotting library needed).

    python scripts/make_figures.py      -> docs/figures/*.svg

Style follows the dashboard: thin bars with 4px rounded data ends, hairline grid, one series colour,
text in ink colours, values labelled at the bar end, transparent background.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from indintel.paths import DOCS, OUT  # noqa: E402

FIG = DOCS / "figures"
INK, MUTED, GRID, AXIS = "#1a1a18", "#6b6963", "#e1e0d9", "#c3c2b7"
S1, S2, S3 = "#2a78d6", "#eb6834", "#1baf7a"
FONT = "Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif"


def esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def hbars(items: list[tuple[str, float]], title: str, subtitle: str, ref: float, lo: float, hi: float,
          fmt=lambda v: f"{v:.2f}", note: str = "", width: int = 760) -> str:
    row = 34
    top, left, right, bottom = 70, 270, 70, 46 if note else 30
    h = top + row * len(items) + bottom
    sx = lambda v: left + (v - lo) / (hi - lo) * (width - left - right)  # noqa: E731
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {h}" width="{width}" height="{h}" '
           f'font-family="{FONT}" role="img" aria-label="{esc(title)}">',
           f'<text x="0" y="22" font-size="17" font-weight="600" fill="{INK}">{esc(title)}</text>',
           f'<text x="0" y="44" font-size="13" fill="{MUTED}">{esc(subtitle)}</text>']
    step = next(s for s in (0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0) if (hi - lo) / s <= 6)
    first = round(-(-lo // step) * step, 6)
    ticks = [round(first + k * step, 6) for k in range(int((hi - first) / step + 1e-9) + 1)]
    for t in ticks:
        x = sx(t)
        out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{top - 6}" y2="{top + row * len(items)}" stroke="{GRID}" stroke-width="1"/>')
        out.append(f'<text x="{x:.1f}" y="{top + row * len(items) + 16}" font-size="11.5" fill="{MUTED}" text-anchor="middle">{fmt(t)}</text>')
    xr = sx(ref)
    out.append(f'<line x1="{xr:.1f}" x2="{xr:.1f}" y1="{top - 10}" y2="{top + row * len(items)}" stroke="{AXIS}" stroke-width="1.5"/>')
    for i, (label, v) in enumerate(items):
        cy = top + row * i + row / 2
        out.append(f'<text x="{left - 12}" y="{cy + 4:.1f}" font-size="13" fill="{INK}" text-anchor="end">{esc(label)}</text>')
        if v is None or pd.isna(v):
            continue
        x0, x1 = sx(ref), sx(v)
        bh, r = 14, 4
        y = cy - bh / 2
        if v >= ref:
            w = max(1.0, x1 - x0)
            d = (f"M{x0:.1f},{y:.1f}H{x0 + w - r:.1f}Q{x0 + w:.1f},{y:.1f} {x0 + w:.1f},{y + r:.1f}V{y + bh - r:.1f}"
                 f"Q{x0 + w:.1f},{y + bh:.1f} {x0 + w - r:.1f},{y + bh:.1f}H{x0:.1f}Z")
            tx, anchor = x0 + w + 6, "start"
        else:
            w = max(1.0, x0 - x1)
            d = (f"M{x0:.1f},{y:.1f}H{x1 + r:.1f}Q{x1:.1f},{y:.1f} {x1:.1f},{y + r:.1f}V{y + bh - r:.1f}"
                 f"Q{x1:.1f},{y + bh:.1f} {x1 + r:.1f},{y + bh:.1f}H{x0:.1f}Z")
            tx, anchor = x1 - 6, "end"
        out.append(f'<path d="{d}" fill="{S1}"/>')
        out.append(f'<text x="{tx:.1f}" y="{cy + 4:.1f}" font-size="12" font-weight="500" fill="{INK}" text-anchor="{anchor}">{fmt(v)}</text>')
    if note:
        out.append(f'<text x="0" y="{h - 8}" font-size="11.5" fill="{MUTED}">{esc(note)}</text>')
    out.append("</svg>")
    return "\n".join(out)


def main() -> None:
    FIG.mkdir(parents=True, exist_ok=True)
    s = pd.read_csv(OUT / "revenue_summary.csv")
    names = {"data_center": "Data center power and cooling", "grid": "Electrical equipment and grid",
             "semicap": "Semiconductor equipment", "backlog": "Backlog reporters",
             "caterpillar": "Caterpillar and machinery", "all": "All 17 companies"}
    items = []
    for k, label in names.items():
        r = s[(s["case"] == k) & (s["h"] == 0) & (s["period"] == "holdout") & (s["model"] == "BayesComp")]
        items.append((label, float(r["mae_vs_ar"].iloc[0]) if len(r) else None))
    vals = [v for _, v in items if v is not None]
    lo, hi = min(0.94, min(vals) - 0.01), max(1.04, max(vals) + 0.01)
    svg = hbars(items, "Nowcast error with indicators, relative to company history alone",
                "Mean absolute error of revenue growth, 2021-26 holdout; below 1 = indicators help",
                1.0, lo, hi, note="Partially pooled model, forecast one second before each earnings release. Source: this repository, out/revenue_summary.csv")
    (FIG / "nowcast_by_case.svg").write_text(svg, encoding="utf-8")
    items = []
    for h, label in ((0, "Nowcast (quarter ended, not reported)"), (1, "One quarter earlier"), (2, "Two quarters earlier")):
        r = s[(s["case"] == "all") & (s["h"] == h) & (s["period"] == "all") & (s["model"] == "BayesComp")]
        items.append((label, float(r["mae_vs_ar"].iloc[0]) if len(r) else None))
    vals = [v for _, v in items if v is not None]
    svg = hbars(items, "The indicators are coincident, not leading",
                "Error relative to the history-only model by forecast horizon, all companies, 2016-26",
                1.0, min(0.95, min(vals) - 0.01), max(1.05, max(vals) + 0.01),
                note="Below 1 = indicators help. Source: out/revenue_summary.csv")
    (FIG / "horizons.svg").write_text(svg, encoding="utf-8")
    print("figures written to", FIG)


if __name__ == "__main__":
    main()
