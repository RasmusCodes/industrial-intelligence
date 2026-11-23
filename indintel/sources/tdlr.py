"""Texas Department of Licensing and Regulation, Texas Architectural Barriers System (TABS).

Texas requires commercial building projects with an estimated construction cost of $50,000 or more to be
registered with TDLR for an accessibility review before construction. The public TABS project search
(robots.txt allows it) returns each registration with its creation timestamp, estimated cost and dates.
Projects named as data centers form a dated, public pipeline of data center construction in Texas.

'event' series: a registration counts in the month it was created, available 7 days after month end.
Limitations: name-based selection (a data center registered under a code name is missed) and estimated
costs are the registrant's own estimates for the building permit scope, not the IT or electrical fit-out.
"""
from __future__ import annotations

import re

import pandas as pd

from .. import store
from ..http import Fetcher
from ..indicators_config import series_for_source

SEARCH = "https://www.tdlr.texas.gov/TABS/Search/SearchProjects"
TERMS = ("data center", "datacenter", "data centre")
PAGE = 100
DC = re.compile(r"data\s?cent(er|re)|datacenter", re.I)


def search(f: Fetcher, term: str) -> list[dict]:
    rows, start, total = [], 0, None
    while total is None or start < total:
        js = f.get_json(SEARCH, "tdlr", post_form={"draw": "1", "start": str(start), "length": str(PAGE),
                                                   "ProjectName": term}, max_age_hours=24 * 7)
        total = int(js.get("recordsFiltered") or 0)
        data = js.get("data", [])
        rows += data
        if not data:
            break
        start += PAGE
    return rows


def run(f: Fetcher) -> pd.DataFrame:
    recs = []
    for term in TERMS:
        recs += search(f, term)
    df = pd.DataFrame(recs).drop_duplicates("ProjectNumber")
    df = df[df["ProjectName"].fillna("").str.contains(DC) | df.get("FacilityName", pd.Series("", index=df.index)).fillna("").str.contains(DC)]
    df["created"] = pd.to_datetime(df["ProjectCreatedOn"], errors="coerce")
    df["cost"] = pd.to_numeric(df["EstimatedCost"], errors="coerce")
    df["month"] = df["created"].dt.to_period("M").dt.to_timestamp()
    store.save("tdlr_dc_projects", df)
    print(f"  TABS data center registrations: {len(df)} from {df['created'].min():%Y-%m} to {df['created'].max():%Y-%m}, "
          f"estimated cost ${df['cost'].sum() / 1e9:.1f}bn", flush=True)
    frames = []
    full = pd.date_range("2012-01-01", df["month"].max(), freq="MS")
    for spec in series_for_source("tdlr"):
        meas = spec["params"]["measure"]
        m = (df.groupby("month")["cost"].sum() if meas == "cost" else df.groupby("month").size()).reindex(full, fill_value=0.0)
        s = pd.DataFrame({"period": m.index, "value": m.values.astype(float)})
        s["series_id"], s["freq"] = spec["id"], "M"
        s["available_at"] = (s["period"] + pd.offsets.MonthEnd(0) + pd.Timedelta(days=7)).dt.tz_localize("UTC")
        s["vintage_kind"], s["source"] = "event", "tdlr"
        frames.append(s[store.OBS_COLUMNS])
    out = store.check_obs(pd.concat(frames, ignore_index=True))
    store.save("obs_tdlr", out)
    return out
