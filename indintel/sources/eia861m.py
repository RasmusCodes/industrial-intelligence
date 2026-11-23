"""EIA-861M monthly retail electricity sales by state and customer sector (sales_revenue.xlsx).

Monthly data are published about two months after the reference month (Electric Power Monthly) and the
latest months are marked preliminary. Only the current file is used, so series are 'lagged'
(available 60 days after month end).
"""
from __future__ import annotations

import io

import pandas as pd

from .. import store
from ..http import Fetcher
from ..indicators_config import series_for_source

URL = "https://www.eia.gov/electricity/data/eia861m/xls/sales_revenue.xlsx"


def parse(data: bytes) -> pd.DataFrame:
    raw = pd.read_excel(io.BytesIO(data), sheet_name="Monthly-States", header=None, engine="calamine", dtype=object)
    hdr = next(i for i in range(10) if str(raw.iloc[i, 0]).strip() == "Year")
    sectors = raw.iloc[hdr - 2].ffill().tolist()
    metrics = raw.iloc[hdr - 1].tolist()
    cols = []
    for j, base in enumerate(raw.iloc[hdr].tolist()):
        if j < 4:
            cols.append(str(base).strip().lower().replace(" ", "_"))
        else:
            cols.append(f"{str(sectors[j]).strip().lower()}_{str(metrics[j]).strip().lower()}")
    df = raw.iloc[hdr + 1:].copy()
    df.columns = cols
    df = df[pd.to_numeric(df["year"], errors="coerce").notna()]
    df["month"] = pd.to_datetime(dict(year=df["year"].astype(int), month=df["month"].astype(int), day=1))
    val_cols = [c for c in cols if c.endswith(("_sales", "_revenue", "_customers"))]
    long = df.melt(id_vars=["month", "state", "data_status"], value_vars=val_cols, var_name="measure", value_name="value")
    long["value"] = pd.to_numeric(long["value"], errors="coerce")
    long[["sector", "metric"]] = long["measure"].str.rsplit("_", n=1, expand=True)
    return long.dropna(subset=["value"])


def run(f: Fetcher) -> pd.DataFrame:
    long = parse(f.get(URL, "eia861m", max_age_hours=24 * 3))
    store.save("eia861m", long)
    frames = []
    for spec in series_for_source("eia861m"):
        p = spec["params"]
        s = long[(long["state"] == p["state"]) & (long["sector"] == p["sector"]) & (long["metric"] == "sales")].copy()
        s["series_id"], s["period"], s["freq"] = spec["id"], s["month"], "M"
        s["available_at"] = (s["month"] + pd.offsets.MonthEnd(0) + pd.Timedelta(days=spec["pub_lag_days"])).dt.tz_localize("UTC")
        s["vintage_kind"], s["source"] = "lagged", "eia861m"
        frames.append(s[store.OBS_COLUMNS])
        print(f"  {spec['id']}: {len(s)} months from {s['month'].min():%Y-%m}", flush=True)
    out = store.check_obs(pd.concat(frames, ignore_index=True))
    store.save("obs_eia861m", out)
    return out
