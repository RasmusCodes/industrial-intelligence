"""Mapping database integrity and connector parsers (no network)."""
import io
import json

import pandas as pd
import pytest

from conftest import requires
from indintel import indicators_config, mapping, universe
from indintel.segments import classify, pretty
from indintel.sources import alfred, census_c30, eia860m, queues, sec, sec_xbrl


# ----------------------------------------------------------------------------- mapping

def test_registry_and_mapping_validate():
    reg = indicators_config.registry()
    assert len(reg) >= 40
    comps = mapping.companies()
    ids = {c["ticker"] for c in comps}
    assert set(universe.target_tickers()) <= ids
    for c in comps:
        demand = [d for d in c["drivers"] if d["role"] == "demand"]
        assert len(demand) >= 3, c["ticker"]
        for d in c["drivers"]:
            assert d["indicator"] in indicators_config.by_id()
            assert d.get("why")


def test_every_target_has_a_profile_with_evidence():
    profs = mapping.load_profiles()
    for tk in universe.target_tickers():
        p = profs[tk]
        for field in ("geography", "customers", "backlog", "pricing", "margin_drivers"):
            assert len(p[field]["evidence"]) >= 2, (tk, field)
            for e in p[field]["evidence"]:
                assert 20 <= len(e["quote"]) <= 400


def test_profile_quotes_verify_against_filings():
    if not mapping.TEXT.exists() or not any(mapping.TEXT.glob("*.txt")):
        pytest.skip("filing texts not downloaded (python -m indintel fetch sec_text)")
    res = mapping.verify()
    bad = res[~res["ok"]]
    assert bad.empty, bad.to_string()


def test_quote_normalisation():
    assert mapping.norm("a’b \n  c d") == "a'b c d"


# ----------------------------------------------------------------------------- parsers

def test_alfred_observations_keep_only_changes():
    long = pd.DataFrame({"period": pd.to_datetime(["2024-01-01"] * 3 + ["2024-02-01"] * 2),
                         "vintage_date": pd.to_datetime(["2024-02-05", "2024-03-05", "2024-04-05", "2024-03-05", "2024-04-05"]),
                         "value": [10.0, 10.0, 11.0, 20.0, 20.0]})
    o = alfred.to_observations("s", long, "M")
    assert len(o) == 3
    jan = o[o["period"] == pd.Timestamp("2024-01-01")].sort_values("available_at")
    assert list(jan["value"]) == [10.0, 11.0]
    # available from the end of the vintage day, never earlier
    assert jan["available_at"].iloc[0] == pd.Timestamp("2024-02-06", tz="UTC")


def test_eia860m_sheet_parser_and_groups():
    rows = [["Inventory of Planned Generators as of January 2024"] + [None] * 7,
            ["Entity ID", "Plant ID", "Plant Name", "Plant State", "Nameplate Capacity (MW)", "Technology",
             "Energy Source Code", "Prime Mover Code", "Planned Operation Month", "Planned Operation Year", "Status"],
            [1, 10, "Gas One", "TX", 500, "Natural Gas Fired Combustion Turbine", "NG", "GT", 6, 2025, "(U) Under construction"],
            [2, 11, "Sun", "TX", 200, "Solar Photovoltaic", "SUN", "PV", 3, 2024, "(P) Planned"],
            [3, 12, "Batt", "CA", 100, "Batteries", "MWH", "BA", 1, 2027, "(P) Planned"]]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        pd.DataFrame(rows).to_excel(w, sheet_name="Planned", header=False, index=False)
        pd.DataFrame([["x"], ["Plant ID"]]).to_excel(w, sheet_name="Operating", header=False, index=False)
    pl = eia860m.read_sheet(buf.getvalue(), "planned")
    assert len(pl) == 3 and pl["mw"].sum() == 800
    s = pd.DataFrame(eia860m.summarize(pd.Timestamp("2024-01-01"), pd.DataFrame(), pl))
    g24 = s[(s["group"] == "gas_turbine") & (s["measure"] == "planned_24m")]["mw"].iloc[0]
    b24 = s[(s["group"] == "battery") & (s["measure"] == "planned_24m")]["mw"].iloc[0]
    assert g24 == 500 and b24 == 0          # the battery is 36 months out


def test_census_c30_parser():
    rows = [["Value of Private Construction Put in Place"], [None], [None],
            ["Date", "Total\n_x000D_Private Construction1", "Data center"],
            ["Sep-26p", 1000, 40], ["Aug-26r", 990, 39], ["Jul-26", 980, 38]]
    buf = io.BytesIO()
    pd.DataFrame(rows).to_excel(buf, header=False, index=False)
    long = census_c30.parse(buf.getvalue())
    dc = long[long["category"] == "Data center"].set_index("month")["value"]
    assert dc[pd.Timestamp("2026-09-01")] == 40 and len(dc) == 3
    assert set(long["category"]) == {"Total Private Construction", "Data center"}


@pytest.mark.parametrize("text,cls", [("Battery Storage", "storage"), ("ES", "storage"), ("Gas", "gas"), ("NG", "gas"),
                                      ("Combined Cycle", "gas"), ("Solar", "solar"), ("S", "solar"), ("OSW", "wind"),
                                      ("Wind Turbine", "wind"), ("Hybrid", "hybrid"), ("", "unknown"), ("L", "load")])
def test_queue_fuel_classes(text, cls):
    assert queues.fuel_class(text) == cls


def test_html_to_text_and_instance_name():
    html = b"<html><head><style>x{}</style></head><body><ix:header>hidden</ix:header><p>Backlog was $5.0&nbsp;billion.</p><div>Next</div></body></html>"
    t = sec.html_to_text(html)
    assert "hidden" not in t and "Backlog was $5.0 billion." in t and "Next" in t
    assert sec.decode("café ’".encode("cp1252")) == "café ’"
    items = [{"name": "cat-20240930.htm"}, {"name": "cat-20240930_htm.xml"}, {"name": "cat-20240930_lab.xml"},
             {"name": "FilingSummary.xml"}]
    assert sec.instance_name(items) == "cat-20240930_htm.xml"
    items = [{"name": "cat-20140930.xml"}, {"name": "cat-20140930_pre.xml"}, {"name": "R2.htm"}]
    assert sec.instance_name(items) == "cat-20140930.xml"


def test_xbrl_instance_parser_keeps_segment_revenue():
    xml = b"""<?xml version="1.0"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
  xmlns:us-gaap="http://fasb.org/us-gaap/2024" xmlns:cat="http://www.caterpillar.com/20240930" xmlns:iso4217="http://www.xbrl.org/2003/iso4217">
 <xbrli:context id="c1"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier><xbrli:segment>
   <xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis">cat:ResourceIndustriesMember</xbrldi:explicitMember>
 </xbrli:segment></xbrli:entity><xbrli:period><xbrli:startDate>2024-07-01</xbrli:startDate><xbrli:endDate>2024-09-30</xbrli:endDate></xbrli:period></xbrli:context>
 <xbrli:context id="c2"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier></xbrli:entity>
   <xbrli:period><xbrli:startDate>2024-07-01</xbrli:startDate><xbrli:endDate>2024-09-30</xbrli:endDate></xbrli:period></xbrli:context>
 <xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
 <us-gaap:Revenues contextRef="c1" unitRef="usd" decimals="-6">3000000000</us-gaap:Revenues>
 <us-gaap:Revenues contextRef="c2" unitRef="usd" decimals="-6">16000000000</us-gaap:Revenues>
 <us-gaap:Assets contextRef="c2" unitRef="usd" decimals="-6">1</us-gaap:Assets>
</xbrli:xbrl>"""
    df = sec_xbrl.parse_instance(xml)
    assert len(df) == 2
    seg = df[df["n_dims"] == 1].iloc[0]
    assert json.loads(seg["dims"]) == {"StatementBusinessSegmentsAxis": "ResourceIndustriesMember"}
    assert seg["val"] == 3e9


def test_segment_variant_ranking_and_names():
    assert classify(json.dumps({"StatementBusinessSegmentsAxis": "AmericasSegmentMember"})) == ("AmericasSegmentMember", 1)
    assert classify(json.dumps({"StatementBusinessSegmentsAxis": "PowerSegmentMember",
                                "ConsolidationItemsAxis": "OperatingSegmentsMember"}))[1] == 2
    assert classify(json.dumps({"StatementBusinessSegmentsAxis": "A", "ProductOrServiceAxis": "B"})) == (None, 0)
    assert pretty("ElectricalAmericasSegmentMember") == "Electrical Americas"


# ----------------------------------------------------------------------------- built data (skip on a fresh clone)

@requires("panel", "observations")
def test_panel_features_recompute_from_raw_observations():
    """Brute force: recompute sampled panel features from the observation table filtered to available_at < origin."""
    import math

    import numpy as np

    from indintel import store
    from indintel.indicators import window_months
    p = store.load("panel")
    o = store.load("observations")
    reg = indicators_config.by_id()
    rng = np.random.default_rng(7)
    sample = p.sample(150, random_state=1)
    checked = 0
    for r in sample.itertuples(index=False):
        for k in rng.choice([s["id"] for s in reg.values() if s["source"] in ("alfred", "eia860m")], 3, replace=False):
            x = getattr(r, f"x_{k}")
            cov = getattr(r, f"cov_{k}")
            if not np.isfinite(x) or cov < 0.99:
                continue
            ok = o[(o["series_id"] == k) & (o["available_at"] < r.origin)]
            ok = ok.sort_values("available_at").drop_duplicates("period", keep="last").set_index("period")["value"]
            ms = window_months(r.period_start, r.period_end)
            cur = [ok.get(m) for m in ms]
            prev = [ok.get(m - pd.DateOffset(months=12)) for m in ms]
            agg = reg[k]["agg"]
            if agg == "last":
                want = math.log(cur[-1] / prev[-1])
            else:
                want = math.log(sum(cur) / sum(prev))
            assert x == pytest.approx(want, rel=1e-9, abs=1e-12)
            checked += 1
    assert checked >= 10


@requires("panel")
def test_panel_targets_never_used_as_features():
    from indintel import store
    p = store.load("panel")
    feats = [c for c in p.columns if c.startswith(("x_", "xl_", "g_last", "rpo_", "dgm_last", "dom_last", "kappa", "orders_last"))]
    h0 = p[p["h"] == 0]
    for c in feats:
        a = h0[[c, "y_rev"]].dropna()
        if len(a) > 30:
            assert not np.allclose(a[c].to_numpy(), a["y_rev"].to_numpy()), c


import numpy as np  # noqa: E402  (used in the data test above)
