"""8-K press releases of the target companies: headline, date and any backlog / order sentences.

For each 8-K since 2019 carrying Item 1.01 (material agreement), 2.01 (acquisition or disposal), 2.02
(results), 7.01 or 8.01 (other announcements), the press-release exhibit (EX-99.1) is located in the filing
directory and its title and first backlog/order sentences are extracted. These feed the dashboard timeline;
the sentences are verbatim from the exhibit.
"""
from __future__ import annotations

import re

import pandas as pd

from .. import store
from ..http import Fetcher, FetchError
from ..universe import target_tickers
from .sec import archive_url, fetch_doc, filing_index, html_to_text

ITEMS = {"1.01": "Material agreement", "2.01": "Acquisition or disposal", "2.02": "Results",
         "7.01": "Announcement", "8.01": "Other event"}
EX99 = re.compile(r"(ex[-_]?99[-_.]?0?1\b|ex991|exh?99[-_]?1|exhibit[-_]?99[-_.]?1|dex991|99[-_]?1\.htm)", re.I)
SKIP = re.compile(r"^(exhibit|for immediate release|news release|press release|contacts?:?|media|investor|"
                  r"source:|\(?nyse|\(?nasdaq|page \d|www\.|https?:|phone|tel\.?|fax|[\d\-\s().+]{8,}$)", re.I)
KPI = re.compile(r"[^.]{0,220}\b(backlog|orders|order intake|book-to-bill|bookings|remaining performance obligations?)\b[^.]{0,220}"
                 r"(\$\s?[\d.,]+\s?(billion|million)|\d+(\.\d+)?\s?(%|percent))[^.]{0,160}\.", re.I)
SINCE = "2019-01-01"


def headline(text: str, company: str) -> str:
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    for ln in lines[:40]:
        if SKIP.search(ln) or len(ln) < 25 or len(ln) > 240:
            continue
        if re.fullmatch(r"[A-Z0-9 .,&'()\-]+", ln) and company.split()[0].upper() in ln and len(ln.split()) <= 6:
            continue          # company-name banner
        if len(ln.split()) < 4 or not re.search(r"[a-z]", ln + ln.lower()):
            continue
        if re.match(r"^(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d", ln, re.I):
            continue
        return ln
    return ""


def kpi_sentences(text: str, max_n: int = 3) -> list[str]:
    flat = re.sub(r"\s+", " ", text)
    out = []
    for m in KPI.finditer(flat):
        s = re.sub(r"^\d{1,3}\s+(?=[A-Z])", "", m.group(0).strip())     # page numbers glued to the first word
        if 40 <= len(s) <= 400 and s not in out:
            out.append(s)
        if len(out) >= max_n:
            break
    return out


def run(f: Fetcher) -> pd.DataFrame:
    filings = store.load("sec_filings")
    ents = store.load("sec_entities").set_index("ticker")["name"].to_dict()
    k8 = filings[filings["ticker"].isin(target_tickers()) & filings["form"].isin(["8-K"])
                 & (filings["filing_date"] >= SINCE)].copy()
    k8["item_list"] = k8["items"].fillna("").str.split(",")
    k8 = k8[k8["item_list"].map(lambda xs: any(x.strip() in ITEMS for x in xs))]
    rows = []
    for i, r in enumerate(k8.itertuples(index=False), 1):
        try:
            items = filing_index(f, r.cik, r.accn)
            names = [it["name"] for it in items if it["name"].lower().endswith((".htm", ".html", ".txt"))]
            ex = next((n for n in names if EX99.search(n)), None)
            doc = ex or r.primary_doc
            data = fetch_doc(f, r.cik, r.accn, doc, kind="sec_8k")
        except FetchError as e:
            print(f"  {r.ticker} {r.accn}: HTTP {e.status}", flush=True)
            continue
        if data is None:
            continue
        text = html_to_text(data)
        codes = [x.strip() for x in r.item_list if x.strip() in ITEMS]
        rows.append({"ticker": r.ticker, "date": r.filing_date, "accepted_utc": r.acceptance_utc, "accn": r.accn,
                     "items": ",".join(codes), "kind": ITEMS[codes[0]] if codes else "",
                     "headline": headline(text, ents.get(r.ticker, r.ticker)), "kpi": " || ".join(kpi_sentences(text)),
                     "exhibit": bool(ex), "url": archive_url(r.cik, r.accn, doc)})
        if i % 100 == 0:
            print(f"  8-K {i}/{len(k8)} (net {f.n_network}, cache {f.n_cache})", flush=True)
    df = pd.DataFrame(rows)
    store.save("press_releases", df)
    print(f"  press releases: {len(df)}; with headline {df['headline'].astype(bool).mean():.0%}; "
          f"with backlog/order sentence {df['kpi'].astype(bool).mean():.0%}", flush=True)
    return df
