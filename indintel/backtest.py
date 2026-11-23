"""Walk-forward backtests and the research tables in out/.

Protocol
* Models are refit once per calendar quarter of forecast origins, on panel rows whose *target* was public
  before the earliest origin in that quarter (so no training label is from the future).
* Evaluation covers targets ending 2016-01-01 or later. 2016-2020 is the development period, 2021 onward
  the holdout. The indicator links (config/companies.yaml) and model settings were fixed before the
  holdout was evaluated; nothing is tuned on it.
* Each forecast is compared with the random walk (last reported growth) and the pooled AR baseline.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict

import numpy as np
import pandas as pd

from . import evaluate as ev, store
from .indicators_config import by_id
from .mapping import drivers
from .models import (AR, RW, BacklogAR, BayesComp, Composite, Conversion, GBM, MappedRidge, MarginPriceCost,
                     Standardiser, composite, mapped)
from .paths import OUT

EVAL_START = pd.Timestamp("2016-01-01")
HOLDOUT_START = pd.Timestamp("2021-01-01")
MIN_TRAIN = 40

CASES = {
    "data_center": {"title": "Data center power and cooling", "tickers": ["VRT", "ETN", "MOD", "NVT", "POWL", "BE", "GEV"]},
    "caterpillar": {"title": "Caterpillar and heavy machinery", "tickers": ["CAT", "CMI", "URI"]},
    "grid": {"title": "Electrical equipment and grid investment", "tickers": ["ETN", "HUBB", "GEV", "PWR", "MTZ", "POWL", "NVT"]},
    "backlog": {"title": "Order backlogs and conversion", "tickers": ["CAT", "CMI", "PWR", "MTZ", "POWL", "ROK", "KLAC", "LRCX", "GEV", "ETN"]},
    "semicap": {"title": "Semiconductor equipment", "tickers": ["AMAT", "LRCX", "KLAC"]},
    "all": {"title": "All modelled companies", "tickers": None},
}


def period_label(pe) -> str:
    return "holdout" if pd.Timestamp(pe) >= HOLDOUT_START else "dev"


# ----------------------------------------------------------------------------- walk-forward engine

def walk_forward(panel: pd.DataFrame, make_models, target: str, h: int, start=EVAL_START, window_q: int | None = None,
                 rows_filter=None, min_train: int = MIN_TRAIN, audit: list | None = None) -> pd.DataFrame:
    P = panel[(panel["h"] == h) & panel[target].notna()].copy()
    P["okey"] = P["origin"].dt.tz_convert("UTC").dt.tz_localize(None).dt.to_period("Q")
    test_all = P[P["period_end"] >= start]
    if rows_filter is not None:
        test_all = test_all[rows_filter(test_all)]
    out = []
    for key, test in test_all.groupby("okey", sort=True):
        cutoff = test["origin"].min()
        train = P[P["release_utc"] < cutoff]
        if window_q:
            train = train[train["release_utc"] >= cutoff - pd.Timedelta(days=int(91.3 * window_q))]
        if len(train) < min_train:
            continue
        if audit is not None:
            audit.append({"h": h, "target": target, "origin_q": str(key), "cutoff": cutoff,
                          "max_train_release": train["release_utc"].max(), "n_train": len(train), "n_test": len(test)})
        for m in make_models():
            m.fit(train)
            pr = m.predict(test).reset_index(drop=True)
            base = test[["ticker", "period_end", "origin", "h", target]].reset_index(drop=True).rename(columns={target: "y"})
            base["model"] = m.name
            base["okey"] = str(key)
            out.append(pd.concat([base, pr], axis=1))
    if not out:
        return pd.DataFrame()
    df = pd.concat(out, ignore_index=True)
    df["err"] = df["yhat"] - df["y"]
    df["period"] = df["period_end"].map(period_label)
    return df


def revenue_models():
    return [RW(), AR(), Composite(), BayesComp(), MappedRidge(), GBM(), BacklogAR(), Conversion()]


# ----------------------------------------------------------------------------- summaries

def summary(preds: pd.DataFrame, tickers=None, base: str = "RW", ar: str = "AR") -> pd.DataFrame:
    p = preds if tickers is None else preds[preds["ticker"].isin(tickers)]
    rows = []
    for (h, per), g in list(p.groupby(["h", "period"])) + [((hh, "all"), gg) for hh, gg in p.groupby("h")]:
        wide = g.pivot_table(index=["ticker", "period_end", "okey"], columns="model", values="yhat", aggfunc="first")
        y = g.drop_duplicates(["ticker", "period_end"]).set_index(["ticker", "period_end"])["y"]
        wide = wide.reset_index()
        wide["y"] = wide.set_index(["ticker", "period_end"]).index.map(y)
        for m in [c for c in wide.columns if c not in ("ticker", "period_end", "okey", "y")]:
            e = wide[m] - wide["y"]
            met = ev.metrics(e.to_numpy())
            eb = wide[base] - wide["y"]
            ea = wide[ar] - wide["y"]
            dm_b = ev.dm_test(e, eb, wide["okey"])
            dm_a = ev.dm_test(e, ea, wide["okey"])
            cw = ev.clark_west(wide["y"], wide[ar], wide[m], wide["okey"]) if m not in (base, ar) else {"cw_stat": math.nan, "cw_p": math.nan}
            rows.append({"h": h, "period": per, "model": m, **met,
                         "mae_vs_rw": met["mae"] / ev.metrics(eb.to_numpy())["mae"],
                         "mae_vs_ar": met["mae"] / ev.metrics(ea.to_numpy())["mae"],
                         "dm_p_vs_rw": dm_b["dm_p"], "dm_p_vs_ar": dm_a["dm_p"], "cw_p_vs_ar": cw["cw_p"],
                         "oos_r2_vs_ar": ev.oos_r2(wide["y"], wide[m], wide[ar]),
                         "accel_hit": ev.hit_rate(wide["y"] - wide[base], wide[m] - wide[base]) if m != base else math.nan,
                         "n_quarters": dm_b["n_periods"]})
    out = pd.DataFrame(rows)
    out["period"] = pd.Categorical(out["period"], ["dev", "holdout", "all"])
    return out.sort_values(["h", "period", "mae"]).reset_index(drop=True)


def by_company(preds: pd.DataFrame, h: int = 0) -> pd.DataFrame:
    p = preds[preds["h"] == h]
    rows = []
    for (tk, m), g in p.groupby(["ticker", "model"]):
        met = ev.metrics(g["err"].to_numpy())
        rows.append({"ticker": tk, "model": m, **met})
    t = pd.DataFrame(rows)
    rw = t[t["model"] == "RW"].set_index("ticker")["mae"]
    t["mae_vs_rw"] = t["mae"] / t["ticker"].map(rw)
    return t.sort_values(["ticker", "mae"])


def coverage_table(preds: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (h, m, per), g in preds[preds["lo"].notna()].groupby(["h", "model", "period"]):
        rows.append({"h": h, "model": m, "period": per, "coverage_80": ev.coverage(g["y"], g["lo"], g["hi"]), "n": len(g)})
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- single-indicator lead analysis

class SingleInd(Composite):
    """y = a + b * last growth + c * z_k on the companies mapped to indicator k only."""

    def __init__(self, k: str, prefix: str = "x_"):
        super().__init__(prefix=prefix)
        self.k = k
        self.name = f"ind:{k}"
        self.inds = {tk: [k] for tk, v in mapped("demand").items() if k in v}


def indicator_lead_table(panel: pd.DataFrame) -> pd.DataFrame:
    """For every mapped (company, indicator) pair and horizon: out-of-sample gain over AR, Clark-West p."""
    d = drivers()
    d = d[d["role"] == "demand"]
    rows = []
    for k, g in d.groupby("indicator"):
        tks = sorted(set(g["ticker"]))
        filt = (lambda df, tks=tks: df["ticker"].isin(tks))
        for h in (0, 1, 2):
            sub = panel[panel["ticker"].isin(tks)]
            pr = walk_forward(sub, lambda k=k: [AR(), SingleInd(k)], "y_rev", h, rows_filter=filt, min_train=20)
            if pr.empty:
                continue
            w = pr.pivot_table(index=["ticker", "period_end", "okey"], columns="model", values="yhat").reset_index()
            y = pr.drop_duplicates(["ticker", "period_end"]).set_index(["ticker", "period_end"])["y"]
            w["y"] = w.set_index(["ticker", "period_end"]).index.map(y)
            col = f"ind:{k}"
            # rows where the indicator was actually available (the model falls back to AR otherwise)
            zrows = pr[(pr["model"] == col) & pr["z"].notna()][["ticker", "period_end"]]
            w = w.merge(zrows, on=["ticker", "period_end"])
            for tk, gg in list(w.groupby("ticker")) + [("pooled", w)]:
                if len(gg) < 8:
                    continue
                cw = ev.clark_west(gg["y"], gg["AR"], gg[col], gg["okey"]) if tk == "pooled" else {"cw_p": math.nan}
                rows.append({"indicator": k, "ticker": tk, "h": h, "n": len(gg),
                             "mae_ar": float(np.mean(np.abs(gg["AR"] - gg["y"]))),
                             "mae_ind": float(np.mean(np.abs(gg[col] - gg["y"]))),
                             "oos_r2_vs_ar": ev.oos_r2(gg["y"], gg[col], gg["AR"]), "cw_p": cw["cw_p"]})
    return pd.DataFrame(rows)


def lag_correlations(panel: pd.DataFrame, max_k: int = 4) -> pd.DataFrame:
    """Descriptive lead/lag: correlation of revenue growth (first reported) with each mapped indicator's
    year-over-year growth k quarters earlier (k > 0: indicator leads). Both raw and after removing each
    series' own AR(1) dynamics (pre-whitening), because two smooth trending series correlate at every lag."""
    from .indicators import FeatureEngine
    fe = FeatureEngine()
    d = drivers()
    d = d[d["role"] == "demand"]
    rev = panel[panel["h"] == 0][["ticker", "period_end", "y_rev"]].drop_duplicates(["ticker", "period_end"])
    rows = []
    hist_cache = {}
    for (tk, k), _ in d.groupby(["ticker", "indicator"]):
        r = rev[rev["ticker"] == tk].copy()
        if len(r) < 12:
            continue
        if k not in hist_cache:
            hq = fe.quarterly_history(k, view="first")
            if hq.empty:
                hist_cache[k] = None
            else:
                hq["q"] = pd.to_datetime(hq["quarter"]).dt.to_period("Q")
                hist_cache[k] = hq.set_index("q")["yoy"].astype(float)
        x = hist_cache[k]
        if x is None:
            continue
        r["q"] = (r["period_end"] - pd.Timedelta(days=40)).dt.to_period("Q")
        r = r.set_index("q").sort_index()
        for lag in range(-max_k, max_k + 1):
            xs = x.reindex(r.index - lag)
            ok = np.isfinite(xs.to_numpy()) & np.isfinite(r["y_rev"].to_numpy())
            if ok.sum() < 10:
                continue
            a, b = r["y_rev"].to_numpy()[ok], xs.to_numpy()[ok]
            c = float(np.corrcoef(a, b)[0, 1])
            pw = math.nan
            if ok.sum() >= 14:
                da, db = np.diff(a), np.diff(b)
                pw = float(np.corrcoef(da, db)[0, 1]) if np.std(da) > 0 and np.std(db) > 0 else math.nan
            rows.append({"ticker": tk, "indicator": k, "lead_q": lag, "corr": c, "corr_diff": pw, "n": int(ok.sum())})
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- inflections

def turning_points(s: pd.Series, min_amp: float) -> list[tuple[pd.Period, str]]:
    """Peaks and troughs of a smoothed series: local extremum of a centred 3-quarter mean, with at least
    `min_amp` rise and fall within four quarters on each side."""
    v = s.rolling(3, center=True, min_periods=2).mean()
    out = []
    idx = list(v.index)
    for i in range(1, len(idx) - 1):
        win = v.iloc[max(0, i - 4): i + 5]
        x = v.iloc[i]
        if not np.isfinite(x):
            continue
        left, right = v.iloc[max(0, i - 4): i], v.iloc[i + 1: i + 5]
        if len(left) == 0 or len(right) == 0:
            continue
        if x == win.max() and x - left.min() >= min_amp and x - right.min() >= min_amp:
            out.append((idx[i], "peak"))
        if x == win.min() and left.max() - x >= min_amp and right.max() - x >= min_amp:
            out.append((idx[i], "trough"))
    return out


def inflection_table(panel: pd.DataFrame) -> pd.DataFrame:
    """Do turning points in a company's composite indicator precede turning points in its revenue growth?"""
    p = panel[(panel["h"] == 0)].drop_duplicates(["ticker", "period_end"]).copy()
    inds = mapped("demand")
    std = Standardiser(p, sorted({k for v in inds.values() for k in v}), "xo_")
    p["comp"] = composite(p, std, inds)
    rows = []
    for tk, g in p.groupby("ticker"):
        g = g.sort_values("period_end")
        g["q"] = (g["period_end"] - pd.Timedelta(days=40)).dt.to_period("Q")
        g = g.set_index("q")
        rtp = turning_points(g["y_rev"] * 100, 5.0)
        itp = turning_points(g["comp"], 0.5)
        for q, kind in rtp:
            cands = [(q - qi).n for qi, k2 in itp if k2 == kind and -2 <= (q - qi).n <= 6]
            lead = min(cands, key=abs) if cands else None
            rows.append({"ticker": tk, "revenue_turn": str(q), "kind": kind, "matched": lead is not None,
                         "indicator_lead_q": lead, "n_indicator_turns": len(itp)})
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- orders (implied from RPO)

def orders_table(panel: pd.DataFrame) -> pd.DataFrame:
    """Implied order intake = change in RPO + revenue; does the mapped composite nowcast its growth?"""
    from . import fundamentals as fu
    v = store.load("fundamentals_vintages")
    fr = fu.first_reported(v)
    w = fr[fr["item"].isin(["rpo", "revenue"]) & (fr["derivation"] != "annual")].pivot_table(
        index=["ticker", "period_end"], columns="item", values="value").reset_index().sort_values(["ticker", "period_end"])
    w["rpo_prev"] = w.groupby("ticker")["rpo"].shift(1)
    gap = w.groupby("ticker")["period_end"].diff().dt.days
    w["orders"] = np.where(gap.between(80, 100), w["rpo"] - w["rpo_prev"] + w["revenue"], np.nan)
    w["orders_ttm"] = w.groupby("ticker")["orders"].transform(lambda s: s.rolling(4, min_periods=4).sum())
    w["y_ord"] = np.log(w["orders_ttm"].where(w["orders_ttm"] > 0)) - np.log(
        w.groupby("ticker")["orders_ttm"].shift(4).where(lambda s: s > 0))
    w["ord_last"] = w.groupby("ticker")["y_ord"].shift(1)
    p = panel[panel["h"] == 0].merge(w[["ticker", "period_end", "y_ord", "ord_last", "orders"]], on=["ticker", "period_end"], how="inner")
    p = p[p["y_ord"].notna() & p["ord_last"].notna()]
    if p.empty:
        return pd.DataFrame(), pd.DataFrame()
    pr = walk_forward(p, lambda: [RW("y_ord", "ord_last"), AR("y_ord", "ord_last"), Composite(target="y_ord", last="ord_last")],
                      "y_ord", 0, min_train=30)
    if pr.empty:
        return pd.DataFrame(), w
    return summary(pr), w


# ----------------------------------------------------------------------------- backlog conversion study

def backlog_study(panel: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Backlog-based nowcasts on the quarters where remaining performance obligations (RPO) are known.

    Pre-specified: BacklogAR, Conversion (pooled static kappa). Added after the first holdout run, and labelled
    so everywhere: ConvLast (last observed conversion ratio) and ConvRPOGrowth (revenue grows with RPO)."""
    from .models import ConvLast, ConvRPOGrowth
    ok = panel["rpo_last"].gt(0) & panel["rpo_prev"].gt(0) & panel["rpo_g_last"].notna() & panel["kappa_last"].gt(0)
    sub = panel[ok]
    filt = lambda df: df["rpo_last"].gt(0) & df["rpo_prev"].gt(0) & df["rpo_g_last"].notna() & df["kappa_last"].gt(0)  # noqa: E731
    pr = walk_forward(panel, lambda: [RW(), AR(), BayesComp(), BacklogAR(), Conversion(), ConvLast(), ConvRPOGrowth()],
                      "y_rev", 0, rows_filter=filt, min_train=MIN_TRAIN)
    s = summary(pr)
    # conversion ratio (quarterly revenue / RPO at the previous quarter end) by company: level, dispersion, drift
    rows = []
    for tk, g in sub[sub["h"] == 0].drop_duplicates(["ticker", "period_end"]).groupby("ticker"):
        g = g.sort_values("period_end")
        k = (g["rev_q"] / g["rpo_last"]).to_numpy(float)
        k = k[np.isfinite(k) & (k > 0)]
        if len(k) < 6:
            continue
        t = (g["period_end"] - g["period_end"].min()).dt.days.to_numpy()[-len(k):] / 365.25
        slope = float(np.polyfit(t, np.log(k), 1)[0]) if len(k) >= 6 else math.nan
        rows.append({"ticker": tk, "quarters": len(k), "first": str(g["period_end"].min().date()),
                     "median_kappa": float(np.median(k)), "cv_kappa": float(np.std(k) / np.mean(k)),
                     "log_kappa_trend_per_year": slope, "rpo_growth_median": float(g["rpo_g_last"].median())})
    return s, pd.DataFrame(rows)


# ----------------------------------------------------------------------------- sensitivity

def sensitivity(panel: pd.DataFrame) -> pd.DataFrame:
    variants = {
        "baseline (point-in-time)": dict(prefix="x_", window_q=None, filt=None),
        "look-ahead: revised data, no publication lag": dict(prefix="xo_", window_q=None, filt=None),
        "indicator one quarter earlier": dict(prefix="xl_", window_q=None, filt=None),
        "rolling 20-quarter training window": dict(prefix="x_", window_q=20, filt=None),
        "excluding 2020": dict(prefix="x_", window_q=None, filt=lambda df: df["period_end"].dt.year != 2020),
        "only quarters with full indicator coverage": dict(prefix="x_", window_q=None, filt="cov"),
    }
    inds = mapped("demand")
    rows = []
    for name, v in variants.items():
        filt = v["filt"]
        if filt == "cov":
            def filt(df, inds=inds):
                ok = np.zeros(len(df), bool)
                tk = df["ticker"].to_numpy()
                for t in np.unique(tk):
                    ks = [f"cov_{k}" for k in inds.get(t, []) if f"cov_{k}" in df]
                    if ks:
                        m = (df.loc[tk == t, ks].fillna(0).mean(axis=1) >= 0.66).to_numpy()
                        ok[np.nonzero(tk == t)[0]] = m
                return ok
        pr = walk_forward(panel, lambda p=v["prefix"]: [RW(), AR(), Composite(prefix=p), BayesComp(prefix=p)], "y_rev", 0,
                          window_q=v["window_q"], rows_filter=filt)
        s = summary(pr)
        s = s[(s["h"] == 0) & s["model"].isin(["Composite", "BayesComp"])]
        for r in s.itertuples(index=False):
            rows.append({"variant": name, "model": r.model, "period": r.period, "n": r.n, "mae": r.mae,
                         "mae_vs_rw": r.mae_vs_rw, "mae_vs_ar": r.mae_vs_ar, "dm_p_vs_ar": r.dm_p_vs_ar, "cw_p_vs_ar": r.cw_p_vs_ar})
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- main

def main(period: str = "all") -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    panel = store.load("panel")
    audit: list = []
    preds = []
    for h in (0, 1, 2):
        pr = walk_forward(panel, revenue_models, "y_rev", h, audit=audit)
        preds.append(pr)
        print(f"  revenue h={h}: {pr['ticker'].nunique()} companies, {len(pr) // max(1, pr['model'].nunique())} forecasts", flush=True)
    preds = pd.concat(preds, ignore_index=True)
    store.save("preds_revenue", preds)
    aud = pd.DataFrame(audit)
    assert (aud["max_train_release"] < aud["cutoff"]).all(), "training label released after the forecast origin"
    aud.to_csv(OUT / "pit_audit_walkforward.csv", index=False)

    tables = {}
    for case, spec in CASES.items():
        s = summary(preds, spec["tickers"])
        s.insert(0, "case", case)
        tables[case] = s
    summ = pd.concat(tables.values(), ignore_index=True)
    summ.to_csv(OUT / "revenue_summary.csv", index=False)
    by_company(preds).to_csv(OUT / "revenue_by_company.csv", index=False)
    coverage_table(preds).to_csv(OUT / "interval_coverage.csv", index=False)
    print(summ[(summ["case"] == "all") & (summ["h"] == 0)][["period", "model", "n", "mae", "mae_vs_rw", "mae_vs_ar", "dm_p_vs_ar", "cw_p_vs_ar"]].to_string(index=False), flush=True)

    # margins (nowcast horizon)
    mpreds = []
    for tgt, last in (("y_dgm", "dgm_last"), ("y_dom", "dom_last")):
        pr = walk_forward(panel[panel[last].notna()], lambda t=tgt, l=last: [RW(t, l), AR(t, l), MarginPriceCost(t, l)], tgt, 0)
        if not pr.empty:
            pr["target"] = tgt
            mpreds.append(pr)
    if mpreds:
        mp = pd.concat(mpreds, ignore_index=True)
        store.save("preds_margin", mp)
        ms = []
        for tgt, g in mp.groupby("target"):
            s = summary(g)
            s.insert(0, "target", tgt)
            ms.append(s)
        pd.concat(ms).to_csv(OUT / "margin_summary.csv", index=False)

    print("  lead analysis ...", flush=True)
    indicator_lead_table(panel).to_csv(OUT / "indicator_leads.csv", index=False)
    lag_correlations(panel).to_csv(OUT / "lag_correlations.csv", index=False)
    inflection_table(panel).to_csv(OUT / "inflections.csv", index=False)
    osum, _ = orders_table(panel)
    if not osum.empty:
        osum.to_csv(OUT / "orders_summary.csv", index=False)
    print("  backlog study ...", flush=True)
    bs, bk = backlog_study(panel)
    bs.to_csv(OUT / "backlog_study.csv", index=False)
    bk.to_csv(OUT / "backlog_kappa.csv", index=False)
    print("  sensitivity ...", flush=True)
    sensitivity(panel).to_csv(OUT / "sensitivity.csv", index=False)
    meta = {"eval_start": str(EVAL_START.date()), "holdout_start": str(HOLDOUT_START.date()),
            "n_panel_rows": int(len(panel)), "companies": sorted(panel["ticker"].unique().tolist()),
            "last_target_quarter": str(panel["period_end"].max().date())}
    (OUT / "backtest_meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
