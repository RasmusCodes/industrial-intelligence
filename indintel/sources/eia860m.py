"""EIA-860M Preliminary Monthly Electric Generator Inventory: one file per month since 2015.

Each monthly file lists every operating generator (with its operating month) and every planned generator
(with its planned operation month and construction status) at plants of 1 MW or more. Because EIA keeps
every past monthly file, the archive is a true point-in-time record of the US generation pipeline: what
developers reported as planned at each date, before projects were built, delayed or cancelled.

Vintage timing: the file for month M is released in the fourth week of month M+1 (e.g. data for
September 2026 on 23 October 2026). ``available_at`` = end of month M + 30 days.
"""
from __future__ import annotations

import io
import re

import numpy as np
import pandas as pd

from .. import store
from ..http import Fetcher
from ..indicators_config import series_for_source

INDEX = "https://www.eia.gov/electricity/data/eia860m/"
BASE = "https://www.eia.gov"
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}
PUB_LAG_DAYS = 30

GROUPS = {
    # prime mover codes: GT combustion turbine, CT/CS combined-cycle combustion part, CA combined-cycle steam part,
    # IC internal combustion engine, FC fuel cell, BA battery, PV photovoltaic, WT/WS wind
    "gas_turbine": lambda d: d["prime_mover"].isin(["GT", "CT", "CS"]) & d["energy_source"].isin(["NG", "OG", "BFG"]),
    "battery": lambda d: d["prime_mover"].eq("BA"),
    "solar": lambda d: d["prime_mover"].eq("PV"),
    "wind": lambda d: d["prime_mover"].isin(["WT", "WS"]),
    "engine_fuelcell": lambda d: d["prime_mover"].isin(["IC", "FC"]),
}


def file_links(f: Fetcher) -> list[tuple[pd.Timestamp, str]]:
    html = f.get_text(INDEX, "eia860m", max_age_hours=24)
    out = {}
    for href in re.findall(r'href="([^"]*_generator(\d{4})\.xlsx?)"', html):
        path, year = href
        m = re.search(r"/([a-z]+)_generator\d{4}\.xlsx?$", path)
        if not m or m.group(1) not in MONTHS:
            continue
        month = pd.Timestamp(int(year), MONTHS[m.group(1)], 1)
        url = path if path.startswith("http") else BASE + path
        out.setdefault(month, url)
    return sorted(out.items())


def _norm(c) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(c).strip().lower()).strip("_")


COLMAP = {
    "plant_id": "plant_id", "plant_name": "plant_name", "generator_id": "generator_id",
    "plant_state": "state", "sector": "sector", "entity_name": "entity",
    "nameplate_capacity_mw": "mw", "net_summer_capacity_mw": "mw_summer",
    "technology": "technology", "energy_source_code": "energy_source", "prime_mover_code": "prime_mover",
    "operating_month": "op_month", "operating_year": "op_year",
    "planned_operation_month": "plan_month", "planned_operation_year": "plan_year", "status": "status",
    "balancing_authority_code": "ba", "county": "county",
}


def read_sheet(data: bytes, want: str) -> pd.DataFrame:
    xl = pd.ExcelFile(io.BytesIO(data), engine="calamine")
    name = next((s for s in xl.sheet_names if s.strip().lower().startswith(want)), None)
    if name is None:
        return pd.DataFrame()
    raw = xl.parse(name, header=None, dtype=object)
    hdr = next(i for i in range(min(10, len(raw))) if any(_norm(x) == "plant_id" for x in raw.iloc[i].tolist()))
    df = raw.iloc[hdr + 1:].copy()
    df.columns = [_norm(c) for c in raw.iloc[hdr].tolist()]
    df = df.rename(columns={k: v for k, v in COLMAP.items() if k in df.columns})
    df = df[[c for c in COLMAP.values() if c in df.columns]]
    df = df[pd.to_numeric(df.get("plant_id"), errors="coerce").notna()]
    for c in ("mw", "mw_summer", "op_month", "op_year", "plan_month", "plan_year"):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    # files before 2016 report net summer capacity only; later files add nameplate capacity
    if "mw" not in df:
        df["mw"] = df.get("mw_summer")
    elif "mw_summer" in df:
        df["mw"] = df["mw"].fillna(df["mw_summer"])
    for c in ("prime_mover", "energy_source", "status", "technology", "state", "ba"):
        if c in df:
            df[c] = df[c].astype(str).str.strip()
    return df


def summarize(month: pd.Timestamp, op: pd.DataFrame, pl: pd.DataFrame) -> list[dict]:
    rows = []
    if not pl.empty:
        pdate = pd.to_datetime(dict(year=pl["plan_year"], month=pl["plan_month"], day=1), errors="coerce")
        months_out = (pdate.dt.year - month.year) * 12 + (pdate.dt.month - month.month)
    for g, sel in GROUPS.items():
        if not pl.empty:
            m = sel(pl)
            for h in (12, 24, 36):
                within = m & months_out.between(1, h)
                rows.append({"month": month, "group": g, "measure": f"planned_{h}m", "mw": float(pl.loc[within, "mw"].sum()),
                             "n": int(within.sum())})
            rows.append({"month": month, "group": g, "measure": "planned_all", "mw": float(pl.loc[m, "mw"].sum()),
                         "n": int(m.sum())})
            uc = m & pl["status"].str.startswith(("(U)", "(V)", "(TS)", "U", "V", "TS"))
            rows.append({"month": month, "group": g, "measure": "under_construction", "mw": float(pl.loc[uc, "mw"].sum()),
                         "n": int(uc.sum())})
        if not op.empty:
            m = sel(op) & (op["op_year"] == month.year) & (op["op_month"] == month.month)
            rows.append({"month": month, "group": g, "measure": "added", "mw": float(op.loc[m, "mw"].sum()),
                         "n": int(m.sum())})
            rows.append({"month": month, "group": g, "measure": "operating", "mw": float(op.loc[sel(op), "mw"].sum()),
                         "n": int(sel(op).sum())})
    return rows


def projects(month: pd.Timestamp, pl: pd.DataFrame, min_mw: float = 200) -> pd.DataFrame:
    """Large planned projects in a vintage (for the timeline): plant-level totals by group."""
    if pl.empty:
        return pd.DataFrame()
    out = []
    for g, sel in GROUPS.items():
        sub = pl[sel(pl)]
        if sub.empty:
            continue
        agg = sub.groupby(["plant_id", "plant_name", "state", "entity"], dropna=False).agg(
            mw=("mw", "sum"), plan_year=("plan_year", "min"), plan_month=("plan_month", "min"),
            status=("status", "first")).reset_index()
        agg = agg[agg["mw"] >= min_mw]
        agg["group"] = g
        out.append(agg)
    if not out:
        return pd.DataFrame()
    df = pd.concat(out, ignore_index=True)
    df["vintage"] = month
    return df


def run(f: Fetcher) -> pd.DataFrame:
    links = file_links(f)
    rows, projs, meta = [], [], []
    today = pd.Timestamp.today().normalize()
    for month, url in links:
        if month > today:
            continue
        recent = month >= today - pd.DateOffset(months=3)
        data = f.get(url, "eia860m", allow_404=True, max_age_hours=24 * 7 if recent else None)
        if data is None:
            meta.append({"month": month, "url": url, "status": "404"})
            continue
        if not data[:2] == b"PK":      # placeholder links for unreleased months return an HTML page
            meta.append({"month": month, "url": url, "status": "not a workbook"})
            continue
        op, pl = read_sheet(data, "operating"), read_sheet(data, "planned")
        last_op = None
        if not op.empty:
            d = pd.to_datetime(dict(year=op["op_year"], month=op["op_month"], day=1), errors="coerce")
            last_op = d.max()
        meta.append({"month": month, "url": url, "status": "ok", "n_operating": len(op), "n_planned": len(pl),
                     "max_operating_month": last_op})
        rows += summarize(month, op, pl)
        pj = projects(month, pl)
        if not pj.empty:
            projs.append(pj)
        print(f"  860M {month:%Y-%m}: operating {len(op):6d} planned {len(pl):5d} last op {last_op}", flush=True)
    summ = pd.DataFrame(rows)
    store.save("eia860m_summary", summ)
    store.save("eia860m_meta", pd.DataFrame(meta))
    if projs:
        store.save("eia860m_projects", pd.concat(projs, ignore_index=True))
    out = to_observations(summ)
    store.save("obs_eia860m", out)
    return out


def to_observations(summ: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for spec in series_for_source("eia860m"):
        p = spec["params"]
        measure = p.get("measure") or f"planned_{p.get('horizon_months', 24)}m"
        s = summ[(summ["group"] == p["group"]) & (summ["measure"] == measure)].copy()
        if s.empty:
            continue
        s["series_id"] = spec["id"]
        s["period"] = s["month"]
        s["freq"] = "M"
        s["value"] = s["mw"]
        month_end = s["month"] + pd.offsets.MonthEnd(0)
        s["available_at"] = (month_end + pd.Timedelta(days=PUB_LAG_DAYS)).dt.tz_localize("UTC")
        s["vintage_kind"] = "vintage"
        s["source"] = "eia860m"
        frames.append(s[store.OBS_COLUMNS])
    return store.check_obs(pd.concat(frames, ignore_index=True)) if frames else pd.DataFrame(columns=store.OBS_COLUMNS)


__all__ = ["run", "file_links", "read_sheet", "summarize", "to_observations", "GROUPS", "np"]
