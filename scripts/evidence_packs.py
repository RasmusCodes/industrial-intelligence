"""Cut each annual-report text into a short evidence pack per mapping field (verbatim paragraphs only).

The packs make the mapping work reviewable: every paragraph is copied unchanged from the filing text, so
quotes taken from a pack verify against the original document. Output: data/raw/text/packs/<TICKER>.txt
"""
from __future__ import annotations

import json
import re

from indintel.paths import RAW

TEXT = RAW / "text"
PACKS = TEXT / "packs"

FIELDS = {
    "business_and_segments": r"(reportable segment|operating segment|our segments|segment (is|includes|consists)|"
                             r"we (design|manufacture|provide|sell)|products include|principal products)",
    "geography": r"(outside (of )?the united states|outside the u\.s\.|international (sales|revenue|operations)|"
                 r"(net sales|revenues?) by geograph|geographic (area|region|information)|emea|asia pacific|"
                 r"% of (our )?(net sales|revenues?|sales).{0,40}(united states|north america|international))",
    "customers": r"(largest customer|one customer|single customer|customers? (accounted|represented) for|"
                 r"concentration|hyperscale|colocation|utilit(y|ies) customers?|dealers?\b.{0,40}(independent|network)|"
                 r"end market|end-market|our customers include)",
    "backlog": r"(backlog|remaining performance obligation|unsatisfied performance obligation|order book|"
               r"orders? (received|intake)|bookings|book-to-bill)",
    "pricing": r"(price realization|pricing actions?|price increases?|favorable price|unfavorable price|"
               r"pricing (pressure|environment|power)|selling prices?|price/cost|price-cost|escalation clause)",
    "margins": r"(gross margin|operating margin|segment (margin|profit)|manufacturing costs|material costs|"
               r"cost of (sales|revenue|goods sold) (increased|decreased)|productivity|absorption|"
               r"raw material|commodity (costs|prices)|tariff)",
}
MAX_PER_FIELD = 28
MAX_LEN = 1400


def paragraphs(text: str) -> list[str]:
    """Paragraphs of a filing. Some filings are typeset line by line (each visual line its own HTML block);
    for those the text is re-flowed and cut into three-sentence chunks."""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    longest = max((len(ln) for ln in lines), default=0)
    if longest >= 400:          # real paragraphs exist
        return [p for p in lines if len(p) > 60]
    flowed = re.sub(r"\s+", " ", " ".join(lines))
    sents = re.split(r"(?<=[.!?])\s+(?=[A-Z(•])", flowed)
    return [" ".join(sents[i:i + 3]) for i in range(0, len(sents), 3) if len(" ".join(sents[i:i + 3])) > 60]


def build_pack(text: str) -> str:
    paras = paragraphs(text)
    out = []
    for field, pat in FIELDS.items():
        rx = re.compile(pat, re.I)
        hits = []
        for i, p in enumerate(paras):
            n = len(rx.findall(p))
            if n:
                hits.append((n, i, p))
        # prefer paragraphs with several hits, keep document order within the selection
        chosen = sorted(sorted(hits, key=lambda h: -h[0])[:MAX_PER_FIELD], key=lambda h: h[1])
        out.append(f"\n######## {field.upper()} ({len(hits)} matching paragraphs, {len(chosen)} shown)\n")
        for _, i, p in chosen:
            out.append(f"[para {i}] {p[:MAX_LEN]}")
    return "\n".join(out)


def main() -> None:
    PACKS.mkdir(parents=True, exist_ok=True)
    index = json.loads((TEXT / "index.json").read_text(encoding="utf-8"))
    for rec in index:
        text = (TEXT / rec["path"]).read_text(encoding="utf-8")
        pack = build_pack(text)
        header = (f"EVIDENCE PACK {rec['ticker']} | source: {rec['form']} filed {rec['filing_date']} | {rec['url']}\n"
                  f"full text file: {rec['path']} ({rec['chars']:,} chars). Paragraphs are verbatim from the filing.\n")
        (PACKS / f"{rec['ticker']}.txt").write_text(header + pack, encoding="utf-8")
        print(rec["ticker"], len(pack))


if __name__ == "__main__":
    main()
