"""Canonical quarterly segment revenue (and segment profit) from dimensional XBRL vintages.

Companies tag segment figures in several ways: segment member alone, or together with a consolidation-item
member (operating segments, operating segments excluding intersegment eliminations, reportable
sub-segments). For each company, segment and quarter the highest-ranked variant is used:

  1. segment member alone
  2. + ConsolidationItemsAxis = OperatingSegmentsMember / OperatingSegmentsExcludingIntersegmentEliminationMember
  3. + SubsegmentsConsolidationItemsAxis = ReportableSubsegmentsMember (Caterpillar before 2023)

and among concepts the standard revenue concepts come first. Member names map to display names through
``segment_names`` in config/companies.yaml (members renamed over time map to one name); unmapped members get
a name derived from the member. Corporate, elimination and total members are dropped.
"""
from __future__ import annotations

import json
import re

import pandas as pd

from . import store
from .fundamentals import first_reported, latest

REV = re.compile(r"(revenue|sales)", re.I)
NOT_REV = re.compile(r"(cost|intersegment|deferred|unbilled|remaining|receivable|percent|incometax|taxexpense|"
                     r"interest|expense|gain|loss|proceeds|reconcil|eliminat|allowance|backlog|returns|discount)", re.I)
PROFIT = re.compile(r"(operatingincome|segmentprofit|segmentoperatingprofit|segmentoperatingincome|"
                    r"operatingprofit|segmentebitda|adjustedebitda)", re.I)
DROP_MEMBER = re.compile(r"(corporate|eliminat|reconcil|total|consolidat|operatingsegments|allother|nonsegment|"
                         r"unallocated|intersegment|reportablesegment)", re.I)
PRIORITY = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
            "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueGoodsNet"]
ALLOWED_EXTRA = {
    ("ConsolidationItemsAxis", "OperatingSegmentsMember"): 2,
    ("ConsolidationItemsAxis", "OperatingSegmentsExcludingIntersegmentEliminationMember"): 2,
    ("SubsegmentsConsolidationItemsAxis", "ReportableSubsegmentsMember"): 3,
}


def pretty(member: str) -> str:
    m = re.sub(r"(Segment)?Member$", "", member)
    m = re.sub(r"Segment$", "", m)
    m = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", m)
    return m.replace(" And ", " and ").strip()


def classify(dims_json: str) -> tuple[str | None, int]:
    """(segment member, variant rank) or (None, 0) if the context is not a plain segment total."""
    d = json.loads(dims_json)
    seg = d.get("StatementBusinessSegmentsAxis")
    if seg is None:
        return None, 0
    other = [(k, v) for k, v in d.items() if k != "StatementBusinessSegmentsAxis"]
    if not other:
        return seg, 1
    if len(other) == 1 and other[0] in ALLOWED_EXTRA:
        return seg, ALLOWED_EXTRA[other[0]]
    return None, 0


def build(names: dict | None = None) -> pd.DataFrame:
    from .mapping import segment_name_map
    names = names if names is not None else segment_name_map()
    sv = store.load("segment_vintages")
    sv = sv[sv["derivation"].isin(["direct", "ytd_diff"])].copy()
    cls = sv["dims"].map(classify)
    sv["member"] = [c[0] for c in cls]
    sv["variant"] = [c[1] for c in cls]
    sv = sv[sv["member"].notna() & ~sv["member"].str.contains(DROP_MEMBER)]
    frames = []
    for metric, keep in (("revenue", lambda c: bool(REV.search(c)) and not NOT_REV.search(c)),
                         ("profit", lambda c: bool(PROFIT.search(c)))):
        sub = sv[sv["concept"].map(keep)].copy()
        if sub.empty:
            continue
        sub["crank"] = sub["concept"].map(lambda c: PRIORITY.index(c) if c in PRIORITY else 50)
        # best (variant, concept) per company, member and quarter
        best = (sub.sort_values(["variant", "crank", "concept"])
                .drop_duplicates(["ticker", "member", "period_end"])[["ticker", "member", "period_end", "variant", "concept"]])
        sub = sub.merge(best, on=["ticker", "member", "period_end", "variant", "concept"])
        sub["item"] = metric
        frames.append(sub)
    seg = pd.concat(frames, ignore_index=True)
    seg["segment"] = [names.get(tk, {}).get(m, pretty(m)) for tk, m in zip(seg["ticker"], seg["member"])]
    key = seg["ticker"] + "|" + seg["segment"]
    tmp = seg.assign(ticker=key)
    fr = first_reported(tmp)
    lt = latest(tmp)
    out = lt[["ticker", "item", "period_start", "period_end", "value", "avail_utc", "concept", "member", "variant"]].merge(
        fr[["ticker", "item", "period_end", "value", "avail_utc"]], on=["ticker", "item", "period_end"],
        suffixes=("_latest", "_first"))
    out[["ticker", "segment"]] = out["ticker"].str.split("|", n=1, expand=True)
    out = out.sort_values(["ticker", "segment", "item", "period_end"]).reset_index(drop=True)
    store.save("segments", out)
    print(f"  segments: {out[out['item'] == 'revenue'].groupby('ticker')['segment'].nunique().to_dict()}", flush=True)
    return out
