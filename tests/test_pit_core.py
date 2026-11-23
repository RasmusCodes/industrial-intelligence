"""Point-in-time core: observation store semantics, XBRL vintage replay, the as-of feature engine."""
import math

import numpy as np
import pandas as pd
import pytest

from indintel import fundamentals as fu
from indintel import store
from indintel.indicators import FeatureEngine, window_months


def obs(rows):
    df = pd.DataFrame(rows, columns=["series_id", "period", "value", "available_at"])
    df["freq"] = "M"
    df["vintage_kind"] = "vintage"
    df["source"] = "test"
    return store.check_obs(df)


def test_asof_first_latest():
    o = obs([("s", "2024-01-01", 10, "2024-02-15"), ("s", "2024-01-01", 11, "2024-03-15"),
             ("s", "2024-02-01", 20, "2024-03-15")])
    a = store.asof(o, "2024-03-01")
    assert len(a) == 1 and a["value"].iloc[0] == 10          # revision not yet public
    a = store.asof(o, "2024-03-15")                          # strictly before: still the first value
    assert a.set_index("period")["value"].to_dict() == {pd.Timestamp("2024-01-01"): 10}
    a = store.asof(o, "2024-03-16")
    assert a.set_index("period")["value"].to_dict() == {pd.Timestamp("2024-01-01"): 11, pd.Timestamp("2024-02-01"): 20}
    assert store.first_release(o).set_index("period")["value"].to_dict()[pd.Timestamp("2024-01-01")] == 10
    assert store.latest(o).set_index("period")["value"].to_dict()[pd.Timestamp("2024-01-01")] == 11


def test_check_obs_rejects_unknown_kind_and_missing_time():
    df = pd.DataFrame({"series_id": ["s"], "period": ["2024-01-01"], "freq": ["M"], "value": [1.0],
                       "available_at": ["2024-02-01"], "vintage_kind": ["guess"], "source": ["t"]})
    with pytest.raises(ValueError):
        store.check_obs(df)
    df["vintage_kind"] = "vintage"
    df["available_at"] = [None]
    with pytest.raises(ValueError):
        store.check_obs(df)


def test_ns_handles_units_and_timezones():
    s = pd.Series(pd.to_datetime(["2024-01-01 12:00"]).tz_localize("UTC")).astype("datetime64[us, UTC]")
    assert store.ns(s)[0] == pd.Timestamp("2024-01-01 12:00", tz="UTC").value


# ----------------------------------------------------------------------------- XBRL vintage replay

def _facts():
    # fiscal year = calendar year; 10-Qs report 3-month and YTD values, the 10-K reports the full year
    rows = [
        # Q1 10-Q
        ("RevenueFromContractWithCustomerExcludingAssessedTax", "2024-01-01", "2024-03-31", 100, "A1", "10-Q", "2024-04-30"),
        # Q2 10-Q: 3 months and 6 months
        ("RevenueFromContractWithCustomerExcludingAssessedTax", "2024-04-01", "2024-06-30", 110, "A2", "10-Q", "2024-07-30"),
        ("RevenueFromContractWithCustomerExcludingAssessedTax", "2024-01-01", "2024-06-30", 210, "A2", "10-Q", "2024-07-30"),
        # Q3 10-Q: YTD only (as cash-flow items are reported)
        ("RevenueFromContractWithCustomerExcludingAssessedTax", "2024-01-01", "2024-09-30", 330, "A3", "10-Q", "2024-10-30"),
        # 10-K: full year only -> Q4 = 460 - 330
        ("RevenueFromContractWithCustomerExcludingAssessedTax", "2024-01-01", "2024-12-31", 460, "A4", "10-K", "2025-02-15"),
        # 10-Q of next year restates Q1-2024 comparative (new vintage)
        ("RevenueFromContractWithCustomerExcludingAssessedTax", "2024-01-01", "2024-03-31", 98, "A5", "10-Q", "2025-04-30"),
    ]
    f = pd.DataFrame(rows, columns=["concept", "start", "end", "val", "accn", "form", "filed"])
    f["ticker"], f["unit"] = "TST", "USD"
    filings = pd.DataFrame({"ticker": "TST", "accn": f["accn"].unique()})
    filings["acceptance_utc"] = pd.to_datetime(f.drop_duplicates("accn")["filed"].to_numpy()).tz_localize("UTC") + pd.Timedelta(hours=21)
    return f, filings


def test_vintage_replay_derives_quarters_and_keeps_revisions():
    f, filings = _facts()
    v = fu.build_vintages(f, filings)
    rev = v[v["item"] == "revenue"]
    fr = fu.first_reported(rev).set_index("period_end")["value"]
    assert fr[pd.Timestamp("2024-03-31")] == 100
    assert fr[pd.Timestamp("2024-09-30")] == 120            # 330 - 210 (YTD difference)
    assert fr[pd.Timestamp("2024-12-31")] == 130            # 460 - 330 (Q4 = FY - 9M)
    lt = fu.latest(rev).set_index("period_end")["value"]
    assert lt[pd.Timestamp("2024-03-31")] == 98             # restated later, first-reported value kept separately
    q4 = rev[rev["period_end"] == pd.Timestamp("2024-12-31")].iloc[0]
    assert q4["derivation"] == "ytd_diff" and q4["accn"] == "A4"
    # what was known before the 10-K: no Q4 yet
    known = fu.asof(rev, pd.Timestamp("2025-02-01", tz="UTC"))
    assert pd.Timestamp("2024-12-31") not in set(known["period_end"])


def test_scale_error_is_dropped():
    rows = []
    for i, (e, val) in enumerate([("2023-03-31", 100), ("2023-06-30", 105), ("2023-09-30", 110e3), ("2023-12-31", 112),
                                  ("2024-03-31", 115)]):
        s = (pd.Timestamp(e) - pd.DateOffset(months=3) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        rows.append(("Revenues", s, e, val, f"A{i}", "10-Q", (pd.Timestamp(e) + pd.Timedelta(days=30)).strftime("%Y-%m-%d")))
    f = pd.DataFrame(rows, columns=["concept", "start", "end", "val", "accn", "form", "filed"])
    f["ticker"], f["unit"] = "TST", "USD"
    filings = pd.DataFrame({"ticker": "TST", "accn": f["accn"], "acceptance_utc": pd.to_datetime(f["filed"]).dt.tz_localize("UTC")})
    v = fu.build_vintages(f, filings)
    assert pd.Timestamp("2023-09-30") not in set(v[v["item"] == "revenue"]["period_end"])


# ----------------------------------------------------------------------------- feature engine

def _engine():
    rows = []
    # monthly series 'm': value 100 in 2023, 110 in 2024; each month published 30 days after month end,
    # March 2024 revised to 130 on 2024-06-15
    for y, v in ((2023, 100.0), (2024, 110.0)):
        for m in range(1, 13):
            per = pd.Timestamp(y, m, 1)
            rows.append(("m", per, v, per + pd.offsets.MonthEnd(0) + pd.Timedelta(days=30)))
    rows.append(("m", pd.Timestamp(2024, 3, 1), 130.0, pd.Timestamp("2024-06-15")))
    df = pd.DataFrame(rows, columns=["series_id", "period", "value", "available_at"])
    df["freq"], df["vintage_kind"], df["source"] = "M", "vintage", "test"
    # quarterly panel 'pnl' with two members, member B reports late
    prow = []
    for q, a, b in ((pd.Timestamp("2023-01-01"), 10.0, 20.0), (pd.Timestamp("2024-01-01"), 12.0, 30.0)):
        prow.append(("pnl:A", q, a, q + pd.Timedelta(days=120)))
        prow.append(("pnl:B", q, b, q + pd.Timedelta(days=150)))
    pdf = pd.DataFrame(prow, columns=["series_id", "period", "value", "available_at"])
    pdf["freq"], pdf["vintage_kind"], pdf["source"] = "Q", "filing", "test"
    o = store.check_obs(pd.concat([df, pdf], ignore_index=True))
    fe = FeatureEngine(o)
    fe.specs = {"m": {"source": "alfred", "agg": "sum"}, "pnl": {"source": "sec_panel", "agg": "sum"}}
    return fe


def test_feature_uses_only_public_values_and_matches_months():
    fe = _engine()
    # Q1-2024 window; at 2024-04-15 Jan and Feb are public (Mar only on 2024-04-30)
    f = fe.feature("m", "2024-01-01", "2024-03-31", pd.Timestamp("2024-04-15", tz="UTC"))
    assert f["coverage"] == pytest.approx(2 / 3)
    assert f["x"] == pytest.approx(math.log(110 / 100))
    assert f["stale_m"] == 0
    # after the revision of March to 130: (110 + 110 + 130) / 300
    f = fe.feature("m", "2024-01-01", "2024-03-31", pd.Timestamp("2024-07-01", tz="UTC"))
    assert f["coverage"] == 1
    assert f["x"] == pytest.approx(math.log(350 / 300))
    # before the revision the original March value is used
    f = fe.feature("m", "2024-01-01", "2024-03-31", pd.Timestamp("2024-06-14", tz="UTC"))
    assert f["x"] == pytest.approx(math.log(330 / 300))


def test_feature_falls_back_to_latest_months_with_staleness():
    fe = _engine()
    # Q3-2024 window at 2024-07-15: nothing in the window yet; latest known month is May 2024 (public 2024-06-30)
    f = fe.feature("m", "2024-07-01", "2024-09-30", pd.Timestamp("2024-07-15", tz="UTC"))
    assert f["coverage"] == 0
    assert f["stale_m"] == 4                                   # September (window end) minus May
    # March-May 2024 against March-May 2023, March already revised to 130 by mid-June
    assert f["x"] == pytest.approx(math.log(350 / 300))


def test_panel_feature_counts_only_members_with_both_years():
    fe = _engine()
    # window ending 2024-03-31 -> calendar Q1-2024; at day 130 only member A has reported Q1-2024
    tau = pd.Timestamp("2024-01-01", tz="UTC") + pd.Timedelta(days=130)
    f = fe.feature("pnl", "2024-01-01", "2024-03-31", tau)
    # A alone is 1 of 2 eligible members -> 50% matched, allowed: growth of A only
    assert f["x"] == pytest.approx(math.log(12 / 10))
    tau = pd.Timestamp("2024-01-01", tz="UTC") + pd.Timedelta(days=160)
    f = fe.feature("pnl", "2024-01-01", "2024-03-31", tau)
    assert f["x"] == pytest.approx(math.log(42 / 30))


def test_window_months():
    assert [m.month for m in window_months("2024-01-01", "2024-03-31")] == [1, 2, 3]
    # 52/53-week fiscal quarter ending 2024-07-28
    assert [m.month for m in window_months("2024-04-29", "2024-07-28")] == [5, 6, 7]
