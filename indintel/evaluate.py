"""Forecast evaluation: error metrics and tests of equal accuracy.

Errors are pooled across companies, but forecasts made in the same calendar quarter share shocks, so the
tests first average the loss differential across companies within each origin quarter and then treat that
quarterly series with Newey-West (HAC) standard errors:

* Diebold-Mariano: H0 equal mean absolute (or squared) error, two-sided
* Clark-West: H0 the indicator adds nothing to a nested baseline (MSPE adjusted for estimation noise),
  one-sided; the right test when the larger model contains the baseline
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy import stats


def metrics(err: np.ndarray) -> dict:
    e = np.asarray(err, float)
    e = e[np.isfinite(e)]
    if len(e) == 0:
        return {"n": 0, "mae": math.nan, "rmse": math.nan, "medae": math.nan, "bias": math.nan}
    return {"n": int(len(e)), "mae": float(np.mean(np.abs(e))), "rmse": float(np.sqrt(np.mean(e ** 2))),
            "medae": float(np.median(np.abs(e))), "bias": float(np.mean(e))}


def nw_se(x: np.ndarray, lags: int | None = None) -> float:
    x = np.asarray(x, float)
    n = len(x)
    if n < 3:
        return math.nan
    lags = int(math.floor(4 * (n / 100) ** (2 / 9))) if lags is None else lags
    u = x - x.mean()
    s = np.sum(u * u) / n
    for L in range(1, lags + 1):
        w = 1 - L / (lags + 1)
        s += 2 * w * np.sum(u[L:] * u[:-L]) / n
    return math.sqrt(max(s, 0) / n)


def _by_time(d: np.ndarray, groups) -> np.ndarray:
    return pd.Series(d).groupby(pd.Series(groups).to_numpy()).mean().sort_index().to_numpy()


def dm_test(e_model, e_base, groups, loss: str = "abs") -> dict:
    """Diebold-Mariano on the time series of cross-sectional mean loss differentials (model - base)."""
    em, eb = np.asarray(e_model, float), np.asarray(e_base, float)
    ok = np.isfinite(em) & np.isfinite(eb)
    if ok.sum() < 10:
        return {"dm_stat": math.nan, "dm_p": math.nan, "n_periods": 0}
    f = (np.abs if loss == "abs" else np.square)
    d = f(em[ok]) - f(eb[ok])
    ts = _by_time(d, np.asarray(groups)[ok])
    se = nw_se(ts)
    t = ts.mean() / se if se and se > 0 else math.nan
    p = 2 * (1 - stats.norm.cdf(abs(t))) if np.isfinite(t) else math.nan
    return {"dm_stat": float(t), "dm_p": float(p), "n_periods": int(len(ts))}


def clark_west(y, yhat_base, yhat_model, groups) -> dict:
    """Clark-West (2007) adjusted MSPE test that the larger model improves on the nested baseline."""
    y, b, m = (np.asarray(a, float) for a in (y, yhat_base, yhat_model))
    ok = np.isfinite(y) & np.isfinite(b) & np.isfinite(m)
    if ok.sum() < 10:
        return {"cw_stat": math.nan, "cw_p": math.nan}
    f = (y[ok] - b[ok]) ** 2 - ((y[ok] - m[ok]) ** 2 - (b[ok] - m[ok]) ** 2)
    ts = _by_time(f, np.asarray(groups)[ok])
    se = nw_se(ts)
    t = ts.mean() / se if se and se > 0 else math.nan
    p = 1 - stats.norm.cdf(t) if np.isfinite(t) else math.nan
    return {"cw_stat": float(t), "cw_p": float(p)}


def oos_r2(y, yhat_model, yhat_base) -> float:
    y, m, b = (np.asarray(a, float) for a in (y, yhat_model, yhat_base))
    ok = np.isfinite(y) & np.isfinite(m) & np.isfinite(b)
    if ok.sum() < 5:
        return math.nan
    return float(1 - np.sum((y[ok] - m[ok]) ** 2) / np.sum((y[ok] - b[ok]) ** 2))


def coverage(y, lo, hi) -> float:
    y, lo, hi = (np.asarray(a, float) for a in (y, lo, hi))
    ok = np.isfinite(y) & np.isfinite(lo) & np.isfinite(hi)
    return float(np.mean((y[ok] >= lo[ok]) & (y[ok] <= hi[ok]))) if ok.any() else math.nan


def hit_rate(y_change, yhat_change) -> float:
    a, b = np.asarray(y_change, float), np.asarray(yhat_change, float)
    ok = np.isfinite(a) & np.isfinite(b) & (a != 0)
    return float(np.mean(np.sign(a[ok]) == np.sign(b[ok]))) if ok.any() else math.nan
