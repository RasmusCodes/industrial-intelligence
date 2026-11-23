"""Tenders Electronic Daily (TED), the EU public procurement journal: contract award notices for grid equipment.

Search API v3 (anonymous, EU open-data reuse terms). Award notices ('can-standard', 'can-modif') whose CPV
codes cover transformers (3117xxxx), switchgear and boards (31211000, 31214000) and power cable (31321000)
are counted by publication date. Coverage in the API starts in 2018. Utilities in the EU publish most
large equipment awards here, so the series is a public read on European grid-equipment demand that ABB,
Siemens Energy (Grid Technologies), Schneider Electric and Hitachi Energy compete for.
'event' series: available 3 days after month end.
"""
from __future__ import annotations

import pandas as pd

from .. import store
from ..http import Fetcher
from ..indicators_config import series_for_source

URL = "https://api.ted.europa.eu/v3/notices/search"
CPV = "31170000 31172000 31173000 31174000 31211000 31214000 31321000"
FIELDS = ["publication-number", "publication-date", "notice-type", "buyer-country", "classification-cpv",
          "result-value-notice", "total-value-cur", "buyer-name"]


def fetch_year(f: Fetcher, year: int, fresh: bool) -> list[dict]:
    out, page = [], 1
    while True:
        q = {"query": f"classification-cpv IN ({CPV}) AND notice-type IN (can-standard can-modif) "
                      f"AND publication-date >= {year}0101 AND publication-date <= {year}1231",
             "fields": FIELDS, "limit": 250, "page": page}
        js = f.get_json(URL, "ted", post_json=q, max_age_hours=24 * 7 if fresh else None)
        notes = js.get("notices", [])
        out += notes
        if len(notes) < 250 or page * 250 >= int(js.get("totalNoticeCount") or 0) or page >= 60:
            break
        page += 1
    return out


def _first(x):
    if isinstance(x, list):
        return x[0] if x else None
    if isinstance(x, dict):
        vals = [v for vs in x.values() for v in (vs if isinstance(vs, list) else [vs])]
        return vals[0] if vals else None
    return x


def run(f: Fetcher) -> pd.DataFrame:
    this_year = pd.Timestamp.today().year
    rows = []
    for y in range(2018, this_year + 1):
        notes = fetch_year(f, y, fresh=y >= this_year - 1)
        for n in notes:
            rows.append({"pub_no": n.get("publication-number"), "date": str(n.get("publication-date", ""))[:10],
                         "type": n.get("notice-type"), "country": _first(n.get("buyer-country")),
                         "cpv": ";".join(sorted(set(n.get("classification-cpv") or []))),
                         "value": pd.to_numeric(n.get("result-value-notice"), errors="coerce"),
                         "currency": _first(n.get("total-value-cur")), "buyer": _first(n.get("buyer-name"))})
        print(f"  TED {y}: {len(notes)} award notices", flush=True)
    df = pd.DataFrame(rows).drop_duplicates("pub_no")
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["month"] = df["date"].dt.to_period("M").dt.to_timestamp()
    store.save("ted_awards", df)
    frames = []
    full = pd.date_range("2018-01-01", df["month"].max(), freq="MS")
    for spec in series_for_source("ted"):
        m = df.groupby("month").size().reindex(full, fill_value=0)
        s = pd.DataFrame({"period": m.index, "value": m.values.astype(float)})
        s["series_id"], s["freq"] = spec["id"], "M"
        s["available_at"] = (s["period"] + pd.offsets.MonthEnd(0) + pd.Timedelta(days=3)).dt.tz_localize("UTC")
        s["vintage_kind"], s["source"] = "event", "ted"
        frames.append(s[store.OBS_COLUMNS])
    out = store.check_obs(pd.concat(frames, ignore_index=True))
    store.save("obs_ted", out)
    return out
