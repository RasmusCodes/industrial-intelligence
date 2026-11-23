"""Static JSON bundle for the dashboard (website/apps/industrial-intelligence/data/).

Everything the page shows is precomputed here, so the page needs no server and no access to this
repository: company financials (first reported and latest), segment revenue, mapped indicators with their
provenance, lead/lag statistics, walk-forward forecasts and errors, live forecasts for the next three
quarters with the coefficients the page needs to recompute them under user assumptions, and the timeline.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from . import fundamentals as fu, store
from .indicators import FeatureEngine
from .indicators_config import by_id, registry
from .mapping import companies, load_profiles
from .models import AR, BayesComp, Conversion, MarginPriceCost, Standardiser, composite, mapped
from .panel import Vint, _find, _gm, _lg
from .paths import CONFIG, OUT, ROOT
from .universe import raw as uraw

DEFAULT_OUT = ROOT.parent / "website" / "apps" / "industrial-intelligence" / "data"
SOURCES_FILE = CONFIG / "sources.yaml"


def _r(x, nd=4):
    if x is None:
        return None
    try:
        if isinstance(x, (float, np.floating)) and not math.isfinite(float(x)):
            return None
        return round(float(x), nd)
    except (TypeError, ValueError):
        return None


def _d(x):
    if x is None or (isinstance(x, float) and math.isnan(x)) or pd.isna(x):
        return None
    return pd.Timestamp(x).strftime("%Y-%m-%d")


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return _r(o, 6)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (pd.Timestamp, datetime)):
        return _d(o)
    return o


# ----------------------------------------------------------------------------- live forecasts

def live_forecasts(panel: pd.DataFrame, fe: FeatureEngine, V: Vint, rel: pd.DataFrame, now: pd.Timestamp) -> dict:
    """Next three quarters per company, from models fit on every target released before `now`."""
    inds = mapped("demand")
    out = {}
    fitted = {}
    for h in (0, 1, 2):
        tr = panel[(panel["h"] == h) & panel["y_rev"].notna() & (panel["release_utc"] < now)]
        bc = BayesComp().fit(tr)
        ar = AR().fit(tr)
        cv = Conversion().fit(tr)
        fitted[h] = (bc, ar, cv)
    trm = panel[(panel["h"] == 0) & panel["y_dgm"].notna() & panel["dgm_last"].notna() & (panel["release_utc"] < now)]
    mpc = MarginPriceCost("y_dgm", "dgm_last").fit(trm) if len(trm) > 40 else None
    for tk, g in rel.groupby("ticker"):
        g = g.sort_values("period_end")
        qs = list(zip(g["period_start"], g["period_end"], g["release_utc"]))
        last_ps, last_pe, _ = qs[-1]
        rows = []
        lp = pd.Timestamp(last_pe)
        month_end = lp == lp + pd.offsets.MonthEnd(0)
        for step, h in ((1, 0), (2, 1), (3, 2)):
            # calendar quarters stay on month ends; 52/53-week fiscal quarters step by 13 weeks
            pe = lp + pd.offsets.MonthEnd(3 * step) if month_end else lp + pd.Timedelta(weeks=13 * step)
            prev_end = lp + pd.offsets.MonthEnd(3 * (step - 1)) if month_end else lp + pd.Timedelta(weeks=13 * (step - 1))
            ps = prev_end + pd.Timedelta(days=1)
            j4 = _find(qs, pe, 364)
            if j4 is None:
                continue
            pe4 = qs[j4][1]
            rev4 = V.asof(tk, "revenue", pe4, now.value)
            jl4 = _find(qs, last_pe, 364)
            g_last = _lg(V.asof(tk, "revenue", last_pe, now.value),
                         V.asof(tk, "revenue", qs[jl4][1], now.value) if jl4 is not None else math.nan)
            row = {"ticker": tk, "period_end": pe, "period_start": ps, "h": h, "g_last": g_last, "rev4_asof": rev4,
                   "rpo_last": V.asof(tk, "rpo", last_pe, now.value)}
            feats = []
            for k in inds.get(tk, []):
                f = fe.feature(k, ps, pe, now)
                row[f"x_{k}"] = f["x"]
                feats.append((k, f))
            df = pd.DataFrame([row])
            bc, ar, cv = fitted[h]
            pb = bc.predict(df).iloc[0]
            pa = ar.predict(df).iloc[0]
            pc = cv.predict(df).iloc[0]
            ind_rows = []
            for k, f in feats:
                st = bc.std.stats.get(k)
                ind_rows.append({"id": k, "x": f["x"], "coverage": f["coverage"], "stale_m": f["stale_m"],
                                 "mu": st[0] if st else None, "sd": st[1] if st else None})
            a, b, _ = bc.beta
            rows.append({
                "period_end": pe, "h": h, "rev_year_ago": rev4, "g_last": g_last,
                "yhat_bayes": pb["yhat"], "lo": pb["lo"], "hi": pb["hi"], "yhat_ar": pa["yhat"],
                "yhat_conversion": pc["yhat"] if bool(pc.get("used_rpo", False)) else None,
                "params": {"a": a, "b": b, "c": bc.slopes.get(tk, bc.c_pool), "c_pool": bc.c_pool,
                           "sd": math.sqrt(bc.resvar.get(tk, bc.pooled_s2)), "ar_a": ar.beta[0], "ar_b": ar.beta[1],
                           "log_kappa": cv.post.get(tk, (None, None))[0], "rpo_last": row["rpo_last"]},
                "z": pb.get("z"), "indicators": ind_rows})
        # margin: next quarter gross-margin change from the price-cost model
        margin = None
        if mpc is not None and rows:
            pk, pk4 = last_pe, (qs[_find(qs, last_pe, 364)][1] if _find(qs, last_pe, 364) is not None else None)
            if pk4 is not None:
                gm_k = _gm(V.asof(tk, "revenue", pk, now.value), V.asof(tk, "gross_profit", pk, now.value), V.asof(tk, "cogs", pk, now.value))
                gm_k4 = _gm(V.asof(tk, "revenue", pk4, now.value), V.asof(tk, "gross_profit", pk4, now.value), V.asof(tk, "cogs", pk4, now.value))
                nxt = rows[0]
                j4 = _find(qs, nxt["period_end"], 364)
                pe4 = qs[j4][1] if j4 is not None else None
                gm_4 = _gm(V.asof(tk, "revenue", pe4, now.value), V.asof(tk, "gross_profit", pe4, now.value),
                           V.asof(tk, "cogs", pe4, now.value)) if pe4 is not None else math.nan
                if np.isfinite(gm_k) and np.isfinite(gm_k4) and np.isfinite(gm_4):
                    df = pd.DataFrame([{"ticker": tk, "dgm_last": gm_k - gm_k4, "g_last": nxt["g_last"],
                                        **{f"x_{k}": fe.feature(k, rows[0]["period_end"] - pd.DateOffset(months=3) + pd.Timedelta(days=1),
                                                                rows[0]["period_end"], now)["x"]
                                           for k in set(sum(mapped("pricing").values(), []) + sum(mapped("cost").values(), []))}}])
                    p = mpc.predict(df).iloc[0]["yhat"]
                    pz, cz = mpc._design(df)
                    margin = {"gm_last": gm_k, "dgm_last": gm_k - gm_k4, "gm_year_ago_next": gm_4, "dgm_hat": p,
                              "gm_hat_next": gm_4 + p, "beta": list(mpc.beta), "price_z": float(pz[0]),
                              "cost_z": float(cz[0]), "g_last": nxt["g_last"]}
        out[tk] = {"as_of": now, "last_reported": last_pe, "quarters": rows, "margin": margin}
    return out


# ----------------------------------------------------------------------------- per-company bundle

def company_json(tk: str, ctx: dict) -> dict:
    spec = next((c for c in companies() if c["ticker"] == tk), {})
    uni = {r["ticker"]: r for r in uraw()["targets"] + uraw().get("annual_only", []) + uraw().get("mapping_only", [])}
    profile = ctx["profiles"].get(tk, {})
    reg = by_id()
    out = {"ticker": tk, "name": uni.get(tk, {}).get("name", tk), "sectors": spec.get("sectors", []),
           "mapping_only": tk in {r["ticker"] for r in uraw().get("mapping_only", [])},
           "annual_only": tk in {r["ticker"] for r in uraw().get("annual_only", [])},
           "profile": profile, "drivers": spec.get("drivers", [])}
    at = ctx["texts"]
    if profile and not at.empty:
        hit = at[at["path"] == profile.get("document")]
        if len(hit):
            h = hit.iloc[0]
            out["profile_source"] = {"form": h["form"], "filed": h["filing_date"], "url": h["url"]}
    # financials
    fin = ctx["fin"]
    f = fin[fin["ticker"] == tk].sort_values("period_end")
    rel = ctx["rel"][ctx["rel"]["ticker"] == tk].set_index("period_end")["release_utc"]
    rows = []
    for r in f.itertuples(index=False):
        rows.append({"q": r.period_end, "rev": r.revenue_latest, "rev_first": r.revenue_first,
                     "gm": r.gm, "om": r.om, "rpo": r.rpo, "release": rel.get(r.period_end)})
    out["financials"] = rows
    # annual (20-F filers)
    if out["annual_only"]:
        v = ctx["vint"]
        a = fu.latest(v[(v["ticker"] == tk) & (v["derivation"] == "annual")])
        out["annual"] = [{"year_end": r.period_end, "item": r.item, "value": r.value} for r in a.itertuples(index=False)]
    # segments
    s = ctx["seg"][(ctx["seg"]["ticker"] == tk) & (ctx["seg"]["item"] == "revenue")]
    if not s.empty:
        names = sorted(s["segment"].unique(), key=lambda n: -s[s["segment"] == n]["value_latest"].tail(4).sum())
        piv = s.pivot_table(index="period_end", columns="segment", values="value_latest", aggfunc="first").sort_index()
        out["segments"] = {"names": names, "rows": [{"q": q, "v": {n: piv.loc[q, n] for n in names if n in piv and pd.notna(piv.loc[q, n])}}
                                                    for q in piv.index]}
    # indicators
    lags = ctx["lags"]
    leads = ctx["leads"]
    ind_out = []
    for d in spec.get("drivers", []):
        k = d["indicator"]
        r = reg[k]
        mh = ctx["monthly"].get(k)
        qh = ctx["quarterly"].get(k)
        lc = lags[(lags["ticker"] == tk) & (lags["indicator"] == k)] if not lags.empty else pd.DataFrame()
        ld = leads[(leads["ticker"] == tk) & (leads["indicator"] == k)] if not leads.empty else pd.DataFrame()
        ind_out.append({
            "id": k, "name": r["name"], "unit": r["unit"], "source": r["source"], "vintage_kind": r["vintage_kind"],
            "pub_lag_days": r.get("pub_lag_days"), "link": r["link"], "role": d["role"], "segment": d.get("segment"),
            "lead": d.get("lead"), "why": d.get("why"),
            "monthly": [[p, v] for p, v in mh] if mh is not None else [],
            "quarterly": [[q, y] for q, y in qh] if qh is not None else [],
            "lag_corr": [{"lead_q": int(x.lead_q), "corr": x.corr, "corr_diff": x.corr_diff, "n": int(x.n)} for x in lc.itertuples(index=False)],
            "incremental": [{"h": int(x.h), "oos_r2": x.oos_r2_vs_ar, "mae_ind": x.mae_ind, "mae_ar": x.mae_ar, "n": int(x.n)}
                            for x in ld.itertuples(index=False)]})
    out["indicators"] = ind_out
    # walk-forward forecasts
    p = ctx["preds"][ctx["preds"]["ticker"] == tk]
    if not p.empty:
        bt = []
        for (pe, h), g in p.groupby(["period_end", "h"]):
            preds = {m: v for m, v in zip(g["model"], g["yhat"])}
            bc = g[g["model"] == "BayesComp"]
            bt.append({"q": pe, "h": int(h), "y": g["y"].iloc[0], "p": preds,
                       "lo": bc["lo"].iloc[0] if len(bc) else None, "hi": bc["hi"].iloc[0] if len(bc) else None,
                       "z": bc["z"].iloc[0] if len(bc) else None})
        out["backtest"] = sorted(bt, key=lambda x: (x["h"], x["q"]))
        summ = []
        for (h, m, per), g in list(p.groupby(["h", "model", "period"])) + [((hh, mm, "all"), gg) for (hh, mm), gg in p.groupby(["h", "model"])]:
            e = g["err"].to_numpy(float)
            e = e[np.isfinite(e)]
            if len(e):
                summ.append({"h": int(h), "model": m, "period": per, "n": int(len(e)), "mae": float(np.mean(np.abs(e))),
                             "bias": float(np.mean(e))})
        out["backtest_summary"] = summ
    mp = ctx["mpreds"]
    if not mp.empty:
        mm = mp[mp["ticker"] == tk]
        out["margin_backtest"] = [{"q": pe, "target": t, "y": g["y"].iloc[0], "p": dict(zip(g["model"], g["yhat"]))}
                                  for (pe, t), g in mm.groupby(["period_end", "target"])]
    out["forecast"] = ctx["live"].get(tk)
    ev = ctx["events"]
    if not ev.empty:
        e = ev[ev["ticker"] == tk].head(160)
        out["timeline"] = [{"date": r.date, "kind": r.kind, "title": r.title, "detail": r.detail, "source": r.source,
                            "url": r.url} for r in e.itertuples(index=False)]
    return _clean(out)


def build_context() -> dict:
    v = store.load("fundamentals_vintages")
    first = fu.quarterly_wide(v, "first")
    lat = fu.quarterly_wide(v, "latest")
    fin = lat.merge(first[["ticker", "period_end", "revenue"]], on=["ticker", "period_end"], suffixes=("_latest", "_first"), how="left")
    fin["gm"] = fu.gross_margin(fin["revenue_latest"], fin.get("gross_profit"), fin.get("cogs"))
    fin["om"] = 100 * fin["operating_income"] / fin["revenue_latest"].where(fin["revenue_latest"] > 0)
    fin = fin[fin["period_end"] >= "2012-01-01"]
    fe = FeatureEngine()
    monthly, quarterly = {}, {}
    start = pd.Timestamp("2013-01-01")
    for spec in registry():
        k = spec["id"]
        mh = fe.monthly_history(k)
        mh = mh[mh["period"] >= start]
        monthly[k] = [(p, v) for p, v in zip(mh["period"], mh["level"]) if np.isfinite(v)]
        qh = fe.quarterly_history(k, view="first")
        qh = qh[qh["quarter"] >= start]
        quarterly[k] = [(q, y) for q, y in zip(qh["quarter"], qh["yoy"]) if np.isfinite(y)]
    panel = store.load("panel")
    rel = store.load("releases")
    now = pd.Timestamp(datetime.now(timezone.utc))
    live = live_forecasts(panel, fe, Vint(v), rel, now)
    def rd(name):
        p = OUT / name
        return pd.read_csv(p) if p.exists() else pd.DataFrame()
    return {"vint": v, "fin": fin, "seg": store.load("segments"), "rel": rel, "preds": store.load("preds_revenue"),
            "mpreds": store.load("preds_margin", missing_ok=True), "events": store.load("events", missing_ok=True),
            "lags": rd("lag_correlations.csv"), "leads": rd("indicator_leads.csv"), "monthly": monthly,
            "quarterly": quarterly, "live": live, "profiles": load_profiles(), "now": now,
            "texts": store.load("annual_texts", missing_ok=True)}


def index_json(ctx: dict) -> dict:
    u = uraw()
    comps = []
    for r in u["targets"] + u.get("annual_only", []) + u.get("mapping_only", []):
        tk = r["ticker"]
        comps.append({"ticker": tk, "name": r["name"], "sectors": r.get("sectors", []),
                      "kind": "modelled" if r in u["targets"] else ("annual" if r in u.get("annual_only", []) else "mapping")})
    srcs = yaml.safe_load(SOURCES_FILE.read_text(encoding="utf-8"))["sources"] if SOURCES_FILE.exists() else []
    obs = store.load("observations")
    span = obs.groupby("series_id").agg(first=("period", "min"), last=("period", "max"), last_avail=("available_at", "max"))
    inds = []
    for s in registry():
        sid = s["id"]
        if s["source"] == "sec_panel":
            sub = span[span.index.str.startswith(sid + ":")]
            fs, ls = (sub["first"].min(), sub["last"].max()) if len(sub) else (None, None)
        else:
            fs, ls = (span.loc[sid, "first"], span.loc[sid, "last"]) if sid in span.index else (None, None)
        inds.append({"id": sid, "name": s["name"], "unit": s["unit"], "source": s["source"], "vintage_kind": s["vintage_kind"],
                     "pub_lag_days": s.get("pub_lag_days"), "sectors": s["sectors"], "link": s["link"],
                     "first": fs, "last": ls, "has_data": fs is not None})
    def rd(name):
        p = OUT / name
        return pd.read_csv(p).to_dict("records") if p.exists() else []
    return _clean({"generated": ctx["now"], "companies": comps, "indicators": inds, "sources": srcs,
                   "results": {"revenue": rd("revenue_summary.csv"), "margin": rd("margin_summary.csv"),
                               "orders": rd("orders_summary.csv"), "sensitivity": rd("sensitivity.csv"),
                               "returns": rd("returns_tests.csv"), "coverage": rd("interval_coverage.csv"),
                               "inflections": rd("inflections.csv")},
                   "meta": json.loads((OUT / "backtest_meta.json").read_text()) if (OUT / "backtest_meta.json").exists() else {}})


def main(outdir: str | None = None) -> None:
    out = Path(outdir) if outdir else DEFAULT_OUT
    out.mkdir(parents=True, exist_ok=True)
    ctx = build_context()
    idx = index_json(ctx)
    (out / "index.json").write_text(json.dumps(idx, separators=(",", ":")), encoding="utf-8")
    u = uraw()
    for r in u["targets"] + u.get("annual_only", []) + u.get("mapping_only", []):
        js = company_json(r["ticker"], ctx)
        (out / f"{r['ticker'].replace('.', '_')}.json").write_text(json.dumps(js, separators=(",", ":")), encoding="utf-8")
    # live forecasts also kept in the repository for reference
    rows = []
    for tk, f in ctx["live"].items():
        for q in f["quarters"]:
            rows.append({"ticker": tk, "period_end": q["period_end"], "h": q["h"], "g_last": q["g_last"],
                         "yhat_bayes": q["yhat_bayes"], "lo80": q["lo"], "hi80": q["hi"], "yhat_ar": q["yhat_ar"],
                         "rev_year_ago": q["rev_year_ago"],
                         "rev_hat": q["rev_year_ago"] * math.exp(q["yhat_bayes"]) if q["rev_year_ago"] and np.isfinite(q["yhat_bayes"]) else None})
    pd.DataFrame(rows).to_csv(OUT / "live_forecasts.csv", index=False)
    size = sum(p.stat().st_size for p in out.glob("*.json"))
    print(f"  dashboard bundle: {len(list(out.glob('*.json')))} files, {size / 1e6:.1f} MB -> {out}", flush=True)
