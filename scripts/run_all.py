"""Rebuild every dataset, result table and the dashboard bundle from the raw cache.

    python scripts/run_all.py                 # offline rebuild from data/raw (no network)
    python scripts/run_all.py --fetch         # refresh all sources first (needs SEC_USER_AGENT for EDGAR)
    python scripts/run_all.py --from panel    # start at a later step

The returns step runs only when INDINTEL_CRSP_DIR points at a local CRSP extract (licensed data).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

STEPS = ["fundamentals", "releases", "segments", "indicators", "panel", "events", "backtest", "returns", "export"]


def run_step(name: str) -> None:
    t0 = time.time()
    print(f"== {name}", flush=True)
    if name == "fundamentals":
        from indintel.fundamentals import build
        build()
    elif name == "releases":
        from indintel.releases import build
        build()
    elif name == "segments":
        from indintel.segments import build
        build()
    elif name == "indicators":
        from indintel.indicators import build
        build()
    elif name == "panel":
        from indintel.panel import build
        build()
    elif name == "events":
        from indintel.events import build
        build()
    elif name == "backtest":
        from indintel.backtest import main
        main()
    elif name == "returns":
        from indintel.returns import run
        run()
    elif name == "export":
        from indintel.export import main
        main()
    print(f"   {name} done in {time.time() - t0:.0f}s", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--from", dest="start", default=STEPS[0], choices=STEPS)
    args = ap.parse_args()
    if args.fetch:
        from indintel.__main__ import main as cli
        cli(["fetch", "all"])
    for s in STEPS[STEPS.index(args.start):]:
        run_step(s)


if __name__ == "__main__":
    main()
