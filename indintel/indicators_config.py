"""Loader and validator for config/indicators.yaml."""
from __future__ import annotations

from functools import lru_cache

import yaml

from .paths import CONFIG
from .store import VINTAGE_KINDS

REQUIRED = ("id", "name", "source", "params", "freq", "agg", "unit", "vintage_kind", "sectors", "link")
AGGS = ("sum", "mean", "last")


@lru_cache(maxsize=1)
def registry() -> list[dict]:
    with (CONFIG / "indicators.yaml").open(encoding="utf-8") as fh:
        items = yaml.safe_load(fh)["indicators"]
    validate(items)
    return items


def validate(items: list[dict]) -> None:
    seen = set()
    for it in items:
        miss = [k for k in REQUIRED if k not in it]
        if miss:
            raise ValueError(f"indicator {it.get('id')} lacks {miss}")
        if it["id"] in seen:
            raise ValueError(f"duplicate indicator id {it['id']}")
        seen.add(it["id"])
        if it["agg"] not in AGGS:
            raise ValueError(f"{it['id']}: agg must be one of {AGGS}")
        if it["vintage_kind"] not in VINTAGE_KINDS:
            raise ValueError(f"{it['id']}: unknown vintage_kind {it['vintage_kind']}")
        if it["vintage_kind"] == "lagged" and not it.get("pub_lag_days"):
            raise ValueError(f"{it['id']}: lagged series need pub_lag_days")


def by_id() -> dict[str, dict]:
    return {it["id"]: it for it in registry()}


def series_for_source(source: str) -> list[dict]:
    return [it for it in registry() if it["source"] == source]
