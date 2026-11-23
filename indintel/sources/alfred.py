"""FRED series with real-time vintages from ALFRED (St. Louis Fed).

For each series, the ALFRED download page lists every vintage date (the dates the series was published or
revised). ``alfredgraph.csv?id=X,X,...&vintage_date=d1,d2,...`` returns the series as published on each of
those dates. A row is stored whenever an observation first appears or changes, with ``available_at`` =
the vintage date + 1 day (00:00 UTC, i.e. the evening of the release day in New York): never earlier than
the actual release.

Series are listed in config/indicators.yaml (source: alfred).
"""
from __future__ import annotations

import io
import re

import pandas as pd

from .. import store
from ..http import Fetcher
from ..indicators_config import series_for_source

URL = "https://alfred.stlouisfed.org/graph/alfredgraph.csv?id={ids}&vintage_date={dates}"
VINTAGES_PAGE = "https://alfred.stlouisfed.org/series/downloaddata?seid={id}"
BATCH = 12                      # ALFRED returns at most 12 vintage columns per request (more are dropped silently)
FIRST_VINTAGE = "2011-06-01"    # forecasts start in 2013; earlier vintages are superseded by these


def vintage_dates(f: Fetcher, fred_id: str) -> list[pd.Timestamp]:
    html = f.get_text(VINTAGES_PAGE.format(id=fred_id), "alfred_meta", max_age_hours=20)
    m = re.search(r'id="form_selected_vintage_dates"(.*?)</select>', html, re.S)
    if not m:
        raise RuntimeError(f"no vintage list on the ALFRED page of {fred_id}")
    return sorted(pd.Timestamp(d) for d in set(re.findall(r'value="(\d{4}-\d{2}-\d{2})"', m.group(1))))


def fetch_series(f: Fetcher, fred_id: str, dates: list[pd.Timestamp]) -> pd.DataFrame:
    """Long table (period, vintage_date, value) for one series over its vintage dates."""
    frames = []
    for i in range(0, len(dates), BATCH):
        chunk = dates[i:i + BATCH]
        url = URL.format(ids=",".join([fred_id] * len(chunk)),
                         dates=",".join(d.strftime("%Y-%m-%d") for d in chunk))
        data = f.get(url, "alfred")          # a fixed list of past vintages never changes
        df = pd.read_csv(io.BytesIO(data))
        if len(df.columns) - 1 != len(chunk):
            raise RuntimeError(f"ALFRED returned {len(df.columns) - 1} vintages for {len(chunk)} requested ({fred_id})")
        df = df.rename(columns={df.columns[0]: "period"})
        long = df.melt(id_vars="period", var_name="col", value_name="value")
        long["vintage_date"] = pd.to_datetime(long["col"].str.rsplit("_", n=1).str[1], format="%Y%m%d")
        long["value"] = pd.to_numeric(long["value"], errors="coerce")
        frames.append(long.dropna(subset=["value"])[["period", "vintage_date", "value"]])
    out = pd.concat(frames, ignore_index=True)
    out["period"] = pd.to_datetime(out["period"])
    return out


def to_observations(series_id: str, long: pd.DataFrame, freq: str) -> pd.DataFrame:
    """One row per (period, vintage) where the value first appears or changes."""
    long = long.sort_values(["period", "vintage_date"])
    chg = long.groupby("period")["value"].transform(lambda s: s.ne(s.shift()))
    obs = long[chg].copy()
    obs["series_id"] = series_id
    obs["freq"] = freq
    obs["available_at"] = (obs["vintage_date"] + pd.Timedelta(days=1)).dt.tz_localize("UTC")
    obs["vintage_kind"] = "vintage"
    obs["source"] = "alfred"
    return obs[store.OBS_COLUMNS]


def run(f: Fetcher) -> pd.DataFrame:
    frames, meta = [], []
    for spec in series_for_source("alfred"):
        fid = spec["params"]["fred_id"]
        alld = vintage_dates(f, fid)
        dates = [d for d in alld if d >= pd.Timestamp(FIRST_VINTAGE)]
        # keep the last vintage before the cut-off so that history published earlier has a starting point
        before = [d for d in alld if d < pd.Timestamp(FIRST_VINTAGE)]
        dates = before[-1:] + dates
        long = fetch_series(f, fid, dates)
        obs = to_observations(spec["id"], long, spec.get("freq", "M"))
        frames.append(obs)
        meta.append({"series_id": spec["id"], "fred_id": fid, "n_vintages": len(dates),
                     "first_vintage": dates[0], "last_vintage": dates[-1], "n_periods": long["period"].nunique(),
                     "n_rows": len(obs)})
        print(f"  {spec['id']:28s} {fid:16s} vintages {len(dates):4d} from {dates[0]:%Y-%m-%d} rows {len(obs):6d}",
              flush=True)
    out = store.check_obs(pd.concat(frames, ignore_index=True))
    store.save("obs_alfred", out)
    store.save("alfred_meta", pd.DataFrame(meta))
    return out
