"""Indicator store and the as-of feature engine.

``build`` combines every connector's observation table (plus the SEC customer-capex panels) into one
point-in-time store. ``FeatureEngine.feature(indicator, window_start, window_end, tau)`` answers: what was
the year-over-year growth of this indicator over the given window, using only values public before tau?

* monthly series: the window's months whose value and year-ago value are both known at tau (the 'ragged
  edge' of late months is dropped from both years, so growth compares like with like). If no month of the
  window is known yet, the latest three known months are used and the staleness is recorded.
* sparse event series (registrations, tenders, queue requests): trailing-twelve-month sums.
* customer-capex panels (quarterly, by filing): only members that have reported both the quarter and the
  year-ago quarter count, so the panel total is not distorted by who has reported so far.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import store
from .indicators_config import by_id, registry
from .universe import panel_members

OBS_TABLES = ["obs_alfred", "obs_eia860m", "obs_eia930", "obs_eia861m", "obs_census_c30", "obs_comtrade",
              "obs_indeed", "obs_queues", "obs_tdlr", "obs_ted"]
TTM_SOURCES = {"tdlr", "ted", "queues"}


def panel_obs() -> pd.DataFrame:
    v = store.load("fundamentals_vintages")
    frames = []
    for spec in registry():
        if spec["source"] != "sec_panel":
            continue
        p = spec["params"]
        members = panel_members(p["panel"])
        s = v[v["ticker"].isin(members) & (v["item"] == p["item"]) & v["derivation"].isin(["direct", "ytd_diff"])].copy()
        s = s[(s["period_days"] >= 80) & (s["period_days"] <= 105)]
        s["series_id"] = spec["id"] + ":" + s["ticker"]
        s["period"] = (s["period_end"] - pd.Timedelta(days=45)).dt.to_period("Q").dt.start_time
        s["freq"] = "Q"
        s["available_at"] = s["avail_utc"]
        s["vintage_kind"] = "filing"
        s["source"] = "sec"
        frames.append(s[store.OBS_COLUMNS])
    return pd.concat(frames, ignore_index=True)


def build() -> pd.DataFrame:
    frames = []
    for t in OBS_TABLES:
        df = store.load(t, missing_ok=True)
        if df.empty:
            print(f"  (missing {t})", flush=True)
            continue
        frames.append(df)
    frames.append(panel_obs())
    obs = store.check_obs(pd.concat(frames, ignore_index=True))
    store.save("observations", obs)
    n = obs.groupby("series_id").size()
    print(f"  observations: {len(obs):,} rows, {obs['series_id'].nunique()} series "
          f"({n.index.str.contains(':').sum()} panel members)", flush=True)
    return obs


# ----------------------------------------------------------------------------- as-of engine

class _Series:
    """One series: for each period, the sorted availability times and values of its vintages."""

    def __init__(self, df: pd.DataFrame):
        df = df.sort_values(["period", "available_at"])
        self.periods: dict[int, tuple[np.ndarray, np.ndarray]] = {}
        for per, g in df.groupby("period", sort=True):
            self.periods[pd.Timestamp(per).value] = (store.ns(g["available_at"]), g["value"].to_numpy(dtype=float))
        self.keys = np.array(sorted(self.periods), dtype=np.int64)
        self.first_avail = np.array([self.periods[k][0][0] for k in self.keys], dtype=np.int64)

    def value(self, period_ns: int, tau_ns: int) -> float:
        a = self.periods.get(period_ns)
        if a is None:
            return math.nan
        i = int(np.searchsorted(a[0], tau_ns, side="left")) - 1
        return float(a[1][i]) if i >= 0 else math.nan

    def latest_known_period(self, tau_ns: int, before_ns: int | None = None) -> int | None:
        ok = self.first_avail < tau_ns
        if before_ns is not None:
            ok &= self.keys <= before_ns
        idx = np.nonzero(ok)[0]
        return int(self.keys[idx[-1]]) if len(idx) else None


def _month(ts: pd.Timestamp, k: int = 0) -> int:
    return (pd.Timestamp(ts) + pd.DateOffset(months=k)).value


def window_months(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    first = (pd.Timestamp(start) + pd.Timedelta(days=14)).to_period("M").to_timestamp()
    last = (pd.Timestamp(end) - pd.Timedelta(days=14)).to_period("M").to_timestamp()
    return list(pd.date_range(first, last, freq="MS"))


class FeatureEngine:
    def __init__(self, obs: pd.DataFrame | None = None):
        obs = store.load("observations") if obs is None else obs
        self.specs = by_id()
        self.series: dict[str, _Series] = {}
        self.members: dict[str, list[str]] = {}
        for sid, g in obs.groupby("series_id"):
            self.series[sid] = _Series(g)
            if ":" in sid:
                self.members.setdefault(sid.split(":")[0], []).append(sid)

    # ---------------------------------------------------------------- monthly
    def _monthly(self, sid: str, months: list[pd.Timestamp], tau_ns: int, agg: str, ttm: bool) -> dict:
        s = self.series.get(sid)
        if s is None or not months:
            return {"x": math.nan, "coverage": 0.0, "stale_m": math.nan}
        if ttm:
            end = months[-1]
            last = s.latest_known_period(tau_ns, end.value)
            if last is None:
                return {"x": math.nan, "coverage": 0.0, "stale_m": math.nan}
            last_ts = pd.Timestamp(last)
            cur = [s.value(_month(last_ts, -k), tau_ns) for k in range(12)]
            prev = [s.value(_month(last_ts, -12 - k), tau_ns) for k in range(12)]
            if any(math.isnan(v) for v in cur + prev) or sum(prev) <= 0 or sum(cur) <= 0:
                return {"x": math.nan, "coverage": 0.0, "stale_m": math.nan}
            stale = (end.year - last_ts.year) * 12 + end.month - last_ts.month
            return {"x": math.log(sum(cur)) - math.log(sum(prev)), "coverage": 1.0 if stale == 0 else 0.0,
                    "stale_m": float(stale)}
        pairs = []
        for m in months:
            c, p = s.value(m.value, tau_ns), s.value(_month(m, -12), tau_ns)
            if not (math.isnan(c) or math.isnan(p)):
                pairs.append((c, p))
        stale = 0
        if not pairs:
            last = s.latest_known_period(tau_ns, months[0].value)
            if last is None:
                return {"x": math.nan, "coverage": 0.0, "stale_m": math.nan}
            last_ts = pd.Timestamp(last)
            for k in range(3):
                m = pd.Timestamp(_month(last_ts, -k))
                c, p = s.value(m.value, tau_ns), s.value(_month(m, -12), tau_ns)
                if not (math.isnan(c) or math.isnan(p)):
                    pairs.append((c, p))
            stale = (months[-1].year - last_ts.year) * 12 + months[-1].month - last_ts.month
            if not pairs:
                return {"x": math.nan, "coverage": 0.0, "stale_m": math.nan}
        if agg == "last":
            c, p = pairs[-1] if stale == 0 else pairs[0]
        else:
            c, p = sum(a for a, _ in pairs), sum(b for _, b in pairs)
        if c <= 0 or p <= 0:
            return {"x": math.nan, "coverage": 0.0, "stale_m": math.nan}
        cov = len(pairs) / len(months) if stale == 0 else 0.0
        return {"x": math.log(c) - math.log(p), "coverage": cov, "stale_m": float(stale)}

    # ---------------------------------------------------------------- quarterly panels
    def _panel(self, iid: str, end: pd.Timestamp, tau_ns: int) -> dict:
        mids = self.members.get(iid, [])
        if not mids:
            return {"x": math.nan, "coverage": 0.0, "stale_m": math.nan}
        q0 = (pd.Timestamp(end) - pd.Timedelta(days=45)).to_period("Q").start_time
        for lag in range(0, 4):
            q = q0 - pd.DateOffset(months=3 * lag)
            qp = q - pd.DateOffset(months=12)
            cur, prev, eligible = 0.0, 0.0, 0
            for sid in mids:
                s = self.series[sid]
                p = s.value(qp.value, tau_ns)
                if math.isnan(p):
                    continue
                eligible += 1
                c = s.value(q.value, tau_ns)
                if not math.isnan(c):
                    cur += c
                    prev += p
            matched_share = (sum(1 for sid in mids if not math.isnan(self.series[sid].value(q.value, tau_ns))
                                 and not math.isnan(self.series[sid].value(qp.value, tau_ns))) / eligible) if eligible else 0
            if eligible and matched_share >= 0.5 and cur > 0 and prev > 0:
                return {"x": math.log(cur) - math.log(prev), "coverage": matched_share if lag == 0 else 0.0,
                        "stale_m": float(3 * lag)}
        return {"x": math.nan, "coverage": 0.0, "stale_m": math.nan}

    @staticmethod
    def is_ttm(spec: dict) -> bool:
        return spec["source"] in TTM_SOURCES or spec.get("transform") == "ttm"

    def feature(self, iid: str, start, end, tau) -> dict:
        spec = self.specs[iid]
        tau_ns = pd.Timestamp(tau).value
        if spec["source"] == "sec_panel":
            return self._panel(iid, end, tau_ns)
        return self._monthly(iid, window_months(start, end), tau_ns, spec["agg"], self.is_ttm(spec))

    # ---------------------------------------------------------------- views for charts and lag analysis
    def quarterly_history(self, iid: str, view: str = "latest") -> pd.DataFrame:
        """Calendar-quarter level and YoY growth (latest vintage, or first release) for charts and lag tables."""
        spec = self.specs[iid]
        far = pd.Timestamp("2100-01-01").value
        if spec["source"] == "sec_panel":
            rows = []
            qs = sorted({k for sid in self.members.get(iid, []) for k in self.series[sid].keys})
            for qv in qs:
                q = pd.Timestamp(qv)
                if view == "first":
                    tau = max((self.series[sid].periods[qv][0][0] for sid in self.members[iid]
                               if qv in self.series[sid].periods), default=far) + 1
                else:
                    tau = far
                lvl = sum(self.series[sid].value(qv, tau) for sid in self.members[iid]
                          if not math.isnan(self.series[sid].value(qv, tau)))
                r = self._panel(iid, q + pd.DateOffset(months=3) + pd.Timedelta(days=44), tau)
                rows.append({"quarter": q, "level": lvl, "yoy": r["x"] if r["stale_m"] == 0 else math.nan})
            return pd.DataFrame(rows)
        s = self.series.get(iid)
        if s is None:
            return pd.DataFrame(columns=["quarter", "level", "yoy"])
        months = pd.to_datetime(s.keys)
        qstarts = sorted({m.to_period("Q").start_time for m in months})
        rows = []
        for q in qstarts:
            ms = list(pd.date_range(q, periods=3, freq="MS"))
            if view == "first":
                avail = [s.periods[m.value][0][0] for m in ms if m.value in s.periods]
                tau = (max(avail) + 1) if len(avail) == 3 else far
            else:
                tau = far
            vals = [s.value(m.value, tau) for m in ms]
            lvl = (vals[-1] if spec["agg"] == "last" else (np.nansum(vals) if spec["agg"] == "sum" else np.nanmean(vals))) \
                if not all(math.isnan(v) for v in vals) else math.nan
            r = self._monthly(iid, ms, tau, spec["agg"], self.is_ttm(spec))
            rows.append({"quarter": q, "level": lvl, "yoy": r["x"] if r["coverage"] >= 0.99 else math.nan})
        return pd.DataFrame(rows)

    def monthly_history(self, iid: str) -> pd.DataFrame:
        """Latest-vintage monthly levels (for dashboard charts)."""
        far = pd.Timestamp("2100-01-01").value
        if self.specs[iid]["source"] == "sec_panel":
            return self.quarterly_history(iid)[["quarter", "level"]].rename(columns={"quarter": "period"})
        s = self.series.get(iid)
        if s is None:
            return pd.DataFrame(columns=["period", "level"])
        return pd.DataFrame({"period": pd.to_datetime(s.keys), "level": [s.value(int(k), far) for k in s.keys]})
