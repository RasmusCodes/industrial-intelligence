"""Company-quarter modelling panel, built strictly point-in-time.

One row per (company, target quarter q, horizon h). The forecast origin is one second before the public
release of quarter q-h, so:

* h = 0  'nowcast': quarter q has ended and is not yet reported (the use case: estimate results before
          the company reports them)
* h = 1  one quarter earlier, h = 2 two quarters earlier

Targets are first-reported values; growth compares them with the year-ago quarter as stated in the same
filing. Every feature is computed from data public before the origin (``tau``).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import fundamentals as fu, store
from .indicators import FeatureEngine
from .indicators_config import registry
from .universe import target_tickers

HORIZONS = (0, 1, 2)
ONE_SEC = pd.Timedelta(seconds=1)
FAR = pd.Timestamp("2100-01-01", tz="UTC")


class Vint:
    """Fast as-of lookups into the fundamentals vintage table."""

    def __init__(self, v: pd.DataFrame):
        v = v[v["derivation"] != "annual"].sort_values("avail_utc")
        self.d: dict = {}
        for (tk, it, pe), g in v.groupby(["ticker", "item", "period_end"], sort=False):
            self.d[(tk, it, pd.Timestamp(pe).value)] = (store.ns(g["avail_utc"]), g["value"].to_numpy(float))

    def asof(self, tk, item, pe, tau_ns, inclusive=False) -> float:
        a = self.d.get((tk, item, pd.Timestamp(pe).value))
        if a is None:
            return math.nan
        i = int(np.searchsorted(a[0], tau_ns, side="right" if inclusive else "left")) - 1
        return float(a[1][i]) if i >= 0 else math.nan

    def first(self, tk, item, pe) -> tuple[float, int | None]:
        a = self.d.get((tk, item, pd.Timestamp(pe).value))
        return (float(a[1][0]), int(a[0][0])) if a is not None else (math.nan, None)


def _gm(rev, gp, cogs):
    if math.isnan(rev) or rev <= 0:
        return math.nan
    g = gp if not math.isnan(gp) else (rev - cogs if not math.isnan(cogs) else math.nan)
    m = 100 * g / rev if not math.isnan(g) else math.nan
    return m if not math.isnan(m) and abs(m) < 100 else math.nan


def _lg(a, b):
    return math.log(a) - math.log(b) if a > 0 and b > 0 and not (math.isnan(a) or math.isnan(b)) else math.nan


def quarter_index(rel: pd.DataFrame) -> dict:
    """ticker -> list of (period_start, period_end, release_utc) in order, with year-ago positions checked."""
    out = {}
    for tk, g in rel.groupby("ticker"):
        g = g.sort_values("period_end")
        out[tk] = list(zip(g["period_start"], g["period_end"], g["release_utc"]))
    return out


def _find(qs, target_end, days):
    """Position of the quarter whose end is ~`days` before target_end (+-20 days)."""
    want = pd.Timestamp(target_end) - pd.Timedelta(days=days)
    best = None
    for i, (_, pe, _) in enumerate(qs):
        if abs((pd.Timestamp(pe) - want).days) <= 20:
            best = i
    return best


def build(tickers: list[str] | None = None, start: str = "2013-01-01") -> pd.DataFrame:
    tickers = tickers or target_tickers()
    v = store.load("fundamentals_vintages")
    rel = store.load("releases")
    rel = rel[rel["ticker"].isin(tickers)]
    V = Vint(v)
    fe = FeatureEngine()
    ind_ids = [s["id"] for s in registry()]
    qidx = quarter_index(rel)
    rows = []
    for tk, qs in qidx.items():
        pos = {pd.Timestamp(pe).value: i for i, (_, pe, _) in enumerate(qs)}
        for i, (ps, pe, rel_utc) in enumerate(qs):
            if pd.Timestamp(pe) < pd.Timestamp(start):
                continue
            rev_q, avail_q = V.first(tk, "revenue", pe)
            j4 = _find(qs, pe, 364)
            pe4 = qs[j4][1] if j4 is not None else None
            base = {"ticker": tk, "period_start": ps, "period_end": pe, "release_utc": rel_utc}
            # ---- targets (first reported; year-ago as stated when q was first reported)
            if avail_q is not None and pe4 is not None:
                rev4 = V.asof(tk, "revenue", pe4, avail_q, inclusive=True)
                base["y_rev"] = _lg(rev_q, rev4)
                gm_q = _gm(rev_q, V.first(tk, "gross_profit", pe)[0], V.first(tk, "cogs", pe)[0])
                gm_4 = _gm(rev4, V.asof(tk, "gross_profit", pe4, avail_q, True), V.asof(tk, "cogs", pe4, avail_q, True))
                base["y_dgm"] = gm_q - gm_4
                base["gm_q"] = gm_q
                oi_q = V.first(tk, "operating_income", pe)[0]
                oi_4 = V.asof(tk, "operating_income", pe4, avail_q, True)
                om_q = 100 * oi_q / rev_q if rev_q > 0 and not math.isnan(oi_q) else math.nan
                om_4 = 100 * oi_4 / rev4 if rev4 > 0 and not math.isnan(oi_4) else math.nan
                base["y_dom"] = om_q - om_4
                base["om_q"] = om_q
                base["rev_q"] = rev_q
                base["rev_q4"] = rev4
            else:
                continue
            for h in HORIZONS:
                if i - h < 0:
                    continue
                tau = pd.Timestamp(qs[i - h][2]) - ONE_SEC
                tau_ns = tau.value
                r = dict(base, h=h, origin=tau)
                # last reported quarter as of tau: q-1-h
                k = i - 1 - h
                if k < 0:
                    continue
                pk = qs[k][1]
                jk4 = _find(qs, pk, 364)
                r["last_period_end"] = pk
                rev_k = V.asof(tk, "revenue", pk, tau_ns)
                rev_k4 = V.asof(tk, "revenue", qs[jk4][1], tau_ns) if jk4 is not None else math.nan
                r["g_last"] = _lg(rev_k, rev_k4)
                if jk4 is not None:
                    jk8 = _find(qs, pk, 728)
                    rev_k8 = V.asof(tk, "revenue", qs[jk8][1], tau_ns) if jk8 is not None else math.nan
                    r["g_last_prev_year"] = _lg(rev_k4, rev_k8)
                # growth of the quarter before the last one (for acceleration features)
                if k - 1 >= 0:
                    pk1 = qs[k - 1][1]
                    jk14 = _find(qs, pk1, 364)
                    r["g_last2"] = _lg(V.asof(tk, "revenue", pk1, tau_ns),
                                       V.asof(tk, "revenue", qs[jk14][1], tau_ns) if jk14 is not None else math.nan)
                r["rev_last"] = rev_k
                r["rev4_asof"] = V.asof(tk, "revenue", pe4, tau_ns)
                gm_k = _gm(rev_k, V.asof(tk, "gross_profit", pk, tau_ns), V.asof(tk, "cogs", pk, tau_ns))
                gm_k4 = _gm(rev_k4, V.asof(tk, "gross_profit", qs[jk4][1], tau_ns) if jk4 is not None else math.nan,
                            V.asof(tk, "cogs", qs[jk4][1], tau_ns) if jk4 is not None else math.nan)
                r["dgm_last"] = gm_k - gm_k4
                r["gm_last"] = gm_k
                oi_k = V.asof(tk, "operating_income", pk, tau_ns)
                oi_k4 = V.asof(tk, "operating_income", qs[jk4][1], tau_ns) if jk4 is not None else math.nan
                om_k = 100 * oi_k / rev_k if rev_k > 0 and not math.isnan(oi_k) else math.nan
                om_k4 = 100 * oi_k4 / rev_k4 if rev_k4 > 0 and not math.isnan(oi_k4) else math.nan
                r["dom_last"] = om_k - om_k4
                r["om_last"] = om_k
                # remaining performance obligations at the last reported quarter end (and a year earlier)
                rpo_k = V.asof(tk, "rpo", pk, tau_ns)
                rpo_k4 = V.asof(tk, "rpo", qs[jk4][1], tau_ns) if jk4 is not None else math.nan
                r["rpo_last"] = rpo_k
                r["rpo_g_last"] = _lg(rpo_k, rpo_k4)
                if k - 1 >= 0:
                    rpo_k1 = V.asof(tk, "rpo", qs[k - 1][1], tau_ns)
                    r["rpo_prev"] = rpo_k1
                    r["kappa_last"] = rev_k / rpo_k1 if rpo_k1 and rpo_k1 > 0 else math.nan
                    r["orders_last"] = rpo_k - rpo_k1 + rev_k if not math.isnan(rpo_k) and not math.isnan(rpo_k1) else math.nan
                # operational indicators over the target quarter's window, as of tau
                ps_l = pd.Timestamp(ps) - pd.DateOffset(months=3)
                pe_l = pd.Timestamp(pe) - pd.DateOffset(months=3)
                for iid in ind_ids:
                    f = fe.feature(iid, ps, pe, tau)
                    r[f"x_{iid}"] = f["x"]
                    r[f"cov_{iid}"] = f["coverage"]
                    r[f"stale_{iid}"] = f["stale_m"]
                    # sensitivity: the same indicator one quarter earlier (previous window), as of tau
                    r[f"xl_{iid}"] = fe.feature(iid, ps_l, pe_l, tau)["x"]
                    if h == 0:
                        # look-ahead benchmark: today's revised data, full window, no publication lag
                        r[f"xo_{iid}"] = fe.feature(iid, ps, pe, FAR)["x"]
                rows.append(r)
        print(f"  panel {tk}: {sum(1 for x in rows if x['ticker'] == tk)} rows", flush=True)
    out = pd.DataFrame(rows)
    store.save("panel", out)
    return out
