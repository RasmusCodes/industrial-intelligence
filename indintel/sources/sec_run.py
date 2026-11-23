"""`fetch sec`: filing index and companyfacts for every SEC entity, then the consolidated vintage replay."""
from __future__ import annotations

from ..fundamentals import build as build_fundamentals
from ..http import Fetcher
from .sec import build_facts, build_filings


def run(f: Fetcher) -> None:
    filings = build_filings(f)
    print(f"  filings: {len(filings):,} rows, {filings['ticker'].nunique()} entities", flush=True)
    facts = build_facts(f)
    print(f"  companyfacts: {len(facts):,} fact rows", flush=True)
    v = build_fundamentals()
    print(f"  fundamentals vintages: {len(v):,} rows", flush=True)
