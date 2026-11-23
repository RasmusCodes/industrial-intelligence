"""A separate question: does the operational-indicator signal predict excess stock returns?

Predicting a company's revenue is not the same as predicting its stock. The indicators used here are public,
so their information may already be in the price. This module tests that with daily stock returns from
CRSP (licensed data, read from a local directory given by INDINTEL_CRSP_DIR and never redistributed; only
summary statistics are written).

Signal: s = BayesComp nowcast - AR nowcast, i.e. what the indicators add to a history-only forecast of
revenue growth, made one second before the release (h=0) or one quarter earlier (h=1).

Tests (each quarter is one cross-section; slopes are averaged over quarters, Fama-MacBeth style):
  T1 announcement: CAR over the release day and the next trading day, minus the S&P 500 (value-weighted)
  T2 pre-release drift: excess return from the day after the previous release to the day before this
     release, against the h=1 signal (tradable before the quarter is reported)
  T3 sanity: the realised revenue surprise against the AR forecast (y - AR) on the same announcement
     returns; if this is not priced either, the setup cannot detect anything
"""
from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np
import pandas as pd

from . import store
from .paths import OUT


def crsp_dir() -> Path | None:
    p = os.environ.get("INDINTEL_CRSP_DIR")
    return Path(p) if p and Path(p).exists() else None


def load_returns(tickers: list[str]) -> tuple[pd.DataFrame, pd.Series]:
    d = crsp_dir()
    sec = pd.read_parquet(d / "securities.parquet")
    tr = pd.read_parquet(d / "panel_tr.parquet")
    bench = pd.read_parquet(d / "benchmarks_daily.parquet")["SP500_VW_CRSP"]
    cols = {}
    toks = sec["member_tickers"].fillna("").astype(str).map(lambda s: set(s.replace(";", " ").replace(",", " ").split()))
    for tk in tickers:
        cand = sec[toks.map(lambda xs: tk in xs)]
        cand = cand[cand["is_current_member"] | (cand["last_member_end"] >= "2016-01-01")]
        if cand.empty:
            continue
        permno = str(cand.sort_values("n_valid").iloc[-1]["permno"])
        if permno in tr.columns:
            cols[tk] = tr[permno]
    return pd.DataFrame(cols), bench


def _event_day(ts_utc: pd.Timestamp, days: pd.DatetimeIndex) -> int | None:
    et = ts_utc.tz_convert("America/New_York")
    d = et.normalize().tz_localize(None)
    if et.hour >= 16:
        d = d + pd.Timedelta(days=1)
    i = int(days.searchsorted(d))
    return i if i < len(days) else None


def fama_macbeth(df: pd.DataFrame, y: str, x: str, by: str = "okey", min_n: int = 4) -> dict:
    slopes = []
    for _, g in df.dropna(subset=[y, x]).groupby(by):
        if len(g) < min_n or g[x].std() == 0:
            continue
        b = np.polyfit(g[x], g[y], 1)[0]
        slopes.append(b)
    s = np.array(slopes)
    if len(s) < 6:
        return {"slope": math.nan, "t": math.nan, "n_quarters": len(s)}
    return {"slope": float(s.mean()), "t": float(s.mean() / (s.std(ddof=1) / math.sqrt(len(s)))), "n_quarters": len(s)}


def run() -> pd.DataFrame:
    if crsp_dir() is None:
        print("  INDINTEL_CRSP_DIR not set or missing: returns study skipped")
        return pd.DataFrame()
    preds = store.load("preds_revenue")
    rel = store.load("releases")
    w = preds.pivot_table(index=["ticker", "period_end", "h", "okey", "y"], columns="model", values="yhat").reset_index()
    w["s"] = w["BayesComp"] - w["AR"]
    w["surprise_vs_ar"] = w["y"] - w["AR"]
    tick = sorted(w["ticker"].unique())
    R, mkt = load_returns(tick)
    days = R.index
    ex = R.sub(mkt, axis=0)
    rel = rel.sort_values(["ticker", "period_end"])
    rel["prev_release"] = rel.groupby("ticker")["release_utc"].shift(1)
    w = w.merge(rel[["ticker", "period_end", "release_utc", "prev_release"]], on=["ticker", "period_end"])
    rows = []
    for r in w.itertuples(index=False):
        if r.ticker not in ex.columns or pd.Timestamp(r.release_utc).tz_convert(None) > days[-1]:
            continue
        i = _event_day(pd.Timestamp(r.release_utc), days)
        if i is None or i + 1 >= len(days):
            continue
        car = ex[r.ticker].iloc[i:i + 2].sum()
        drift = math.nan
        if pd.notna(r.prev_release):
            j = _event_day(pd.Timestamp(r.prev_release), days)
            if j is not None and i - 1 > j + 1:
                drift = ex[r.ticker].iloc[j + 2:i].sum()
        rows.append({"ticker": r.ticker, "period_end": r.period_end, "h": r.h, "okey": r.okey, "s": r.s,
                     "surprise_vs_ar": r.surprise_vs_ar, "car": car, "drift": drift})
    ev = pd.DataFrame(rows).dropna(subset=["car"])
    out = []
    h0 = ev[ev["h"] == 0]
    h1 = ev[ev["h"] == 1]
    for name, df, y, x in (("T1 announcement CAR on h=0 signal", h0, "car", "s"),
                           ("T2 pre-release drift on h=1 signal", h1, "drift", "s"),
                           ("T3 announcement CAR on realised surprise vs AR", h0, "car", "surprise_vs_ar")):
        fm = fama_macbeth(df, y, x)
        d = df.dropna(subset=[y, x])
        hi, lo = d[d[x] > 0][y], d[d[x] <= 0][y]
        out.append({"test": name, "n_events": len(d), "companies": d["ticker"].nunique(), **fm,
                    "mean_ret_signal_pos": float(hi.mean()) if len(hi) else math.nan,
                    "mean_ret_signal_neg": float(lo.mean()) if len(lo) else math.nan,
                    "first_quarter": str(d["period_end"].min().date()) if len(d) else "",
                    "last_quarter": str(d["period_end"].max().date()) if len(d) else ""})
    res = pd.DataFrame(out)
    OUT.mkdir(exist_ok=True)
    res.to_csv(OUT / "returns_tests.csv", index=False)
    print(res.to_string(index=False))
    return res


if __name__ == "__main__":
    run()
