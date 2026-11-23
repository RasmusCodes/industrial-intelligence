"""Point-in-time storage conventions.

Indicator observations use one long schema (``OBS_COLUMNS``). Each row says what value a series had for a
period *and when that value became public* (``available_at``, UTC). Revised data are kept as separate rows,
so the value known at any moment is recoverable:

* ``asof(obs, t)``          latest row per (series, period) with available_at < t
* ``first_release(obs)``    earliest row per (series, period)
* ``latest(obs)``           newest row per (series, period); used only to measure look-ahead bias

``vintage_kind`` records how ``available_at`` was obtained, from strongest to weakest:

* ``vintage``      a dated snapshot of the series as published (ALFRED vintages, monthly EIA-860M files)
* ``filing``       SEC acceptance timestamp of the filing that first contained the value
* ``event``        the record carries its own public date (award, tender notice, registration, queue entry)
* ``lagged``       only today's revised history exists; available_at = period end + a documented
                   publication lag. Revisions are therefore *not* point-in-time (flagged in every output)
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .paths import PROCESSED

OBS_COLUMNS = ["series_id", "period", "freq", "value", "available_at", "vintage_kind", "source"]
VINTAGE_KINDS = ("vintage", "filing", "event", "lagged")


def path(name: str) -> Path:
    return PROCESSED / f"{name}.parquet"


def save(name: str, df: pd.DataFrame) -> Path:
    p = path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p, index=False)
    return p


def load(name: str, missing_ok: bool = False) -> pd.DataFrame:
    p = path(name)
    if not p.exists():
        if missing_ok:
            return pd.DataFrame()
        raise FileNotFoundError(f"{p} not built; run the matching `python -m indintel build` stage")
    return pd.read_parquet(p)


def check_obs(df: pd.DataFrame) -> pd.DataFrame:
    """Validate and normalise an observation table."""
    missing = [c for c in OBS_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"observation table lacks columns {missing}")
    out = df[OBS_COLUMNS].copy()
    out["period"] = pd.to_datetime(out["period"])
    out["available_at"] = pd.to_datetime(out["available_at"], utc=True)
    out["value"] = pd.to_numeric(out["value"], errors="coerce")
    bad = ~out["vintage_kind"].isin(VINTAGE_KINDS)
    if bad.any():
        raise ValueError(f"unknown vintage_kind values: {sorted(out.loc[bad, 'vintage_kind'].unique())}")
    if out["available_at"].isna().any():
        raise ValueError("every observation needs available_at")
    out = out.dropna(subset=["value"])
    return out.sort_values(["series_id", "period", "available_at"]).reset_index(drop=True)


def ns(s: pd.Series):
    """Datetime column -> int64 nanoseconds since the epoch (UTC), whatever the stored unit (pandas 3 uses
    microseconds by default) and timezone. Comparable with pd.Timestamp(...).value."""
    s = pd.to_datetime(s)
    if getattr(s.dt, "tz", None) is not None:
        s = s.dt.tz_convert("UTC").dt.tz_localize(None)
    return s.dt.as_unit("ns").astype("int64").to_numpy()


def asof(obs: pd.DataFrame, t) -> pd.DataFrame:
    """Latest known value per (series, period) strictly before t (UTC)."""
    t = pd.Timestamp(t)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    v = obs[obs["available_at"] < t]
    return v.sort_values("available_at").drop_duplicates(["series_id", "period"], keep="last")


def first_release(obs: pd.DataFrame) -> pd.DataFrame:
    return obs.sort_values("available_at").drop_duplicates(["series_id", "period"], keep="first")


def latest(obs: pd.DataFrame) -> pd.DataFrame:
    return obs.sort_values("available_at").drop_duplicates(["series_id", "period"], keep="last")
