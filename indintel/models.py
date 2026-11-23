"""Forecasting models. All fit on a training frame and predict a test frame of panel rows.

Revenue-growth target y = log revenue growth versus the year-ago quarter (first reported).

Baselines
  RW          y_hat = last reported growth (as of the origin)
  AR          pooled OLS: y = a + b * last growth
Operational-indicator models (the company's mapped demand indicators from config/companies.yaml)
  Composite   pooled OLS: y = a + b * last growth + c * z, z = average of the company's standardised
              indicator growth rates
  BayesComp   the same with a company-specific slope c_i drawn from N(c, tau^2): empirical-Bayes partial
              pooling; also gives predictive intervals
  MappedRidge ridge regression on each mapped indicator separately (unmapped ones are held at zero)
  GBM         gradient-boosted trees on the same inputs plus backlog growth and sector (non-linear benchmark)
Backlog models (companies that report remaining performance obligations, RPO)
  BacklogAR   y = a + b * last growth + c * RPO growth (year over year, last reported quarter)
  Conversion  revenue = kappa_i * RPO at the last quarter end, with log kappa_i partially pooled across
              companies (Bayesian normal-normal); converted to growth against the year-ago quarter
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge

from .mapping import drivers

Z80 = 1.2815515655446004


def mapped(role: str = "demand") -> dict[str, list[str]]:
    d = drivers()
    d = d[d["role"] == role]
    return {tk: sorted(set(g["indicator"])) for tk, g in d.groupby("ticker")}


def _ols(X: np.ndarray, y: np.ndarray, ridge: float = 1e-6) -> np.ndarray:
    XtX = X.T @ X + ridge * np.eye(X.shape[1])
    return np.linalg.solve(XtX, X.T @ y)


class Standardiser:
    """Mean / sd of each indicator's growth over the training rows (point-in-time by construction)."""

    def __init__(self, train: pd.DataFrame, iids: list[str], prefix: str = "x_"):
        self.stats = {}
        for k in iids:
            col = prefix + k
            if col in train:
                v = train[col].to_numpy(float)
                v = v[np.isfinite(v)]
                if len(v) >= 8 and np.std(v) > 1e-9:
                    self.stats[k] = (float(np.mean(v)), float(np.std(v)))
        self.prefix = prefix

    def z(self, df: pd.DataFrame, k: str) -> np.ndarray:
        col = self.prefix + k
        if k not in self.stats or col not in df:
            return np.full(len(df), np.nan)
        mu, sd = self.stats[k]
        return (df[col].to_numpy(float) - mu) / sd


def composite(df: pd.DataFrame, std: Standardiser, inds: dict[str, list[str]], sign: dict | None = None) -> np.ndarray:
    """Average standardised growth of each row's mapped indicators (NaN if none available)."""
    out = np.full(len(df), np.nan)
    tick = df["ticker"].to_numpy()
    zcache = {}
    for tk in np.unique(tick):
        ks = [k for k in inds.get(tk, []) if k in std.stats]
        if not ks:
            continue
        idx = np.nonzero(tick == tk)[0]
        sub = df.iloc[idx]
        mats = []
        for k in ks:
            if (tk, k) not in zcache:
                zcache[(tk, k)] = std.z(sub, k)
            mats.append(zcache[(tk, k)])
        m = np.vstack(mats)
        with np.errstate(all="ignore"):
            cnt = np.isfinite(m).sum(axis=0)
            val = np.nansum(np.where(np.isfinite(m), m, 0.0), axis=0) / np.where(cnt > 0, cnt, 1)
        val[cnt == 0] = np.nan
        out[idx] = val
    return out


class Model:
    name = "model"
    target = "y_rev"
    last = "g_last"

    def fit(self, train: pd.DataFrame) -> "Model":
        return self

    def predict(self, test: pd.DataFrame) -> pd.DataFrame:
        raise NotImplementedError


def _frame(yhat, lo=None, hi=None, extra=None) -> pd.DataFrame:
    d = {"yhat": yhat}
    if lo is not None:
        d["lo"], d["hi"] = lo, hi
    if extra:
        d.update(extra)
    return pd.DataFrame(d)


class RW(Model):
    name = "RW"

    def __init__(self, target="y_rev", last="g_last"):
        self.target, self.last = target, last

    def predict(self, test):
        return _frame(test[self.last].to_numpy(float))


class AR(Model):
    name = "AR"

    def __init__(self, target="y_rev", last="g_last"):
        self.target, self.last = target, last

    def fit(self, train):
        t = train[[self.target, self.last]].dropna()
        X = np.column_stack([np.ones(len(t)), t[self.last]])
        self.beta = _ols(X, t[self.target].to_numpy(float))
        self.sigma = float(np.std(t[self.target].to_numpy(float) - X @ self.beta))
        return self

    def predict(self, test):
        g = test[self.last].to_numpy(float)
        yh = self.beta[0] + self.beta[1] * g
        return _frame(yh, yh - Z80 * self.sigma, yh + Z80 * self.sigma)


class Composite(Model):
    name = "Composite"

    def __init__(self, prefix="x_", name=None, target="y_rev", last="g_last"):
        self.prefix, self.target, self.last = prefix, target, last
        self.name = name or self.name
        self.inds = mapped("demand")

    def _design(self, df):
        z = composite(df, self.std, self.inds)
        return z

    def fit(self, train):
        all_k = sorted({k for v in self.inds.values() for k in v})
        self.std = Standardiser(train, all_k, self.prefix)
        z = self._design(train)
        t = pd.DataFrame({"y": train[self.target].to_numpy(float), "g": train[self.last].to_numpy(float), "z": z}).dropna()
        X = np.column_stack([np.ones(len(t)), t["g"], t["z"]])
        self.beta = _ols(X, t["y"].to_numpy())
        self.sigma = float(np.std(t["y"].to_numpy() - X @ self.beta))
        self.ar = AR(self.target, self.last).fit(train)
        self.n = len(t)
        return self

    def predict(self, test):
        z = self._design(test)
        g = test[self.last].to_numpy(float)
        yh = self.beta[0] + self.beta[1] * g + self.beta[2] * np.nan_to_num(z, nan=0.0)
        fallback = ~np.isfinite(z)
        ar = self.ar.predict(test)["yhat"].to_numpy()
        yh = np.where(fallback, ar, yh)
        return _frame(yh, yh - Z80 * self.sigma, yh + Z80 * self.sigma, {"z": z})


class BayesComp(Composite):
    """Partially pooled company slopes on the composite (empirical Bayes normal-normal)."""
    name = "BayesComp"

    def fit(self, train):
        super().fit(train)
        z = self._design(train)
        a, b, c = self.beta
        t = pd.DataFrame({"tk": train["ticker"].to_numpy(), "y": train[self.target].to_numpy(float),
                          "g": train[self.last].to_numpy(float), "z": z}).dropna()
        t["r"] = t["y"] - a - b * t["g"]
        est = {}
        for tk, g in t.groupby("tk"):
            zz = g["z"].to_numpy()
            if len(g) < 6 or np.sum(zz ** 2) < 1e-6:
                continue
            ci = float(np.sum(zz * g["r"]) / np.sum(zz ** 2))
            res = g["r"].to_numpy() - ci * zz
            s2 = float(np.sum(res ** 2) / max(1, len(g) - 1))
            est[tk] = (ci, s2 / np.sum(zz ** 2), s2)
        if len(est) >= 3:
            cs = np.array([v[0] for v in est.values()])
            vs = np.array([v[1] for v in est.values()])
            tau2 = max(0.0, float(np.var(cs, ddof=1) - np.mean(vs)))
        else:
            tau2 = 0.0
        self.tau2 = tau2
        self.slopes, self.slope_var, self.resvar = {}, {}, {}
        pooled_s2 = float(np.var(t["r"] - c * t["z"]))
        for tk, (ci, vi, s2) in est.items():
            if tau2 <= 0:
                post, pv = c, 0.0
            else:
                w = (1 / vi) / (1 / vi + 1 / tau2)
                post, pv = w * ci + (1 - w) * c, 1 / (1 / vi + 1 / tau2)
            self.slopes[tk], self.slope_var[tk] = post, pv
            n = len(t[t["tk"] == tk])
            self.resvar[tk] = (n * s2 + 10 * pooled_s2) / (n + 10)      # shrink residual variance too
        self.c_pool, self.pooled_s2 = c, pooled_s2
        return self

    def predict(self, test):
        z = self._design(test)
        g = test[self.last].to_numpy(float)
        tk = test["ticker"].to_numpy()
        a, b, _ = self.beta
        c_i = np.array([self.slopes.get(t, self.c_pool) for t in tk])
        v_i = np.array([self.slope_var.get(t, self.tau2) for t in tk])
        s2 = np.array([self.resvar.get(t, self.pooled_s2) for t in tk])
        zz = np.nan_to_num(z, nan=0.0)
        yh = a + b * g + c_i * zz
        sd = np.sqrt(s2 + zz ** 2 * v_i)
        ar = self.ar.predict(test)
        fb = ~np.isfinite(z)
        yh = np.where(fb, ar["yhat"].to_numpy(), yh)
        lo = np.where(fb, ar["lo"].to_numpy(), yh - Z80 * sd)
        hi = np.where(fb, ar["hi"].to_numpy(), yh + Z80 * sd)
        return _frame(yh, lo, hi, {"z": z, "slope": c_i})


class MappedRidge(Model):
    name = "MappedRidge"

    def __init__(self, alpha=5.0, prefix="x_", target="y_rev", last="g_last"):
        self.alpha, self.prefix, self.target, self.last = alpha, prefix, target, last
        self.inds = mapped("demand")
        self.keys = sorted({k for v in self.inds.values() for k in v})

    def _X(self, df):
        tick = df["ticker"].to_numpy()
        cols = [df[self.last].to_numpy(float)]
        for k in self.keys:
            zk = self.std.z(df, k) if k in self.std.stats else np.full(len(df), np.nan)
            mask = np.array([k in self.inds.get(t, []) for t in tick])
            cols.append(np.where(mask & np.isfinite(zk), zk, 0.0))
        return np.column_stack(cols)

    def fit(self, train):
        self.std = Standardiser(train, self.keys, self.prefix)
        t = train[train[self.target].notna() & train[self.last].notna()]
        self.m = Ridge(alpha=self.alpha).fit(self._X(t), t[self.target].to_numpy(float))
        return self

    def predict(self, test):
        X = self._X(test)
        yh = self.m.predict(np.nan_to_num(X, nan=0.0))
        yh = np.where(np.isfinite(test[self.last].to_numpy(float)), yh, np.nan)
        return _frame(yh)


class GBM(Model):
    name = "GBM"

    def __init__(self, prefix="x_", target="y_rev", last="g_last"):
        self.prefix, self.target, self.last = prefix, target, last
        self.inds = mapped("demand")
        self.keys = sorted({k for v in self.inds.values() for k in v})

    def _X(self, df):
        tick = df["ticker"].to_numpy()
        cols = [df[self.last].to_numpy(float), df["g_last2"].to_numpy(float) if "g_last2" in df else np.full(len(df), np.nan),
                df["rpo_g_last"].to_numpy(float) if "rpo_g_last" in df else np.full(len(df), np.nan)]
        for k in self.keys:
            col = self.prefix + k
            v = df[col].to_numpy(float) if col in df else np.full(len(df), np.nan)
            mask = np.array([k in self.inds.get(t, []) for t in tick])
            cols.append(np.where(mask, v, np.nan))
        return np.column_stack(cols)

    def fit(self, train):
        t = train[train[self.target].notna()]
        X = self._X(t)
        # columns with no observation in this training window (series that start later) cannot be binned
        self.keep = ~np.all(np.isnan(X), axis=0)
        self.m = HistGradientBoostingRegressor(max_depth=3, learning_rate=0.05, max_iter=250, min_samples_leaf=20,
                                               l2_regularization=1.0, random_state=0)
        self.m.fit(X[:, self.keep], t[self.target].to_numpy(float))
        return self

    def predict(self, test):
        return _frame(self.m.predict(self._X(test)[:, self.keep]))


class BacklogAR(Model):
    name = "BacklogAR"

    def __init__(self, target="y_rev", last="g_last"):
        self.target, self.last = target, last

    def fit(self, train):
        t = train[[self.target, self.last, "rpo_g_last"]].dropna()
        self.ok = len(t) >= 20
        if self.ok:
            X = np.column_stack([np.ones(len(t)), t[self.last], t["rpo_g_last"]])
            self.beta = _ols(X, t[self.target].to_numpy(float))
        self.ar = AR(self.target, self.last).fit(train)
        return self

    def predict(self, test):
        ar = self.ar.predict(test)["yhat"].to_numpy()
        if not self.ok:
            return _frame(ar, extra={"used_rpo": np.zeros(len(test), bool)})
        r = test["rpo_g_last"].to_numpy(float)
        yh = self.beta[0] + self.beta[1] * test[self.last].to_numpy(float) + self.beta[2] * np.nan_to_num(r)
        use = np.isfinite(r)
        return _frame(np.where(use, yh, ar), extra={"used_rpo": use})


class Conversion(Model):
    """Revenue = kappa_i x RPO(last quarter end); log kappa_i partially pooled (normal-normal)."""
    name = "Conversion"

    def __init__(self, target="y_rev", last="g_last"):
        self.target, self.last = target, last

    def fit(self, train):
        # realised conversion of each training row: revenue of the target quarter / RPO at the last quarter end
        t = train[(train["rpo_last"] > 0) & (train["rev_q"] > 0)][["ticker", "rev_q", "rpo_last"]].copy()
        t["lk"] = np.log(t["rev_q"] / t["rpo_last"])
        self.mu, self.var, self.post = 0.0, 1.0, {}
        if len(t) >= 10:
            g = t.groupby("ticker")["lk"].agg(["mean", "var", "count"])
            within = float(np.nanmean(g["var"])) if g["var"].notna().any() else 0.05
            g["var"] = g["var"].where(g["var"] > 0, within).fillna(within)
            mu = float(g["mean"].mean())
            tau2 = max(1e-4, float(g["mean"].var(ddof=1)) - float(np.mean(g["var"] / g["count"]))) if len(g) > 1 else 0.05
            for tk, r in g.iterrows():
                se2 = r["var"] / r["count"]
                w = (1 / se2) / (1 / se2 + 1 / tau2)
                self.post[tk] = (w * r["mean"] + (1 - w) * mu, 1 / (1 / se2 + 1 / tau2) + r["var"])
            self.mu, self.var = mu, tau2 + within
        self.ar = AR(self.target, self.last).fit(train)
        return self

    def predict(self, test):
        ar = self.ar.predict(test)["yhat"].to_numpy()
        tk = test["ticker"].to_numpy()
        rpo = test["rpo_last"].to_numpy(float)
        rev4 = test["rev4_asof"].to_numpy(float)
        mus = np.array([self.post.get(t, (math.nan, math.nan))[0] for t in tk])
        ok = np.isfinite(mus) & (rpo > 0) & (rev4 > 0)
        with np.errstate(all="ignore"):
            yh = mus + np.log(np.where(rpo > 0, rpo, np.nan)) - np.log(np.where(rev4 > 0, rev4, np.nan))
        return _frame(np.where(ok, yh, ar), extra={"used_rpo": ok, "log_kappa": mus})


class ConvLast(Model):
    """Post-hoc backlog rule (added after the first holdout run, see docs): next quarter's revenue converts the
    last RPO at the most recent observed conversion ratio: revenue = kappa_last * RPO(last)."""
    name = "ConvLast"

    def __init__(self, target="y_rev", last="g_last"):
        self.target, self.last = target, last

    def fit(self, train):
        self.ar = AR(self.target, self.last).fit(train)
        return self

    def predict(self, test):
        k, rpo, rev4 = (test[c].to_numpy(float) for c in ("kappa_last", "rpo_last", "rev4_asof"))
        ok = (k > 0) & (rpo > 0) & (rev4 > 0)
        with np.errstate(all="ignore"):
            yh = np.log(np.where(ok, k * rpo, np.nan)) - np.log(np.where(ok, rev4, np.nan))
        return _frame(np.where(ok, yh, self.ar.predict(test)["yhat"].to_numpy()), extra={"used_rpo": ok})


class ConvRPOGrowth(Model):
    """Post-hoc backlog rule: revenue grows as fast as RPO did over the last reported year (no parameters).
    Equivalent to converting RPO at the same-quarter-last-year conversion ratio."""
    name = "ConvRPOGrowth"

    def __init__(self, target="y_rev", last="g_last"):
        self.target, self.last = target, last

    def fit(self, train):
        self.ar = AR(self.target, self.last).fit(train)
        return self

    def predict(self, test):
        r = test["rpo_g_last"].to_numpy(float)
        ok = np.isfinite(r)
        return _frame(np.where(ok, r, self.ar.predict(test)["yhat"].to_numpy()), extra={"used_rpo": ok})


# ----------------------------------------------------------------------------- margin models

class MarginPriceCost(Model):
    """y = a + b * last margin change + c * (pricing composite) + d * (cost composite) + e * last revenue growth."""
    name = "PriceCost"

    def __init__(self, target="y_dgm", last="dgm_last", prefix="x_"):
        self.target, self.last, self.prefix = target, last, prefix
        self.price, self.cost = mapped("pricing"), mapped("cost")

    def _design(self, df):
        p = composite(df, self.sp, self.price)
        c = composite(df, self.sc, self.cost)
        return np.nan_to_num(p), np.nan_to_num(c)

    def fit(self, train):
        self.sp = Standardiser(train, sorted({k for v in self.price.values() for k in v}), self.prefix)
        self.sc = Standardiser(train, sorted({k for v in self.cost.values() for k in v}), self.prefix)
        p, c = self._design(train)
        t = pd.DataFrame({"y": train[self.target].to_numpy(float), "l": train[self.last].to_numpy(float),
                          "p": p, "c": c, "g": train["g_last"].to_numpy(float)}).dropna()
        X = np.column_stack([np.ones(len(t)), t["l"], t["p"], t["c"], t["g"]])
        self.beta = _ols(X, t["y"].to_numpy(), ridge=1.0)
        return self

    def predict(self, test):
        p, c = self._design(test)
        X = np.column_stack([np.ones(len(test)), test[self.last].to_numpy(float), p, c, test["g_last"].to_numpy(float)])
        return _frame(X @ self.beta)


def revenue_models() -> list[Model]:
    return [RW(), AR(), Composite(), BayesComp(), MappedRidge(), GBM(), BacklogAR(), Conversion()]


def margin_models(target: str, last: str) -> list[Model]:
    return [RW(target, last), AR(target, last), MarginPriceCost(target, last)]
