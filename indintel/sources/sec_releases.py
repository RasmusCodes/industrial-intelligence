"""8-K press releases of the target companies: headline, date and any backlog / order sentences.

For each 8-K since 2019 carrying Item 1.01 (material agreement), 2.01 (acquisition or disposal), 2.02
(results), 7.01 or 8.01 (other announcements), the press-release exhibit is located in the filing directory
(EX-99.1 under any of the file-naming styles filers use, else a document named like a press release) and its
headline and first backlog/order sentences are extracted. An 8-K without a press release is summarised by the
title of its first reported item and that item's first sentence. These feed the dashboard timeline; headlines
and sentences are verbatim from the filing.

Contact blocks (investor-relations names, phone numbers, e-mail addresses), office addresses, dates and the
8-K cover page are never used as a headline, and phone numbers and e-mail addresses are removed from every
stored text as a last check.
"""
from __future__ import annotations

import html
import re

import pandas as pd

from .. import store
from ..http import Fetcher, FetchError
from ..universe import target_tickers
from .sec import archive_url, decode, fetch_doc, filing_index

ITEMS = {"1.01": "Material agreement", "2.01": "Acquisition or disposal", "2.02": "Results",
         "7.01": "Announcement", "8.01": "Other event"}
ITEM_TITLES = {"1.01": "Entry into a material definitive agreement",
               "2.01": "Completion of an acquisition or disposition of assets",
               "2.02": "Results of operations and financial condition",
               "7.01": "Regulation FD disclosure", "8.01": "Other events"}
SINCE = "2019-01-01"

# ----------------------------------------------------------------------------- exhibit choice

_EX991 = re.compile(r"ex(?:hibit)?[-_x.]*99[-_.x]*0?1(?!\d)", re.I)       # ex99-1, ex991, exx991x.., exhibitx991, dex991
_EX99 = re.compile(r"ex(?:hibit)?[-_x.]*99(?![-_.x]*0?\d)(?!\d)", re.I)     # ex99 with no sub-number
_EX99_ANY = re.compile(r"ex(?:hibit)?[-_x.]*99", re.I)
_EXNUM = re.compile(r"ex(?:hibit)?[-_x.]*(\d{1,3})", re.I)
_RELEASE = re.compile(r"press|release|earnings|news|announce|results", re.I)
_NOT_PR = re.compile(r"letter|sharehold|presentation|slides|transcript|retailstat|supplement|addend|faq|graphic", re.I)
_NOT_DOC = re.compile(r"^R\d+\.htm$|-index(-headers)?\.html?$|\.txt$", re.I)


def pick_exhibit(names: list[str], primary: str) -> str | None:
    """The press-release document among a filing directory's documents, or None (8-K only)."""
    docs = [n for n in names if n != primary and n.lower().endswith((".htm", ".html")) and not _NOT_DOC.search(n)]

    def other_exhibit(n: str) -> bool:                # EX-10 agreements, EX-3/4 charters and indentures, XBRL
        m = _EXNUM.search(n)
        return bool(m) and not m.group(1).startswith("99")

    tiers = [
        [n for n in docs if _EX991.search(n) and not _NOT_PR.search(n)],
        [n for n in docs if _EX99.search(n) and not _NOT_PR.search(n)],
        [n for n in docs if _RELEASE.search(n) and not _NOT_PR.search(n) and not other_exhibit(n)],
        [n for n in docs if _EX99_ANY.search(n) and not _NOT_PR.search(n)],
        [n for n in docs if _EX991.search(n) or _EX99.search(n)],
        [n for n in docs if not other_exhibit(n) and not _NOT_PR.search(n)],
    ]
    for tier in tiers:
        if tier:
            return tier[0]
    return None


# ----------------------------------------------------------------------------- html -> paragraphs

_BLOCK = re.compile(r"(?i)<\s*/?\s*(?:p|div|tr|li|h[1-6]|table|section|ul|ol|center|blockquote|body|title|dd|dt|dl|"
                    r"caption|thead|tbody|tfoot|hr|article|header|footer|t[dh])\b[^>]*>")
_INLINE = re.compile(r"(?i)<\s*/?\s*(?:span|font|b|i|u|a|em|strong|small|big|sup|sub|ins|del|s|strike|tt|abbr|cite|"
                     r"code|mark|q|label|nobr|wbr)\b[^>]*>")
_ANYTAG = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"[ \t\r\n\f\v  -​  　]+")
_LB = " "                                                        # a line break inside a paragraph


def paragraphs(raw: bytes | str, lines: bool = False) -> list[str]:
    """Paragraphs of an EDGAR document. Inline tags join (as a browser renders them), so letter-spaced small capitals
    such as <font>Q</font><font>UANTA</font> read 'QUANTA'. A line break inside a paragraph is a space, or with
    `lines=True` a separate line (headlines and contact blocks are often laid out with <br> only)."""
    s = decode(raw) if isinstance(raw, bytes) else raw
    s = re.sub(r"(?is)<ix:header>.*?</ix:header>", " ", s)
    s = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", s)
    s = re.sub(r"(?s)<!--.*?-->", " ", s)
    if re.search(r"(?i)<pre\b", s) or not re.search(r"(?i)<(?:p|div|table|font|span|br|td)\b", s):
        s = html.unescape(_ANYTAG.sub(" ", s))                       # plain text: blank lines separate paragraphs
        paras = [re.sub(r"[ \t]*\n[ \t]*", _LB, x.strip()) for x in re.split(r"\n\s*\n", s.replace("\r", ""))]
    else:
        s = _SPACES.sub(" ", s)                                       # source newlines are just whitespace in HTML
        s = re.sub(r"(?i)<br\s*/?>", _LB, s)
        s = _BLOCK.sub("\n", s)
        s = _INLINE.sub("", s)
        paras = html.unescape(_ANYTAG.sub(" ", s)).split("\n")
    if lines:
        return [ln for p in paras for ln in (_SPACES.sub(" ", x).strip() for x in p.split(_LB)) if ln]
    return [p for p in (_SPACES.sub(" ", x.replace(_LB, " ")).strip() for x in paras) if p]


# ----------------------------------------------------------------------------- headline

_MONTHS = (r"(?i:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|"
           r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?")
PHONE = re.compile(r"\(?\b\d{3}\)?[-. ‑]\d{3}[-. ‑]\d{4}\b|\+\d{1,3}[\s-]?\(?\d[\d\s().-]{6,}\d")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_BOILER = re.compile(
    r"^(?:exhibit\b|ex-?\s?\d|securities and exchange commission|united states securities|washington,? d\.?\s?c|"
    r"form 8-k|current report|pursuant to|(?:for )?immediate release|news release|press release|news\b|media release|"
    r"earnings release|investor news|contacts?\b|media\b|investors?\b|analysts?\b|source:|\(?nyse\b|\(?nasdaq\b|"
    r"page \d|www\.|https?:|phone\b|tel\b|fax\b|date:|dated\b|forward[- ]looking|cautionary|safe harbor|non-gaap|"
    r"unaudited|table of contents|about\b|\((?:in |dollars|unaudited|millions)|for further information|logo\b)", re.I)
_COVER = re.compile(r"securities and exchange commission|securities exchange act|commission file|form 8-k|"
                    r"current report|exhibit\s+99|ex-99", re.I)
_CONTACT_HARD = re.compile(r"@|\b(?:CFA|IRC)\b|\bcontacts?\s*:|\b(?:e-?mail|tel|phone|fax)\s*:", re.I)
_CONTACT_ROLE = re.compile(
    r"\b(?:investor|media|public|corporate|government|external)\s*(?:&\s*\w+\s+)?(?:relations|affairs|communications)\b|"
    r"\b(?:vice president|director|manager|spokes(?:person|man|woman))\b[\s,-]+(?:of\s+)?"
    r"(?:investor|media|corporate|global|public|external)", re.I)
ALIASES = {"MOD": ("modine",), "KLAC": ("kla",), "GEV": ("vernova",), "URI": ("united rentals",),
           "AMAT": ("applied",), "LRCX": ("lam",), "ROK": ("rockwell",)}
_ADDRESS = re.compile(
    r"\b(?:suite|ste\.)\s*\d|\b\d+\s*(?:st|nd|rd|th)?\s+floor\b|\bp\.?\s?o\.?\s+box\b|"
    r"^\d{1,6}\s+(?:[A-Za-z][\w.]*\s+){0,5}(?:road|rd\.?|street|st\.?|avenue|ave\.?|place|boulevard|blvd\.?|drive|"
    r"dr\.?|parkway|pkwy\.?|way|lane|plaza|circle)\b|,\s*(?:[A-Za-z]+\s+){0,2}\d{5}(?:-\d{4})?\s*$", re.I)
_DATE_LINE = re.compile(rf"^(?:date:?\s*)?(?:(?i:mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)[a-z]*\.?,?\s*)?"
                        rf"{_MONTHS}\s+\d{{1,2}},?\s+\d{{4}}", re.I)
_BODY = re.compile(r"\((?:NYSE|NASDAQ|Nasdaq|NYSE American)\s*:\s*[A-Z.]+\)|/\s*PRNewswire|GLOBE NEWSWIRE|"
                   r"BUSINESS WIRE|\btoday (?:announced|reported)\b|\bannounced today\b")
_VERB = re.compile(
    r"\b(?:reports?|reported|announces?|announced|completes?|completed|declares?|declared|acquires?|acquired|"
    r"agrees?|enters?|selects?|selected|names?|named|appoints?|appointed|launch(?:es)?|launched|signs?|"
    r"signed|awarded|wins?|raises?|reaffirms?|provides?|updates?|prices?|priced|closes?|closed|expands?|delivers?|"
    r"achieves?|posts?|increases?|elects?|elected|joins?|celebrates?|introduces?|unveils?|receives?|secures?|"
    r"opens?|commences?|retires?|succeeds?|withdraws?|revises?|promotes?|promoted|approves?|approved|reach(?:es)?|"
    r"responds?|amends?|hosts?|extends?|advances?|establishes|accelerates?|acquires|completes|"
    r"to (?:acquire|sell|divest|separate|host|present|participate|combine|merge|invest|build|expand|spin|continue|"
    r"become|webcast|report|hold|release|join|lead|retire))\b", re.I)
_NOUN = re.compile(r"\b(?:results|earnings|dividend|offering|transition|acquisition|retirement|successor|outlook|"
                   r"guidance|agreement|merger|spin-?off|separation)\b", re.I)
_RELEASE_BODY = re.compile(
    r"/\s*PRNewswire|GLOBE NEWSWIRE|BUSINESS WIRE|\btoday (?:announced|reported)\b|\b(?:announced|reported) today\b|"
    r"\((?:NYSE|NASDAQ|Nasdaq|NYSE American)\s*:\s*[A-Z.]+\)[^.]{0,200}?\b(?:announced|announces|reported|reports|said|"
    r"has|have|will|is|are|completed|completes|declared|declares|named|names|appointed|appoints|entered|signed|agreed|"
    r"received|posted|delivered|achieved|expects|priced|plans|launched|introduced|unveiled|opened|issued|elected|"
    r"approved|provided|provides|updated|updates|withdrew|withdraws|revised|revises|reached|reaches|celebrates|"
    r"acquired|acquires|joined|joins)\b")
_CAPS_RUN = re.compile(r"(?:\b[A-Z][A-Z0-9&.,'’-]+\b\s+){5,}")
_SPLIT = re.compile(r"([.!?])([\"”’)]*)\s+(?=[\"“(]?[A-Z$])")
_ABBR = {"inc", "corp", "co", "cos", "ltd", "plc", "no", "nos", "mr", "mrs", "ms", "dr", "st", "jr", "sr", "vs", "approx",
         "est", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec", "ill", "calif", "wis",
         "conn", "ind", "pa", "fla", "tex", "mass", "mich", "minn", "mo", "ga", "va", "md", "colo", "ariz", "fig", "ft",
         "sq", "pts", "ref"}
_CONNECT = re.compile(r"(?:\b(?:and|or|the|of|for|in|with|to|at|on|by|from|a|an|its|their|as|into|over|under|after|"
                      r"amid|per|about|than|via|through|including|between|against|towards?|within|across|following|"
                      r"announces?|reports?|declares?|completes?)|[,&:–—-])\s*$", re.I)
_COMPLETE = re.compile(r"\b(?:results|dividend|notes|offering|agreement|acquisition|guidance|directors|officer|board|"
                       r"company|inc\.?|corporation|year|quarter|conference|meeting|program)\s*$", re.I)
_SECTION = re.compile(r"\bhighlights?\b|^(?:overview|outlook|summary|about)\b", re.I)
_NOT_KPI = re.compile(r"amortiz|intangible|purchase accounting", re.I)
_DATELINE = re.compile(
    rf"^(?:[A-Z][A-Za-z.'’]*(?:[ -][A-Z][A-Za-z.'’]*){{0,3}}(?:,\s*[A-Z][A-Za-z.'’ ]{{1,25}}){{0,3}},?\s*)?"
    rf"(?:(?:—|–|--|-)\s*)?(?:{_MONTHS}\s+\d{{1,2}}(?:,?\s+\d{{4}})?\s*)?"
    rf"(?:/\s*PRNewswire(?:-FirstCall)?\s*/|\((?:GLOBE NEWSWIRE|BUSINESS WIRE|PRNewswire)\))?\s*(?:—|–|--|-)\s+")


def scrub(text: str) -> str:
    """Remove e-mail addresses and phone numbers (press-release contact blocks)."""
    t = EMAIL.sub("", PHONE.sub("", text or ""))
    return re.sub(r"\s{2,}", " ", t).strip(" ,;|-–—")


def strip_dateline(s: str) -> str:
    for _ in range(3):                                # 'FREMONT, Calif. -- (PRNewswire) – November 5, 2026 — ...'
        m = _DATELINE.match(s)
        if not (m and (re.search(rf"\d{{4}}|NEWSWIRE|PRNewswire|BUSINESS WIRE|{_MONTHS}\s+\d{{1,2}}", m.group(0))
                       or re.match(r"[A-Z]{3,}", m.group(0)))):
            break
        s = s[m.end():]
    return s


def _contact(p: str, roles: bool = True) -> bool:
    """A contact-block line: phone, e-mail, 'Contacts:', CFA/IRC, or (with roles) an investor-relations or
    communications job title on a line without a headline verb ('names X Vice President, Investor Relations' is news)."""
    if _CONTACT_HARD.search(p) or PHONE.search(p) or EMAIL.search(p):
        return True
    return roles and bool(_CONTACT_ROLE.search(p)) and not _VERB.search(p)


def _sentence_like(s: str) -> bool:
    return len(s) >= 40 and s.rstrip("”\")’").endswith((".", "!", "?")) and bool(re.search(r"\b[a-z]{3,}\b", s))


def shorten(s: str, n: int) -> str:
    s = s.strip()
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def _keys(company: str, ticker: str = "") -> tuple[str, ...]:
    w = re.findall(r"[A-Za-z]+", company or "")
    return tuple(k for k in ((w[0].lower(),) if w else ()) + ALIASES.get(ticker, ()) if k)


def _excluded(p: str, roles: bool = True) -> bool:
    """Never part of a headline: boilerplate, the 8-K cover, contact blocks, addresses, dates, the body."""
    return bool(_BOILER.match(p) or _COVER.search(p) or _contact(p, roles) or _ADDRESS.search(p)
                or _DATE_LINE.match(p) or _BODY.search(p))


def _headline_like(p: str) -> bool:
    n = len(p)
    if n < 20 or n > 300 or len(p.split()) < 4 or _excluded(p):
        return False
    return sum(c.isalpha() for c in p) >= 0.6 * n


def _styled(p: str) -> bool:
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'’-]*", p) if len(w) > 3]
    return bool(words) and sum(w[0].isupper() for w in words) / len(words) >= 0.7


def _merged(paras: list[str], i: int) -> str:
    """paras[i] joined with the paragraphs that continue it (headlines are often split over several blocks)."""
    c, j = paras[i], i + 1
    while j < len(paras) and len(c) < 260:
        n = paras[j]
        joins = bool(re.match(r"(?:and|&|with|to|for|as|of|in|plus)\s", n))      # '... and Luther Peters, Controller'
        if _excluded(n, roles=not joins) or len(n) > 200:
            break
        words = len(n.split())
        short_c = len(c.split()) <= 10          # a long first line is a whole headline; what follows is a sub-headline
        if (joins or _CONNECT.search(c) or (c.isupper() and n.isupper() and words <= 5)
                or ((words <= 2 or (short_c and words <= 12 and len(n) <= 120)) and _styled(c) and _styled(n)
                    and n[:1].isupper() and not n.endswith(".") and not _COMPLETE.search(c)
                    and not _SECTION.search(n))):
            c, j = f"{c} {n}", j + 1
            continue
        break
    return c


def headline(paras: list[str], company: str, ticker: str = "") -> str:
    """The release headline: the first headline-like paragraph (joined with its continuation lines) before the body
    starts, preferring ones with a headline verb, the company's name and headline capitalisation."""
    keys, fallback, styled_any = _keys(company, ticker), "", ""
    for i, p in enumerate(paras[:80]):
        if _BODY.search(p) or len(p) > 400:
            break
        if _excluded(p):
            continue
        p = re.sub(r"(?i)^(?:subject|re)\s*:\s*", "", _merged(paras, i))
        p = re.sub(r"^(?:[`´'\"“”]+\s*|\d{1,3}\s+(?=[A-Z][a-z]))+", "", p)      # stray quote marks, page numbers
        if not _headline_like(p):
            continue
        verb = bool(_VERB.search(p))
        score = 2 * verb + (not verb and bool(_NOUN.search(p))) \
            + any(re.search(rf"\b{re.escape(k)}\b", p, re.I) for k in keys) + _styled(p)
        if p.endswith(".") and len(p) > 160:
            score -= 2
        if score >= 3:
            return p
        if score >= 2 and not fallback:
            fallback = p
        if _styled(p) and not styled_any and not p.endswith("."):
            styled_any = p
    return fallback or styled_any


def split_sentences(p: str) -> list[str]:
    """Sentences of a paragraph; no split after abbreviations (Inc., Ill., U.S.) or initials (Blake D. Moret)."""
    out, start = [], 0
    for m in _SPLIT.finditer(p):
        tok = re.search(r"([A-Za-z.]+)$", p[start:m.start()])
        word = tok.group(1).lower().rstrip(".") if tok else ""
        if m.group(1) == "." and tok and (word in _ABBR or len(word) == 1 or re.fullmatch(r"(?:[a-z]\.)+[a-z]?", word)):
            continue
        end = m.start() + len(m.group(1)) + len(m.group(2))
        out.append(p[start:end].strip())
        start = m.end()
    out.append(p[start:].strip())
    return [s for s in out if s]


def sentences(paras: list[str]):
    for p in paras:
        if len(p) < 40:
            continue
        for s in split_sentences(p):
            yield re.sub(r"^\d{1,3}\s+(?=[A-Z])", "", strip_dateline(s))   # page numbers glued to a word


def _body_start(p: str) -> str:
    """Drop what precedes the dateline when a page has no paragraph breaks (PDF-converted exhibits run the headline,
    sub-headline and first sentence together): start at the last upper-case city before the release marker."""
    m = _RELEASE_BODY.search(p) or _BODY.search(p)
    if m and m.start() > 0:
        cities = list(re.finditer(r"\b[A-Z]{3,}(?:[ -][A-Z]{2,})*,", p[:m.start()]))
        if cities:
            return p[cities[-1].start():]
    return p


def lead_sentence(paras: list[str]) -> str:
    """First sentence of the release body (after the dateline)."""
    for p in paras[:120]:
        if _BODY.search(p) or (len(p) > 120 and _DATELINE.match(p)):
            for s in sentences([_body_start(p)]):
                if _sentence_like(s) and not _contact(s, roles=False):
                    return s
    return ""


_KPI_WORD = re.compile(r"\b(?:backlog|orders|order intake|book-to-bill|bookings|remaining performance obligations?)\b",
                       re.I)
_KPI_NUM = re.compile(r"\$\s?[\d.,]+\s?(?:billion|million|bn)\b|\b\d+(?:\.\d+)?\s?(?:%|percent)", re.I)


def kpi_sentences(paras: list[str], max_n: int = 3) -> list[str]:
    """Verbatim sentences that state backlog, orders or bookings with an amount or a percentage."""
    out = []
    for s in sentences(paras):
        if not (40 <= len(s) <= 400) or len(s.split()) < 8 or not s.rstrip("”\")").endswith((".", "!")):
            continue
        if not (_KPI_WORD.search(s) and _KPI_NUM.search(s)):
            continue
        if _contact(s, roles=False) or _CAPS_RUN.search(s) or _NOT_KPI.search(s):
            continue
        if s not in out:
            out.append(s)
        if len(out) >= max_n:
            break
    return out


_ITEM_TITLE = re.compile(r"^(?:entry into a material definitive agreement|completion of acquisition or disposition of "
                         r"assets|results of operations and financial condition|regulation fd disclosure|other events)"
                         r"\.?\s*", re.I)


def form_lead(paras: list[str], codes: list[str]) -> str:
    """First sentence of the first reported item of an 8-K that has no press release."""
    for i, p in enumerate(paras):
        m = re.match(r"(?i)^item\s*(\d\.\d\d)\b\.?\s*", p)
        if not m or m.group(1) not in codes:
            continue
        texts = [p[m.end():]] + paras[i + 1:i + 6]
        for t in texts:
            t = _ITEM_TITLE.sub("", t.strip())
            if re.match(r"(?i)^item\s*\d", t):
                break
            for s in sentences([t]):
                if _sentence_like(s):
                    return s
    return ""


def extract(raw: bytes | str, company: str, ticker: str = "") -> tuple[str, list[str], bool]:
    """(headline, backlog/order sentences, is a press release) of an exhibit. Slide decks, agreements and other
    exhibits without a release body (newswire tag, 'today announced', '(NYSE: X) reported ...') are not releases."""
    paras = paragraphs(raw)
    if not any(_RELEASE_BODY.search(p) for p in paras[:200]):
        return "", [], False
    h = headline(paragraphs(raw, lines=True), company, ticker) or shorten(lead_sentence(paras), 220)
    return h, kpi_sentences(paras), True


def run(f: Fetcher) -> pd.DataFrame:
    filings = store.load("sec_filings")
    ents = store.load("sec_entities").set_index("ticker")["name"].to_dict()
    k8 = filings[filings["ticker"].isin(target_tickers()) & filings["form"].isin(["8-K"])
                 & (filings["filing_date"] >= SINCE)].copy()
    k8["item_list"] = k8["items"].fillna("").str.split(",")
    k8 = k8[k8["item_list"].map(lambda xs: any(x.strip() in ITEMS for x in xs))]
    rows = []
    for i, r in enumerate(k8.itertuples(index=False), 1):
        codes = [x.strip() for x in r.item_list if x.strip() in ITEMS]
        try:
            items = filing_index(f, r.cik, r.accn)
            names = [it["name"] for it in items]
            ex = pick_exhibit(names, r.primary_doc)
            head, kpi, lead, release = "", [], "", False
            if ex:
                head, kpi, release = extract(fetch_doc(f, r.cik, r.accn, ex, kind="sec_8k") or b"",
                                             ents.get(r.ticker, r.ticker), r.ticker)
            if not head:
                lead = form_lead(paragraphs(fetch_doc(f, r.cik, r.accn, r.primary_doc, kind="sec_8k") or b""), codes)
                head = ITEM_TITLES[codes[0]] if codes else ""
        except FetchError as e:
            print(f"  {r.ticker} {r.accn}: HTTP {e.status}", flush=True)
            continue
        doc = ex if (ex and release) else r.primary_doc
        rows.append({"ticker": r.ticker, "date": r.filing_date, "accepted_utc": r.acceptance_utc, "accn": r.accn,
                     "items": ",".join(codes), "kind": ITEMS[codes[0]] if codes else "",
                     "headline": scrub(head), "kpi": " || ".join(scrub(k) for k in kpi), "lead": scrub(shorten(lead, 320)),
                     "exhibit": bool(ex and release), "doc": doc, "url": archive_url(r.cik, r.accn, doc)})
        if i % 100 == 0:
            print(f"  8-K {i}/{len(k8)} (net {f.n_network}, cache {f.n_cache})", flush=True)
    df = pd.DataFrame(rows)
    store.save("press_releases", df)
    print(f"  press releases: {len(df)}; with a press-release exhibit {df['exhibit'].mean():.0%}; "
          f"with backlog/order sentence {df['kpi'].astype(bool).mean():.0%}", flush=True)
    return df
