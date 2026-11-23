"""EIA-930 Hourly Electric Grid Monitor: demand by balancing authority (BA) and PJM sub-region.

Six-month CSV files (since July 2015 for BAs; sub-regions such as Dominion 'DOM' later) are reduced to
monthly average demand in MW. EIA publishes hourly data with about a day's delay and later revises some
values; only the current files exist, so the series are 'lagged' (available 10 days after month end).
"""
from __future__ import annotations

import io

import pandas as pd

from .. import store
from ..http import Fetcher
from ..indicators_config import series_for_source

BAL = "https://www.eia.gov/electricity/gridmonitor/sixMonthFiles/EIA930_BALANCE_{y}_{h}.csv"
SUB = "https://www.eia.gov/electricity/gridmonitor/sixMonthFiles/EIA930_SUBREGION_{y}_{h}.csv"
HALVES = ("Jan_Jun", "Jul_Dec")


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.replace(",", "", regex=False), errors="coerce")


def _half_files(start_year: int = 2015) -> list[tuple[int, str]]:
    today = pd.Timestamp.today()
    out = []
    for y in range(start_year, today.year + 1):
        for h in HALVES:
            if y == 2015 and h == "Jan_Jun":
                continue
            first_month = 1 if h == "Jan_Jun" else 7
            if pd.Timestamp(y, first_month, 1) > today:
                continue
            out.append((y, h))
    return out


def is_csv(data: bytes | None) -> bool:
    """Missing six-month files come back as an HTML page with status 200."""
    return bool(data) and b"Balancing Authority" in data[:400]


def monthly_from(data: bytes, key_cols: list[str]) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(data), low_memory=False)
    cols = {c.lower(): c for c in df.columns}
    tcol = cols.get("utc time at end of hour")
    dcol = next((cols[c] for c in ("demand (mw) (adjusted)", "demand (mw)") if c in cols), None)
    if tcol is None or dcol is None:
        raise ValueError(f"unexpected EIA-930 columns: {list(df.columns)[:12]}")
    t = pd.to_datetime(df[tcol], format="mixed", errors="coerce") - pd.Timedelta(minutes=30)
    out = pd.DataFrame({"month": t.dt.to_period("M").dt.to_timestamp(), "mw": _num(df[dcol])})
    for k in key_cols:
        out[k] = df[cols[k.lower()]].astype(str).str.strip()
    out = out.dropna(subset=["month", "mw"])
    # the hourly feed has isolated bad values (e.g. one Dominion hour in October 2021 near 10^9 MW, zeros during
    # outages of the reporting system); drop hours outside 0.25x-3x of the region-month median before averaging
    med = out.groupby(key_cols + ["month"])["mw"].transform("median")
    ok = (out["mw"] > 0.25 * med) & (out["mw"] < 3.0 * med)
    out = out[ok]
    g = out.groupby(key_cols + ["month"]).agg(mw=("mw", "mean"), hours=("mw", "size")).reset_index()
    return g


def run(f: Fetcher) -> pd.DataFrame:
    today = pd.Timestamp.today()
    frames_ba, frames_sub = [], []
    for y, h in _half_files():
        current = pd.Timestamp(y, 1 if h == "Jan_Jun" else 7, 1) + pd.DateOffset(months=6) > today - pd.DateOffset(months=2)
        age = 24 if current else None
        data = f.get(BAL.format(y=y, h=h), "eia930", allow_404=True, max_age_hours=age)
        data = data if is_csv(data) else None
        if data is not None:
            frames_ba.append(monthly_from(data, ["Balancing Authority"]))
        sdata = f.get(SUB.format(y=y, h=h), "eia930", allow_404=True, max_age_hours=age)
        sdata = sdata if is_csv(sdata) else None
        if sdata is not None:
            frames_sub.append(monthly_from(sdata, ["Balancing Authority", "Sub-Region"]))
        print(f"  EIA-930 {y} {h}: balance {'ok' if data else '-'} subregion {'ok' if sdata else '-'}", flush=True)
    ba = pd.concat(frames_ba, ignore_index=True).rename(columns={"Balancing Authority": "region"})
    ba = ba.groupby(["region", "month"]).agg(mw=("mw", "mean"), hours=("hours", "sum")).reset_index()
    us = ba.groupby("month").agg(mw=("mw", "sum"), hours=("hours", "sum")).reset_index()
    us["region"] = "US48"
    parts = [ba, us]
    if frames_sub:
        sub = pd.concat(frames_sub, ignore_index=True)
        sub["region"] = sub["Sub-Region"]
        sub = sub.groupby(["region", "month"]).agg(mw=("mw", "mean"), hours=("hours", "sum")).reset_index()
        parts.append(sub)
    monthly = pd.concat(parts, ignore_index=True)
    store.save("eia930_monthly", monthly)
    frames = []
    for spec in series_for_source("eia930"):
        s = monthly[monthly["region"] == spec["params"]["ba"]].copy()
        # drop months with less than 90% of hours (partial current month, outages in the feed)
        s = s[s["hours"] >= 0.9 * 24 * s["month"].dt.days_in_month]
        s["series_id"], s["period"], s["freq"], s["value"] = spec["id"], s["month"], "M", s["mw"]
        s["available_at"] = (s["month"] + pd.offsets.MonthEnd(0) + pd.Timedelta(days=spec["pub_lag_days"])).dt.tz_localize("UTC")
        s["vintage_kind"], s["source"] = "lagged", "eia930"
        frames.append(s[store.OBS_COLUMNS])
    out = store.check_obs(pd.concat(frames, ignore_index=True))
    store.save("obs_eia930", out)
    return out
