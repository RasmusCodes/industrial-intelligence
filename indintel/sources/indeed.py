"""Indeed Hiring Lab job postings index by occupational sector (US), daily since February 2020.

Data: github.com/hiring-lab/job_postings_tracker (CC BY 4.0, attribution: Indeed Hiring Lab). Only the
current file is used, so the series are 'lagged' (7 days after month end); the index is seasonally adjusted
and smoothed by Indeed, and back history can be revised.
"""
from __future__ import annotations

import io

import pandas as pd

from .. import store
from ..http import Fetcher
from ..indicators_config import series_for_source

URL = "https://raw.githubusercontent.com/hiring-lab/job_postings_tracker/master/US/job_postings_by_sector_US.csv"


def run(f: Fetcher) -> pd.DataFrame:
    data = f.get(URL, "indeed", max_age_hours=24 * 3)
    df = pd.read_csv(io.BytesIO(data), parse_dates=["date"])
    df = df[df["variable"] == "total postings"]
    df["month"] = df["date"].dt.to_period("M").dt.to_timestamp()
    m = df.groupby(["display_name", "month"]).agg(value=("indeed_job_postings_index", "mean"),
                                                 days=("date", "nunique")).reset_index()
    store.save("indeed_monthly", m)
    frames = []
    for spec in series_for_source("indeed"):
        s = m[m["display_name"] == spec["params"]["sector"]].copy()
        if s.empty:
            raise RuntimeError(f"sector {spec['params']['sector']!r} not in the Indeed file; "
                               f"available: {sorted(m['display_name'].unique())[:60]}")
        s = s[s["days"] >= 25]
        s["series_id"], s["period"], s["freq"] = spec["id"], s["month"], "M"
        s["available_at"] = (s["month"] + pd.offsets.MonthEnd(0) + pd.Timedelta(days=spec["pub_lag_days"])).dt.tz_localize("UTC")
        s["vintage_kind"], s["source"] = "lagged", "indeed"
        frames.append(s[store.OBS_COLUMNS])
    out = store.check_obs(pd.concat(frames, ignore_index=True))
    store.save("obs_indeed", out)
    return out
