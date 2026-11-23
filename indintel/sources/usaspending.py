"""USAspending.gov: federal contract awards to the target companies (timeline and context, not a model input).

Search is by recipient name as recorded today; names are filtered with a per-company pattern to drop
look-alikes. Dates are each award's period-of-performance start. Federal buying is a small share of revenue
for every company here, so awards are shown as events, not used as a demand indicator.
"""
from __future__ import annotations

import re

import pandas as pd

from .. import store
from ..http import Fetcher, FetchError

URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
RECIPIENTS = {
    "CAT": ("CATERPILLAR", r"^CATERPILLAR"),
    "VRT": ("VERTIV", r"^VERTIV"),
    "ETN": ("EATON", r"^EATON (CORP|ELECTRICAL|AEROSPACE|INDUSTRIES|LIMITED|CORPORATION)"),
    "GEV": ("GE VERNOVA", r"^GE VERNOVA|^GE GRID|^GE POWER|^GE ENERGY"),
    "PWR": ("QUANTA", r"^QUANTA (SERVICES|ENERGY|UTILITY|INFRASTRUCTURE|TECHNOLOGY)"),
    "BE": ("BLOOM ENERGY", r"^BLOOM ENERGY"),
    "HUBB": ("HUBBELL", r"^HUBBELL"),
    "POWL": ("POWELL INDUSTRIES", r"^POWELL INDUSTRIES"),
    "MOD": ("MODINE", r"^MODINE"),
    "NVT": ("NVENT", r"^NVENT"),
    "CMI": ("CUMMINS", r"^CUMMINS (INC|POWER|SALES|ENGINE|FILTRATION|EMISSION|WESTPORT)"),
    "URI": ("UNITED RENTALS", r"^UNITED RENTALS"),
    "MTZ": ("MASTEC", r"^MASTEC"),
    "ROK": ("ROCKWELL AUTOMATION", r"^ROCKWELL AUTOMATION"),
    "AMAT": ("APPLIED MATERIALS", r"^APPLIED MATERIALS"),
    "LRCX": ("LAM RESEARCH", r"^LAM RESEARCH"),
    "KLAC": ("KLA CORP", r"^KLA[- ](CORP|TENCOR)"),
}
FIELDS = ["Award ID", "Recipient Name", "Start Date", "End Date", "Award Amount", "Awarding Agency",
          "Awarding Sub Agency", "Description", "generated_internal_id"]


def run(f: Fetcher, pages: int = 3) -> pd.DataFrame:
    rows = []
    for tk, (text, pat) in RECIPIENTS.items():
        rx = re.compile(pat, re.I)
        n = 0
        for page in range(1, pages + 1):
            body = {"filters": {"award_type_codes": ["A", "B", "C", "D"], "recipient_search_text": [text],
                                "time_period": [{"start_date": "2015-10-01", "end_date": pd.Timestamp.today().strftime("%Y-%m-%d")}]},
                    "fields": FIELDS, "sort": "Award Amount", "order": "desc", "limit": 100, "page": page}
            try:
                js = f.get_json(URL, "usaspending", post_json=body, max_age_hours=24 * 14)
            except FetchError as e:
                print(f"  {tk} page {page}: HTTP {e.status}", flush=True)
                break
            res = js.get("results", [])
            for a in res:
                if not rx.search(str(a.get("Recipient Name") or "")):
                    continue
                rows.append({"ticker": tk, "award_id": a.get("Award ID"), "recipient": a.get("Recipient Name"),
                             "start": a.get("Start Date"), "end": a.get("End Date"), "amount": a.get("Award Amount"),
                             "agency": a.get("Awarding Agency"), "sub_agency": a.get("Awarding Sub Agency"),
                             "description": (a.get("Description") or "")[:240],
                             "url": f"https://www.usaspending.gov/award/{a.get('generated_internal_id')}"})
                n += 1
            if not js.get("page_metadata", {}).get("hasNext"):
                break
        print(f"  {tk}: {n} awards", flush=True)
    df = pd.DataFrame(rows).drop_duplicates(["ticker", "award_id"])
    store.save("federal_awards", df)
    return df
