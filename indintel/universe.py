"""Universe loader (config/universe.yaml)."""
from __future__ import annotations

from functools import lru_cache

import pandas as pd
import yaml

from .paths import CONFIG


@lru_cache(maxsize=1)
def raw() -> dict:
    with (CONFIG / "universe.yaml").open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def targets() -> pd.DataFrame:
    return pd.DataFrame(raw()["targets"])


def sec_entities() -> pd.DataFrame:
    """Every SEC filer the pipeline downloads: targets, annual-only filers and indicator-panel members."""
    rows = []
    for r in raw()["targets"]:
        rows.append({**r, "role": "target"})
    for r in raw().get("annual_only", []):
        rows.append({**r, "role": "annual_only"})
    for panel, spec in raw().get("indicator_panels", {}).items():
        for m in spec["members"]:
            rows.append({**m, "role": f"panel:{panel}"})
    df = pd.DataFrame(rows)
    return df.drop_duplicates("ticker").reset_index(drop=True)


def panel_members(panel: str) -> list[str]:
    return [m["ticker"] for m in raw()["indicator_panels"][panel]["members"]]


def target_tickers() -> list[str]:
    return [r["ticker"] for r in raw()["targets"]]
