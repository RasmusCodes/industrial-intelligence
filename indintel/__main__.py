"""Command line: python -m indintel <command> [args]

  fetch <source> [...]     download a source into data/raw and build its processed dataset
  build <stage> [...]      rebuild processed datasets from the raw cache (no network unless missing)
  backtest [...]           walk-forward forecasts and evaluation tables (out/)
  export-dashboard [dir]   write the JSON bundle for the static dashboard
  sources                  list connectors
"""
from __future__ import annotations

import argparse
import importlib
import sys
import time

CONNECTORS = {
    # name: (module, function, description)
    "sec": ("indintel.sources.sec_run", "run", "SEC filing index + companyfacts (consolidated XBRL)"),
    "sec_xbrl": ("indintel.sources.sec_xbrl", "run", "XBRL instance documents -> segment facts"),
    "sec_releases": ("indintel.sources.sec_releases", "run", "8-K press releases -> timeline and backlog text"),
    "sec_text": ("indintel.sources.sec_text", "run", "latest annual report text per company (mapping evidence)"),
    "alfred": ("indintel.sources.alfred", "run", "FRED/ALFRED series with weekly vintages"),
    "eia860m": ("indintel.sources.eia860m", "run", "EIA-860M monthly generator inventory vintages"),
    "eia930": ("indintel.sources.eia930", "run", "EIA-930 hourly demand by balancing authority"),
    "eia861m": ("indintel.sources.eia861m", "run", "EIA-861M monthly retail electricity sales by state"),
    "census_c30": ("indintel.sources.census_c30", "run", "Census construction spending incl. data centers"),
    "comtrade": ("indintel.sources.comtrade", "run", "UN Comtrade monthly trade by HS code"),
    "indeed": ("indintel.sources.indeed", "run", "Indeed Hiring Lab job postings by sector"),
    "queues": ("indintel.sources.queues", "run", "ISO interconnection queues (MISO, SPP, CAISO, NYISO)"),
    "usaspending": ("indintel.sources.usaspending", "run", "USAspending federal contract awards"),
    "ted": ("indintel.sources.ted", "run", "EU Tenders Electronic Daily award notices"),
    "tdlr": ("indintel.sources.tdlr", "run", "Texas TDLR TABS construction registrations (data centers)"),
}

STAGES = {
    "fundamentals": ("indintel.fundamentals", "build"),
    "segments": ("indintel.segments", "build"),
    "releases": ("indintel.releases", "build"),
    "indicators": ("indintel.indicators", "build"),
    "panel": ("indintel.panel", "build"),
    "events": ("indintel.events", "build"),
}


def _call(module: str, func: str, *args):
    mod = importlib.import_module(module)
    return getattr(mod, func)(*args)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="indintel")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fetch")
    p.add_argument("sources", nargs="+")
    p.add_argument("--offline", action="store_true", help="use the raw cache only")
    p = sub.add_parser("build")
    p.add_argument("stages", nargs="+")
    p = sub.add_parser("backtest")
    p.add_argument("--period", default="all")
    p = sub.add_parser("export-dashboard")
    p.add_argument("outdir", nargs="?", default=None)
    sub.add_parser("sources")
    p = sub.add_parser("verify-mapping")
    args = ap.parse_args(argv)

    t0 = time.time()
    if args.cmd == "sources":
        for k, (_, _, d) in CONNECTORS.items():
            print(f"{k:14s} {d}")
        return 0
    if args.cmd == "fetch":
        from .http import Fetcher
        f = Fetcher(offline=args.offline)
        names = list(CONNECTORS) if args.sources == ["all"] else args.sources
        for name in names:
            mod, fn, _ = CONNECTORS[name]
            print(f"== fetch {name}", flush=True)
            _call(mod, fn, f)
        print(f"network requests {f.n_network}, cache hits {f.n_cache}")
    elif args.cmd == "build":
        for st in args.stages:
            mod, fn = STAGES[st]
            print(f"== build {st}", flush=True)
            _call(mod, fn)
    elif args.cmd == "backtest":
        _call("indintel.backtest", "main", args.period)
    elif args.cmd == "export-dashboard":
        _call("indintel.export", "main", args.outdir)
    elif args.cmd == "verify-mapping":
        return _call("indintel.mapping", "verify_cli")
    print(f"done in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
