"""Plain-text copies of each company's latest annual report (10-K, or 20-F for ABB) for the mapping layer.

The mapping database (config/companies.yaml) cites verbatim sentences from these documents; `python -m
indintel verify-mapping` checks every quote against the same text, so each qualitative claim is traceable.
"""
from __future__ import annotations

import json

import pandas as pd

from .. import store
from ..http import Fetcher
from ..paths import RAW
from ..universe import raw as uraw
from .sec import archive_url, fetch_doc, html_to_text

TEXT_DIR = RAW / "text"
ANNUAL = ("10-K", "20-F")


def latest_annual(filings: pd.DataFrame, ticker: str, n: int = 1) -> pd.DataFrame:
    fl = filings[(filings["ticker"] == ticker) & filings["form"].isin(ANNUAL)]
    return fl.sort_values("acceptance_utc").tail(n)


def text_path(ticker: str, form: str, filing_date: str):
    return TEXT_DIR / f"{ticker}_{form.replace('/', '')}_{filing_date}.txt"


def run(f: Fetcher, n_years: int = 1) -> pd.DataFrame:
    filings = store.load("sec_filings")
    u = uraw()
    tickers = [r["ticker"] for r in u["targets"]] + [r["ticker"] for r in u.get("annual_only", [])]
    TEXT_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for tk in tickers:
        for r in latest_annual(filings, tk, n_years).itertuples(index=False):
            data = fetch_doc(f, r.cik, r.accn, r.primary_doc, kind="sec_docs")
            if data is None:
                continue
            txt = html_to_text(data)
            p = text_path(tk, r.form, r.filing_date)
            p.write_text(txt, encoding="utf-8")
            rows.append({"ticker": tk, "form": r.form, "filing_date": r.filing_date, "accn": r.accn,
                         "url": archive_url(r.cik, r.accn, r.primary_doc), "path": p.name, "chars": len(txt)})
            print(f"  {tk} {r.form} {r.filing_date}: {len(txt):,} chars", flush=True)
    df = pd.DataFrame(rows)
    store.save("annual_texts", df)
    (TEXT_DIR / "index.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return df
