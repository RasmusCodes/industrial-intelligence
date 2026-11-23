"""Point-in-time quarterly financials from XBRL vintages (consolidated totals and segments).

Every filing is replayed in acceptance-time order. After each filing the latest value of every reported
period is known, and discrete fiscal quarters are derived:

* direct    a reported 3-month value (80-105 day duration)
* ytd_diff  cumulative year-to-date differences with the same fiscal-year start (cash flows are reported
            year-to-date only, and Q4 = full year - nine months)

Whenever a derived quarterly value appears or changes, one row is written with the acceptance time of the
filing that produced it (``avail_utc``). First-reported values (forecast targets) and values known at any
moment (features) follow directly. The replay logic is adapted from the author's earnings-engine project.
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np
import pandas as pd

from . import store

Q_MIN, Q_MAX = 80, 105
REL_TOL = 1e-6
MAX_YTD_VINTAGE_GAP = pd.Timedelta(days=400)

DURATION_ITEMS: dict[str, list[str]] = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueGoodsNet",
                "SalesRevenueServicesNet"],
    "cogs": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold",
             "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "sga": ["SellingGeneralAndAdministrativeExpense"],
    "rnd": ["ResearchAndDevelopmentExpense"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
              "PaymentsForCapitalImprovements", "PaymentsToAcquireOtherPropertyPlantAndEquipment"],
}
INSTANT_ITEMS: dict[str, list[str]] = {
    "rpo": ["RevenueRemainingPerformanceObligation"],
    "inventory": ["InventoryNet"],
    "contract_liab": ["ContractWithCustomerLiabilityCurrent", "ContractWithCustomerLiability"],
}
FIN_FORMS = ("10-Q", "10-K", "10-KT", "10-Q/A", "10-K/A", "10-KT/A", "20-F", "20-F/A")


# ----------------------------------------------------------------------------- replay core

def prep(facts: pd.DataFrame, filings: pd.DataFrame, concepts: list[str], unit: str = "USD") -> pd.DataFrame:
    f = facts[facts["concept"].isin(concepts) & (facts["unit"] == unit) & facts["form"].isin(FIN_FORMS)].copy()
    if f.empty:
        return f
    f["prio"] = f["concept"].map({c: i for i, c in enumerate(concepts)})
    acc = filings[["accn", "acceptance_utc"]].drop_duplicates("accn")
    f = f.merge(acc, on="accn", how="left")
    fb = (pd.to_datetime(f["filed"]) + pd.Timedelta(hours=23, minutes=59)).dt.tz_localize("America/New_York")
    f["avail_utc"] = f["acceptance_utc"].fillna(fb.dt.tz_convert("UTC"))
    f["end"] = pd.to_datetime(f["end"])
    if "start" in f:
        f["start"] = pd.to_datetime(f["start"])
    return f.sort_values(["avail_utc", "accn", "prio", "val"])


def _derive_quarters(cur: dict) -> dict:
    """{(start, end): (value, avail)} -> {quarter_end: (start, value, derivation, fy_start)}.

    A year-to-date difference is formed only when both cumulative values were filed within ~13 months of each
    other (a full year restated years later minus an original nine-month figure would mix bases)."""
    direct, cum = {}, defaultdict(dict)
    fy_starts = set()
    for (s, e), (v, _) in cur.items():
        d = (e - s).days + 1
        if Q_MIN <= d <= Q_MAX:
            direct[e] = (s, v)
        if d > Q_MAX:
            fy_starts.add(s)
    for (s, e), va in cur.items():
        if s in fy_starts:
            cum[s][e] = va
    out = {}
    for s, pts in cum.items():
        prev_e = None
        for e in sorted(pts):
            if prev_e is None:
                if Q_MIN <= (e - s).days + 1 <= Q_MAX:
                    out[e] = (s, pts[e][0], "direct", s)
            else:
                gap = (e - prev_e).days
                if Q_MIN <= gap <= Q_MAX and abs(pts[e][1] - pts[prev_e][1]) <= MAX_YTD_VINTAGE_GAP:
                    out[e] = (prev_e + pd.Timedelta(days=1), pts[e][0] - pts[prev_e][0], "ytd_diff", s)
            prev_e = e
    for e, (s, v) in direct.items():
        fs = next((x for x in sorted(fy_starts) if x <= s and (e - x).days < 380), None)
        out[e] = (s, v, "direct", fs if fs is not None else out.get(e, (None, None, None, None))[3])
    return out


def duration_vintages(f: pd.DataFrame, item: str) -> list[dict]:
    rows: list[dict] = []
    if f.empty:
        return rows
    cur: dict = {}
    prev: dict = {}
    for (avail, accn), g in f.groupby(["avail_utc", "accn"], sort=True):
        g = g.dropna(subset=["start"]).drop_duplicates(["start", "end"], keep="first")
        for s, e, v in zip(g["start"], g["end"], g["val"]):
            cur[(s, e)] = (float(v), avail)
        q = _derive_quarters(cur)
        form = g["form"].iloc[0]
        for e, (s, v, how, fys) in q.items():
            pv = prev.get(e)
            if pv is None or abs(pv - v) > REL_TOL * max(1.0, abs(pv)):
                rows.append({"item": item, "period_start": s, "period_end": e, "value": v, "derivation": how,
                             "fy_start": fys, "avail_utc": avail, "accn": accn, "form": form})
            prev[e] = v
    return rows


def annual_vintages(f: pd.DataFrame, item: str) -> list[dict]:
    """Full-year values (for annual-only filers such as 20-F registrants)."""
    rows, prev = [], {}
    if f.empty:
        return rows
    for (avail, accn), g in f.groupby(["avail_utc", "accn"], sort=True):
        g = g.dropna(subset=["start"])
        g = g[((g["end"] - g["start"]).dt.days + 1).between(350, 380)].drop_duplicates(["start", "end"])
        for s, e, v in zip(g["start"], g["end"], g["val"]):
            v = float(v)
            pv = prev.get(e)
            if pv is None or abs(pv - v) > REL_TOL * max(1.0, abs(pv)):
                rows.append({"item": item, "period_start": s, "period_end": e, "value": v, "derivation": "annual",
                             "fy_start": s, "avail_utc": avail, "accn": accn, "form": g["form"].iloc[0]})
            prev[e] = v
    return rows


def instant_vintages(f: pd.DataFrame, item: str) -> list[dict]:
    rows, prev = [], {}
    if f.empty:
        return rows
    for (avail, accn), g in f.groupby(["avail_utc", "accn"], sort=True):
        g = g.drop_duplicates(["end"], keep="first")
        form = g["form"].iloc[0]
        for e, v in zip(g["end"], g["val"]):
            v = float(v)
            pv = prev.get(e)
            if pv is None or abs(pv - v) > REL_TOL * max(1.0, abs(pv)):
                rows.append({"item": item, "period_start": pd.NaT, "period_end": e, "value": v,
                             "derivation": "instant", "fy_start": pd.NaT, "avail_utc": avail, "accn": accn,
                             "form": form})
            prev[e] = v
    return rows


def canonical_boundaries(ft: pd.DataFrame, tol_days: int = 10) -> dict:
    """Map period boundaries a few days apart (52/53-week vs calendar month-end tags) onto one date."""
    ends = pd.to_datetime(ft["end"])
    starts = pd.to_datetime(ft["start"].dropna()) - pd.Timedelta(days=1)
    counts = pd.concat([ends, starts], ignore_index=True).value_counts()
    mapping, cluster = {}, []

    def flush():
        if cluster:
            best = max(cluster, key=lambda d: (counts[d], -d.value))
            for d in cluster:
                mapping[d] = best

    for d in sorted(counts.index):
        if cluster and (d - cluster[-1]).days > tol_days:
            flush()
            cluster = []
        cluster.append(d)
    flush()
    return mapping


def apply_boundaries(ft: pd.DataFrame) -> pd.DataFrame:
    m = canonical_boundaries(ft)
    ft = ft.copy()
    ft["end"] = pd.to_datetime(ft["end"]).map(lambda d: m.get(d, d))
    s = pd.to_datetime(ft["start"])
    has = s.notna()
    adj = (s[has] - pd.Timedelta(days=1)).map(lambda d: m.get(d, d)) + pd.Timedelta(days=1)
    ft["start"] = s
    ft.loc[has, "start"] = adj
    ft["end"] = ft["end"].dt.strftime("%Y-%m-%d")
    ft["start"] = ft["start"].dt.strftime("%Y-%m-%d")
    return ft


SCALE_CHECK = {"revenue", "cogs", "gross_profit", "capex", "rpo", "inventory"}


def clean_vintages(v: pd.DataFrame) -> pd.DataFrame:
    """Drop XBRL tagging errors: non-positive revenue and values 10^3 / 10^6 off the neighbouring quarters."""
    v = v[~((v["item"] == "revenue") & (v["value"] <= 0))].copy()
    latest = v.sort_values("avail_utc").drop_duplicates(["ticker", "item", "period_end"], keep="last")
    refs = []
    for (tk, it), g in latest[latest["item"].isin(SCALE_CHECK)].groupby(["ticker", "item"]):
        g = g.sort_values("period_end")
        a = g["value"].abs().to_numpy()
        ref = [np.median(np.r_[a[max(0, i - 2):i], a[i + 1:i + 3]]) if len(a) > 1 else np.nan for i in range(len(a))]
        refs.append(pd.DataFrame({"ticker": tk, "item": it, "period_end": g["period_end"].values, "ref": ref}))
    if refs:
        r = pd.concat(refs, ignore_index=True)
        v = v.merge(r, on=["ticker", "item", "period_end"], how="left")
        with np.errstate(divide="ignore", invalid="ignore"):
            lg = np.log10(v["value"].abs() / v["ref"])
        bad = v["ref"].gt(0) & (((lg - 3).abs() < 0.3) | ((lg + 3).abs() < 0.3) | ((lg - 6).abs() < 0.3)
                                | ((lg + 6).abs() < 0.3))
        v = v[~bad].drop(columns="ref")
    return v.reset_index(drop=True)


def build_vintages(facts: pd.DataFrame, filings: pd.DataFrame, annual_tickers: set[str] | None = None) -> pd.DataFrame:
    annual_tickers = annual_tickers or set()
    out = []
    for tk, ft in facts.groupby("ticker"):
        ft = apply_boundaries(ft)
        fl = filings[filings["ticker"] == tk]
        for item, concepts in DURATION_ITEMS.items():
            p = prep(ft, fl, concepts)
            rows = annual_vintages(p, item) if tk in annual_tickers else duration_vintages(p, item)
            for r in rows:
                r["ticker"] = tk
                out.append(r)
        for item, concepts in INSTANT_ITEMS.items():
            for r in instant_vintages(prep(ft, fl, concepts), item):
                r["ticker"] = tk
                out.append(r)
    v = pd.DataFrame(out)
    v["period_days"] = (v["period_end"] - v["period_start"]).dt.days + 1
    v = clean_vintages(v)
    return v.sort_values(["ticker", "item", "period_end", "avail_utc"]).reset_index(drop=True)


def instance_fallback(facts: pd.DataFrame, filings: pd.DataFrame) -> pd.DataFrame:
    """Consolidated (non-dimensional) facts from XBRL instance documents for filings that the companyfacts
    feed lacks (e.g. United Rentals' 10-Q for June 2026). Same schema as companyfacts rows."""
    xf = store.load("sec_xbrl_facts", missing_ok=True)
    if xf.empty:
        return facts
    add = xf[(xf["n_dims"] == 0) & ~xf["accn"].isin(set(facts["accn"]))].copy()
    if add.empty:
        return facts
    fl = filings.drop_duplicates("accn").set_index("accn")
    add["cik"] = add["accn"].map(fl["cik"])
    add["unit"] = "USD"
    add["filed"] = add["accn"].map(fl["filing_date"])
    add["fy"], add["fp"], add["frame"] = None, None, None
    add = add.rename(columns={"taxonomy": "taxonomy"})
    cols = ["ticker", "cik", "taxonomy", "concept", "unit", "start", "end", "val", "accn", "fy", "fp", "form", "filed", "frame"]
    print(f"  instance fallback: {add['accn'].nunique()} filings missing from companyfacts", flush=True)
    return pd.concat([facts, add[cols]], ignore_index=True)


def build() -> pd.DataFrame:
    from .universe import raw as uraw
    facts = store.load("sec_facts")
    filings = store.load("sec_filings")
    facts = instance_fallback(facts, filings)
    annual = {r["ticker"] for r in uraw().get("annual_only", [])}
    v = build_vintages(facts, filings, annual)
    store.save("fundamentals_vintages", v)
    return v


# ----------------------------------------------------------------------------- views

def first_reported(v: pd.DataFrame) -> pd.DataFrame:
    return v.sort_values("avail_utc").drop_duplicates(["ticker", "item", "period_end"], keep="first")


def latest(v: pd.DataFrame) -> pd.DataFrame:
    return v.sort_values("avail_utc").drop_duplicates(["ticker", "item", "period_end"], keep="last")


def asof(v: pd.DataFrame, tau) -> pd.DataFrame:
    tau = pd.Timestamp(tau)
    tau = tau.tz_localize("UTC") if tau.tzinfo is None else tau.tz_convert("UTC")
    return latest(v[v["avail_utc"] < tau])


def quarterly_wide(v: pd.DataFrame, view: str = "first") -> pd.DataFrame:
    """ticker x period_end table of items (first-reported or latest)."""
    base = first_reported(v) if view == "first" else latest(v)
    base = base[base["derivation"] != "annual"]
    w = base.pivot_table(index=["ticker", "period_end"], columns="item", values="value", aggfunc="first")
    av = base[base["item"] == "revenue"].set_index(["ticker", "period_end"])["avail_utc"].rename("revenue_avail")
    return w.join(av).reset_index()


def gross_margin(rev, gp, cogs):
    """GM in percent from GrossProfit when tagged, else revenue - cost of revenue; sign errors flipped."""
    rev = pd.Series(rev, dtype=float)
    gp = pd.Series(gp, dtype=float, index=rev.index)
    cogs = pd.Series(cogs, dtype=float, index=rev.index)
    alt = rev - cogs
    signerr = gp.notna() & alt.notna() & (gp < 0) & ((gp + alt).abs() < 0.02 * rev.abs())
    g = gp.where(gp.notna(), alt).where(~signerr, alt)
    gm = 100 * g / rev.where(rev > 0)
    return gm.where(gm.abs() < 100)
