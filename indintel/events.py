"""Timeline of contracts, projects and announcements per company (dashboard).

Sources, each event keeping its own date and link:
* company 8-K press releases (headline; backlog / order sentences quoted verbatim)
* federal contract awards from USAspending (largest by amount)
* large planned power plants in EIA-860M (first month each plant appears as planned) for companies whose
  equipment such plants buy, by technology
* Texas data center registrations (TDLR TABS) above $50 million estimated cost, for data center suppliers
"""
from __future__ import annotations

import pandas as pd

from . import store
from .sources.sec_releases import scrub

TECH_FOR = {
    "GEV": ["gas_turbine", "wind"], "CAT": ["gas_turbine", "engine_fuelcell"], "CMI": ["engine_fuelcell"],
    "BE": ["engine_fuelcell"], "PWR": ["solar", "battery"], "MTZ": ["solar", "wind"],
}
DC_SUPPLIERS = {"VRT", "ETN", "MOD", "NVT", "POWL", "BE", "PWR", "HUBB"}


def build() -> pd.DataFrame:
    ev = []
    pr = store.load("press_releases", missing_ok=True)
    for r in pr.itertuples(index=False):
        title = r.headline or f"8-K: {r.kind}"
        is_results = "2.02" in str(r.items).split(",")
        ev.append({"ticker": r.ticker, "date": pd.Timestamp(r.date), "kind": "results" if is_results else "announcement",
                   "title": title, "detail": r.kpi or getattr(r, "lead", ""), "source": "SEC EDGAR 8-K", "url": r.url})
    fa = store.load("federal_awards", missing_ok=True)
    if not fa.empty:
        fa = fa.sort_values("amount", ascending=False).groupby("ticker").head(25)
        for r in fa.itertuples(index=False):
            ev.append({"ticker": r.ticker, "date": pd.to_datetime(r.start, errors="coerce"), "kind": "contract",
                       "title": f"Federal award ${r.amount / 1e6:,.1f}m from {r.sub_agency or r.agency}",
                       "detail": r.description, "source": "USAspending.gov", "url": r.url})
    pj = store.load("eia860m_projects", missing_ok=True)
    if not pj.empty:
        first = pj.sort_values("vintage").drop_duplicates(["plant_id", "group"], keep="first")
        first = first[first["vintage"] >= "2016-01-01"]
        for tk, groups in TECH_FOR.items():
            sub = first[first["group"].isin(groups)].sort_values("mw", ascending=False).head(30)
            for r in sub.itertuples(index=False):
                ev.append({"ticker": tk, "date": r.vintage, "kind": "project",
                           "title": f"{r.plant_name} ({r.state}): {r.mw:,.0f} MW {r.group.replace('_', ' ')} first listed as planned",
                           "detail": f"Owner {r.entity}; planned operation {int(r.plan_month) if pd.notna(r.plan_month) else '?'}/"
                                     f"{int(r.plan_year) if pd.notna(r.plan_year) else '?'}; status {r.status}",
                           "source": "EIA-860M", "url": "https://www.eia.gov/electricity/data/eia860m/"})
    td = store.load("tdlr_dc_projects", missing_ok=True)
    if not td.empty:
        big = td[td["cost"] >= 50e6].sort_values("cost", ascending=False).head(40)
        for tk in DC_SUPPLIERS:
            for r in big.itertuples(index=False):
                ev.append({"ticker": tk, "date": r.created, "kind": "project",
                           "title": f"Texas data center registration: {r.ProjectName} (${r.cost / 1e6:,.0f}m est.)",
                           "detail": f"TABS {r.ProjectNumber}; facility {r.FacilityName}",
                           "source": "TDLR TABS", "url": f"https://www.tdlr.texas.gov/TABS/Search/Project/{r.ProjectNumber}"})
    df = pd.DataFrame(ev).dropna(subset=["date"])
    for col in ("title", "detail"):                     # no phone numbers or e-mail addresses in published text
        df[col] = df[col].fillna("").astype(str).map(scrub)
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None) if getattr(pd.to_datetime(df["date"]).dt, "tz", None) else pd.to_datetime(df["date"])
    df = df.sort_values(["ticker", "date"], ascending=[True, False]).reset_index(drop=True)
    store.save("events", df)
    print(f"  events: {len(df)} ({df['kind'].value_counts().to_dict()})", flush=True)
    return df
