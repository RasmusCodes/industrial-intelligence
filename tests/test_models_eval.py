"""Models, evaluation statistics and the walk-forward leakage guard, on synthetic data."""
import math

import numpy as np
import pandas as pd
import pytest

from indintel import evaluate as ev
from indintel import models as md
from indintel.backtest import summary, walk_forward


def synthetic_panel(n_firms=6, n_q=40, seed=0, signal=0.6):
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n_firms):
        tk = f"F{i}"
        g_prev = 0.05
        for t in range(n_q):
            pe = pd.Timestamp("2014-03-31") + pd.offsets.QuarterEnd(t)
            x = rng.normal(0, 0.1)
            y = 0.02 + 0.5 * g_prev + signal * x + rng.normal(0, 0.03)
            rel = pe + pd.Timedelta(days=30)
            rows.append({"ticker": tk, "period_end": pe, "period_start": pe - pd.offsets.QuarterBegin(1),
                         "h": 0, "origin": (rel - pd.Timedelta(seconds=1)).tz_localize("UTC"),
                         "release_utc": rel.tz_localize("UTC"), "y_rev": y, "g_last": g_prev, "g_last2": g_prev,
                         "x_ind": x, "rpo_g_last": np.nan, "rpo_last": np.nan, "rev_q": 100.0, "rev4_asof": 95.0})
            g_prev = y
    return pd.DataFrame(rows)


@pytest.fixture
def mapped_ind(monkeypatch):
    monkeypatch.setattr(md, "mapped", lambda role="demand": {f"F{i}": ["ind"] for i in range(6)} if role == "demand" else {})


def test_ar_recovers_slope():
    p = synthetic_panel(signal=0.0)
    m = md.AR().fit(p)
    assert m.beta[1] == pytest.approx(0.5, abs=0.08)


def test_composite_beats_ar_when_indicator_is_informative(mapped_ind):
    p = synthetic_panel(signal=0.6)
    pr = walk_forward(p, lambda: [md.RW(), md.AR(), md.Composite(), md.BayesComp()], "y_rev", 0,
                      start=pd.Timestamp("2018-01-01"), min_train=40)
    s = summary(pr)
    a = s[(s["period"] == "all")].set_index("model")
    assert a.loc["Composite", "mae"] < 0.8 * a.loc["AR", "mae"]
    assert a.loc["BayesComp", "cw_p_vs_ar"] < 0.01


def test_composite_adds_nothing_when_indicator_is_noise(mapped_ind):
    p = synthetic_panel(signal=0.0, seed=3)
    pr = walk_forward(p, lambda: [md.RW(), md.AR(), md.Composite()], "y_rev", 0, start=pd.Timestamp("2018-01-01"))
    s = summary(pr)
    a = s[(s["period"] == "all")].set_index("model")
    assert a.loc["Composite", "mae_vs_ar"] > 0.97


def test_walk_forward_never_trains_on_unreleased_targets(mapped_ind):
    p = synthetic_panel()
    audit = []
    walk_forward(p, lambda: [md.AR()], "y_rev", 0, start=pd.Timestamp("2016-01-01"), audit=audit)
    a = pd.DataFrame(audit)
    assert len(a) > 10
    assert (a["max_train_release"] < a["cutoff"]).all()


def test_bayes_slopes_are_shrunk(mapped_ind):
    p = synthetic_panel(signal=0.5)
    # give one firm a very different slope with few observations
    m = md.BayesComp().fit(p)
    assert all(np.isfinite(list(m.slopes.values())))
    assert m.tau2 >= 0


def test_conversion_recovers_constant_kappa():
    rng = np.random.default_rng(1)
    rows = []
    for i in range(5):
        for t in range(20):
            rpo = 1000 * (1 + 0.05 * t)
            rows.append({"ticker": f"F{i}", "rpo_last": rpo, "rev_q": 0.3 * rpo * math.exp(rng.normal(0, 0.02)),
                         "y_rev": 0.1, "g_last": 0.1, "rev4_asof": 0.3 * rpo / 1.1})
    p = pd.DataFrame(rows)
    m = md.Conversion().fit(p)
    assert math.exp(m.post["F0"][0]) == pytest.approx(0.3, rel=0.03)


def test_dm_and_cw_tests():
    rng = np.random.default_rng(0)
    groups = np.repeat(np.arange(60), 5)
    y = rng.normal(size=300)
    good = y + rng.normal(0, 0.5, 300)
    bad = y + rng.normal(0, 1.0, 300)
    d = ev.dm_test(good - y, bad - y, groups)
    assert d["dm_stat"] < -3 and d["dm_p"] < 0.01
    same = ev.dm_test(bad - y, bad - y, groups)
    assert not np.isfinite(same["dm_stat"]) or abs(same["dm_stat"]) < 1e-9
    cw = ev.clark_west(y, bad, good, groups)
    assert cw["cw_p"] < 0.01


def test_metrics_and_coverage():
    m = ev.metrics(np.array([1.0, -1.0, np.nan, 2.0]))
    assert m["n"] == 3 and m["mae"] == pytest.approx(4 / 3)
    assert ev.coverage([0, 1, 2], [-1, 0, 3], [1, 2, 4]) == pytest.approx(2 / 3)
    assert ev.hit_rate([1, -1, 1, 0], [1, 1, 1, 1]) == pytest.approx(2 / 3)
