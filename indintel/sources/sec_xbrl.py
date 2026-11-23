"""Dimensional XBRL facts (segment, product-line and geographic revenue and profit) from filing instances.

companyfacts holds consolidated totals only. Segment data sit in each 10-Q/10-K instance document as facts
whose context carries dimension members (``us-gaap:StatementBusinessSegmentsAxis = cat:ResourceIndustriesMember``).
This module downloads the instance of every periodic filing of the target companies, keeps monetary facts
whose concept looks like revenue, sales, profit, orders or backlog, and stores them with the filing's
acceptance time. ``segment_vintages`` then derives discrete quarters exactly like the consolidated replay.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict

import pandas as pd
from lxml import etree

from .. import store
from ..fundamentals import apply_boundaries, duration_vintages, instant_vintages
from ..http import Fetcher, FetchError
from ..universe import raw as uraw
from .sec import fetch_doc, filing_index, instance_name

XBRLI = "http://www.xbrl.org/2003/instance"
XBRLDI = "http://xbrl.org/2006/xbrldi"
PERIODIC = ("10-Q", "10-K", "10-Q/A", "10-K/A", "10-KT", "20-F", "20-F/A")
KEEP_CONCEPT = re.compile(r"(revenue|sales|operatingincome|segmentprofit|segmentoperatingprofit|profit|ebitda|"
                          r"orders|backlog|remainingperformance|bookings|costofgoods|costofrevenue|grossprofit)", re.I)
KEEP_AXES = re.compile(r"(Segment|ProductOrService|Geographical|ConsolidationItems|BusinessSegments|"
                       r"ProductInformation|Customer|MajorCustomers|SubsegmentsAxis|RevenueRemaining)", re.I)


def _ns_kind(ns: str) -> str:
    if "fasb.org/us-gaap" in ns:
        return "us-gaap"
    if "fasb.org/srt" in ns:
        return "srt"
    if "xbrl.sec.gov/dei" in ns:
        return "dei"
    return "ext"


def _local(qname: str) -> str:
    return qname.split(":", 1)[-1]


def parse_instance(data: bytes) -> pd.DataFrame:
    root = etree.fromstring(data, parser=etree.XMLParser(huge_tree=True, recover=True))
    if root is None:
        return pd.DataFrame()
    ctx = {}
    for c in root.iter(f"{{{XBRLI}}}context"):
        cid = c.get("id")
        per = c.find(f"{{{XBRLI}}}period")
        start = end = None
        if per is not None:
            inst = per.find(f"{{{XBRLI}}}instant")
            if inst is not None:
                end = inst.text.strip()
            else:
                s, e = per.find(f"{{{XBRLI}}}startDate"), per.find(f"{{{XBRLI}}}endDate")
                start = s.text.strip() if s is not None else None
                end = e.text.strip() if e is not None else None
        dims = {}
        for m in c.iter(f"{{{XBRLDI}}}explicitMember"):
            dims[_local(m.get("dimension", ""))] = _local((m.text or "").strip())
        for m in c.iter(f"{{{XBRLDI}}}typedMember"):
            dims[_local(m.get("dimension", ""))] = "typed:" + "".join(m.itertext()).strip()[:60]
        ctx[cid] = (start, end, dims)
    units = {}
    for u in root.iter(f"{{{XBRLI}}}unit"):
        meas = [(_local(x.text or "")) for x in u.iter(f"{{{XBRLI}}}measure")]
        units[u.get("id")] = "/".join(meas)
    rows = []
    for el in root:
        if not isinstance(el.tag, str):
            continue
        cref = el.get("contextRef")
        if cref is None or cref not in ctx:
            continue
        ns, _, local = el.tag[1:].partition("}")
        if not KEEP_CONCEPT.search(local):
            continue
        unit = units.get(el.get("unitRef"), "")
        if unit != "USD":
            continue
        start, end, dims = ctx[cref]
        if dims and not all(KEEP_AXES.search(a) for a in dims):
            continue
        txt = (el.text or "").strip()
        if not txt or el.get("{http://www.w3.org/2001/XMLSchema-instance}nil") == "true":
            continue
        try:
            val = float(txt)
        except ValueError:
            continue
        rows.append((_ns_kind(ns), local, start, end, json.dumps(dims, sort_keys=True), len(dims), val,
                     el.get("decimals")))
    df = pd.DataFrame(rows, columns=["taxonomy", "concept", "start", "end", "dims", "n_dims", "val", "decimals"])
    return df.drop_duplicates(["concept", "start", "end", "dims"], keep="first")


def collect(f: Fetcher, tickers: list[str] | None = None, since: str = "2012-01-01", verbose: bool = True) -> pd.DataFrame:
    filings = store.load("sec_filings")
    u = uraw()
    allowed = {r["ticker"] for r in u["targets"]} | {r["ticker"] for r in u.get("annual_only", [])}
    tickers = tickers or sorted(allowed)
    fl = filings[filings["ticker"].isin(tickers) & filings["form"].isin(PERIODIC)
                 & (filings["filing_date"] >= since)].sort_values(["ticker", "acceptance_utc"])
    frames, problems = [], []
    for i, r in enumerate(fl.itertuples(index=False), 1):
        try:
            items = filing_index(f, r.cik, r.accn)
            name = instance_name(items)
            if not name:
                problems.append((r.ticker, r.accn, r.form, "no instance"))
                continue
            data = fetch_doc(f, r.cik, r.accn, name, kind="sec_xbrl")
            if data is None:
                problems.append((r.ticker, r.accn, r.form, "instance 404"))
                continue
            df = parse_instance(data)
        except (FetchError, etree.XMLSyntaxError) as e:
            problems.append((r.ticker, r.accn, r.form, str(e)[:120]))
            continue
        if df.empty:
            continue
        df["ticker"], df["accn"], df["form"] = r.ticker, r.accn, r.form
        df["avail_utc"] = r.acceptance_utc
        frames.append(df)
        if verbose and i % 25 == 0:
            print(f"  xbrl {i}/{len(fl)} {r.ticker} {r.form} {r.filing_date} (net {f.n_network}, cache {f.n_cache})",
                  flush=True)
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    store.save("sec_xbrl_facts", out)
    if problems:
        store.save("sec_xbrl_problems", pd.DataFrame(problems, columns=["ticker", "accn", "form", "problem"]))
    if verbose:
        print(f"xbrl facts: {len(out):,} rows from {len(frames)} filings; problems {len(problems)}", flush=True)
    return out


def segment_vintages(xf: pd.DataFrame | None = None) -> pd.DataFrame:
    """Quarterly vintages for every (ticker, concept, dims) series of dimensional duration facts."""
    xf = store.load("sec_xbrl_facts") if xf is None else xf
    xf = xf[xf["n_dims"] >= 1].copy()
    out = []
    for (tk, concept, dims), g in xf.groupby(["ticker", "concept", "dims"]):
        g = g.rename(columns={"val": "val"}).copy()
        g["filed"] = pd.to_datetime(g["avail_utc"]).dt.strftime("%Y-%m-%d")
        g = apply_boundaries(g)
        g["end"] = pd.to_datetime(g["end"])
        g["start"] = pd.to_datetime(g["start"])
        g = g.sort_values(["avail_utc", "accn", "val"])
        dur = g[g["start"].notna()]
        inst = g[g["start"].isna()]
        rows = duration_vintages(dur, "x") if not dur.empty else []
        rows += instant_vintages(inst, "x") if not inst.empty else []
        for r in rows:
            r.update(ticker=tk, concept=concept, dims=dims)
            out.append(r)
    v = pd.DataFrame(out)
    if not v.empty:
        v = v.drop(columns=["item"])
    store.save("segment_vintages", v)
    return v


def member_catalog(xf: pd.DataFrame | None = None) -> pd.DataFrame:
    """Which (axis, member) pairs each company uses, with first/last period and fact counts (for mapping)."""
    xf = store.load("sec_xbrl_facts") if xf is None else xf
    rows = defaultdict(lambda: [None, None, 0, set()])
    for r in xf[xf["n_dims"] >= 1].itertuples(index=False):
        for ax, mem in json.loads(r.dims).items():
            k = (r.ticker, ax, mem)
            rec = rows[k]
            e = r.end
            rec[0] = e if rec[0] is None or e < rec[0] else rec[0]
            rec[1] = e if rec[1] is None or e > rec[1] else rec[1]
            rec[2] += 1
            rec[3].add(r.concept)
    return pd.DataFrame([{"ticker": k[0], "axis": k[1], "member": k[2], "first_end": v[0], "last_end": v[1],
                          "n_facts": v[2], "concepts": ";".join(sorted(v[3]))[:400]} for k, v in rows.items()])


def run(f: Fetcher) -> None:
    collect(f)
    v = segment_vintages()
    print(f"  segment vintages: {len(v):,} rows", flush=True)


if __name__ == "__main__":
    fx = Fetcher()
    collect(fx, sys.argv[1:] or None)
    segment_vintages()
