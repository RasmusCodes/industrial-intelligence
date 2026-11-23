"""SEC EDGAR: filing index with acceptance timestamps, XBRL companyfacts, filing documents.

* data.sec.gov submissions JSON -> every filing with its UTC acceptance time (point-in-time anchor)
* data.sec.gov companyfacts JSON -> non-dimensional XBRL facts with the accession that reported them
* www.sec.gov Archives -> filing indexes, XBRL instance documents (segment facts), press-release exhibits
"""
from __future__ import annotations

import html
import json
import re

import pandas as pd

from .. import store
from ..http import Fetcher
from ..universe import sec_entities

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SUBMISSIONS_PAGE = "https://data.sec.gov/submissions/{name}"
COMPANYFACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
ARCHIVE_DIR = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/"
API_MAX_AGE_H = 24.0

FIN_FORMS = ("10-Q", "10-K", "10-KT", "10-Q/A", "10-K/A", "10-KT/A", "20-F", "20-F/A", "40-F")

# us-gaap concepts kept from companyfacts (candidates per line item live in fundamentals.ITEMS)
FACT_CONCEPTS = {
    "Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
    "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueGoodsNet", "SalesRevenueServicesNet",
    "CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold",
    "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization", "GrossProfit", "OperatingIncomeLoss",
    "SellingGeneralAndAdministrativeExpense", "ResearchAndDevelopmentExpense",
    "PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
    "PaymentsForCapitalImprovements", "PaymentsToAcquireOtherPropertyPlantAndEquipment",
    "RevenueRemainingPerformanceObligation", "InventoryNet", "ContractWithCustomerLiabilityCurrent",
    "ContractWithCustomerLiability", "DepreciationDepletionAndAmortization", "NetIncomeLoss",
}


def submissions(f: Fetcher, cik: int, refresh_hours: float = API_MAX_AGE_H) -> tuple[dict, pd.DataFrame]:
    js = f.get_json(SUBMISSIONS.format(cik=cik), "sec_submissions", max_age_hours=refresh_hours)
    frames = [pd.DataFrame(js["filings"]["recent"])]
    for page in js["filings"].get("files", []):
        pj = f.get_json(SUBMISSIONS_PAGE.format(name=page["name"]), "sec_submissions", max_age_hours=24 * 30)
        frames.append(pd.DataFrame(pj))
    df = pd.concat(frames, ignore_index=True)
    df["cik"] = cik
    header = {k: js.get(k) for k in ("cik", "name", "sic", "sicDescription", "fiscalYearEnd", "tickers",
                                      "exchanges", "entityType", "stateOfIncorporation")}
    header["formerNames"] = js.get("formerNames", [])
    return header, df


def companyfacts(f: Fetcher, cik: int, refresh_hours: float = API_MAX_AGE_H) -> dict | None:
    return f.get_json(COMPANYFACTS.format(cik=cik), "sec_companyfacts", max_age_hours=refresh_hours, allow_404=True)


def flatten_facts(js: dict, ticker: str, concepts: set[str] | None = None) -> pd.DataFrame:
    out = []
    cik = int(js.get("cik", 0))
    for tax, block in js.get("facts", {}).items():
        for concept, cd in block.items():
            if concepts is not None and concept not in concepts:
                continue
            for unit, items in cd.get("units", {}).items():
                for it in items:
                    out.append((ticker, cik, tax, concept, unit, it.get("start"), it.get("end"), it.get("val"),
                                it.get("accn"), it.get("fy"), it.get("fp"), it.get("form"), it.get("filed"),
                                it.get("frame")))
    cols = ["ticker", "cik", "taxonomy", "concept", "unit", "start", "end", "val", "accn", "fy", "fp", "form",
            "filed", "frame"]
    return pd.DataFrame(out, columns=cols)


def build_filings(f: Fetcher, refresh_hours: float = API_MAX_AGE_H) -> pd.DataFrame:
    ents = sec_entities()
    rows, heads = [], []
    for _, u in ents.iterrows():
        head, df = submissions(f, int(u["cik"]), refresh_hours)
        df["ticker"] = u["ticker"]
        df["role"] = u["role"]
        rows.append(df)
        heads.append({**head, "ticker": u["ticker"], "role": u["role"]})
    filings = pd.concat(rows, ignore_index=True)
    keep = ["ticker", "role", "cik", "accessionNumber", "form", "filingDate", "reportDate", "acceptanceDateTime",
            "items", "primaryDocument", "primaryDocDescription", "size", "isXBRL", "isInlineXBRL"]
    filings = filings[[c for c in keep if c in filings.columns]].rename(columns={
        "accessionNumber": "accn", "filingDate": "filing_date", "reportDate": "report_date",
        "acceptanceDateTime": "acceptance_raw", "primaryDocument": "primary_doc",
        "primaryDocDescription": "primary_desc", "isXBRL": "is_xbrl", "isInlineXBRL": "is_ixbrl"})
    # EDGAR acceptanceDateTime carries a 'Z' suffix and is UTC.
    filings["acceptance_utc"] = pd.to_datetime(filings["acceptance_raw"], utc=True, errors="coerce")
    filings = filings.drop_duplicates(["cik", "accn"]).sort_values(["ticker", "acceptance_utc"]).reset_index(drop=True)
    store.save("sec_filings", filings)
    hd = pd.DataFrame(heads)
    for c in ("formerNames", "tickers", "exchanges"):
        hd[c] = hd[c].apply(json.dumps)
    store.save("sec_entities", hd)
    return filings


def build_facts(f: Fetcher, refresh_hours: float = API_MAX_AGE_H) -> pd.DataFrame:
    ents = sec_entities()
    frames = []
    for _, u in ents.iterrows():
        js = companyfacts(f, int(u["cik"]), refresh_hours)
        if js is None:
            continue
        frames.append(flatten_facts(js, u["ticker"], FACT_CONCEPTS))
    facts = pd.concat(frames, ignore_index=True)
    store.save("sec_facts", facts)
    return facts


# ----------------------------------------------------------------------------- archives

def acc_nodash(accn: str) -> str:
    return accn.replace("-", "")


def filing_index(f: Fetcher, cik: int, accn: str) -> list[dict]:
    url = ARCHIVE_DIR.format(cik=int(cik), acc_nodash=acc_nodash(accn)) + "index.json"
    js = f.get_json(url, "sec_index", allow_404=True)
    if not js:
        return []
    return js.get("directory", {}).get("item", [])


_LINKBASE = re.compile(r"_(cal|def|lab|pre)\.xml$", re.I)


def instance_name(items: list[dict]) -> str | None:
    """The XBRL instance document in a filing directory (inline filings: '<doc>_htm.xml')."""
    names = [it["name"] for it in items]
    for n in names:
        if n.lower().endswith("_htm.xml"):
            return n
    cands = [n for n in names if n.lower().endswith(".xml") and not _LINKBASE.search(n)
             and n not in ("FilingSummary.xml",) and not n.lower().startswith("r")]
    cands = [n for n in cands if re.match(r"^[a-z0-9\-_.]+-\d{8}\.xml$", n, re.I)] or cands
    return cands[0] if len(cands) == 1 else (sorted(cands, key=len)[0] if cands else None)


def archive_url(cik: int, accn: str, name: str) -> str:
    return ARCHIVE_DIR.format(cik=int(cik), acc_nodash=acc_nodash(accn)) + name


def fetch_doc(f: Fetcher, cik: int, accn: str, name: str, kind: str = "sec_docs") -> bytes | None:
    return f.get(archive_url(cik, accn, name), kind, allow_404=True)


# ----------------------------------------------------------------------------- html -> text

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v ]+")


def decode(raw: bytes) -> str:
    """EDGAR documents are UTF-8 or Windows-1252 (older filer software); try strict UTF-8 first."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def html_to_text(raw: bytes | str) -> str:
    """Readable text from EDGAR HTML: drops hidden iXBRL header, scripts and styles; keeps block breaks."""
    s = decode(raw) if isinstance(raw, bytes) else raw
    s = re.sub(r"(?is)<ix:header>.*?</ix:header>", " ", s)
    s = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(p|div|tr|li|h[1-6]|table|section)>", "\n", s)
    s = re.sub(r"(?i)</t[dh]>", " \t ", s)
    s = _TAG.sub(" ", s)
    s = html.unescape(s)
    s = _WS.sub(" ", s)
    lines = [ln.strip() for ln in s.split("\n")]
    out, blank = [], 0
    for ln in lines:
        if not ln:
            blank += 1
            if blank <= 1:
                out.append("")
            continue
        blank = 0
        out.append(ln)
    return "\n".join(out).strip()
