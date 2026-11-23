"""Census Value of Construction Put in Place (C30): private spending by type, including data centers.

FRED/ALFRED carry the main construction aggregates with vintages, but not the data center category. This
connector reads the Census historical table (privsatime.xlsx). Only the current revised history is
published, so the series is 'lagged': available 35 days after month end (the monthly release comes on the
first business day of the second month after the reference month). Revisions are not point-in-time.
"""
from __future__ import annotations

import io
import re

import pandas as pd

from .. import store
from ..http import Fetcher
from ..indicators_config import series_for_source

URL = "https://www.census.gov/construction/c30/xlsx/privsatime.xlsx"


def parse(data: bytes) -> pd.DataFrame:
    raw = pd.read_excel(io.BytesIO(data), header=None, engine="calamine", dtype=object)
    hdr = next(i for i in range(15) if str(raw.iloc[i, 0]).strip().lower() == "date")
    cols = [re.sub(r"\s+", " ", str(c).replace("_x000D_", " ")).strip() for c in raw.iloc[hdr].tolist()]
    cols = [re.sub(r"\d+$", "", c).strip() for c in cols]      # footnote digits glued to names
    df = raw.iloc[hdr + 1:].copy()
    df.columns = cols
    df = df[df["Date"].notna()]
    lab = df["Date"].astype(str).str.strip()
    month = pd.to_datetime(lab.str.replace(r"[pr]$", "", regex=True), format="%b-%y", errors="coerce")
    df = df.assign(month=month, flag=lab.str.extract(r"([pr])$")[0].fillna(""))
    df = df[df["month"].notna()]
    long = df.melt(id_vars=["month", "flag"], value_vars=[c for c in cols if c not in ("Date", "nan")],
                   var_name="category", value_name="value")
    long["value"] = pd.to_numeric(long["value"], errors="coerce")
    return long.dropna(subset=["value"])


def run(f: Fetcher) -> pd.DataFrame:
    data = f.get(URL, "census_c30", max_age_hours=24 * 3)
    long = parse(data)
    store.save("census_c30", long)
    frames = []
    for spec in series_for_source("census_c30"):
        col = spec["params"]["column"].lower()
        s = long[long["category"].str.lower() == col].copy()
        if s.empty:
            raise RuntimeError(f"column {spec['params']['column']!r} not in the Census table")
        s["series_id"], s["period"], s["freq"] = spec["id"], s["month"], "M"
        s["available_at"] = (s["month"] + pd.offsets.MonthEnd(0) + pd.Timedelta(days=spec["pub_lag_days"])).dt.tz_localize("UTC")
        s["vintage_kind"], s["source"] = "lagged", "census_c30"
        frames.append(s[store.OBS_COLUMNS])
        print(f"  {spec['id']}: {len(s)} months from {s['month'].min():%Y-%m}", flush=True)
    out = store.check_obs(pd.concat(frames, ignore_index=True))
    store.save("obs_census_c30", out)
    return out
