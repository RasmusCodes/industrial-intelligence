"""Repository paths. Raw downloads and built datasets live under data/ (not committed)."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
DATA = Path(os.environ.get("INDINTEL_DATA", ROOT / "data"))
RAW = DATA / "raw"
PROCESSED = DATA / "processed"
OUT = ROOT / "out"
DOCS = ROOT / "docs"
LOGS = DATA / "logs"

for _p in (RAW, PROCESSED, LOGS):
    _p.mkdir(parents=True, exist_ok=True)
