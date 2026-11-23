"""Company mapping database: config/companies.yaml (indicator links) + config/profiles/<TICKER>.yaml (cited
company profiles: segments, geography, customers, backlog, pricing, margin drivers).

Every profile quote is checked against the filing text it cites (``verify``): whitespace is normalised and
typographic quotes are folded, nothing else. A profile with an unverifiable quote fails the test suite.
"""
from __future__ import annotations

import re
import sys
import unicodedata
from functools import lru_cache
from pathlib import Path

import pandas as pd
import yaml

from .indicators_config import by_id
from .paths import CONFIG, RAW

PROFILES = CONFIG / "profiles"
TEXT = RAW / "text"
ROLES = ("demand", "pricing", "cost")


@lru_cache(maxsize=1)
def companies() -> list[dict]:
    with (CONFIG / "companies.yaml").open(encoding="utf-8") as fh:
        items = yaml.safe_load(fh)["companies"]
    validate(items)
    return items


def validate(items: list[dict]) -> None:
    ids = by_id()
    seen = set()
    for c in items:
        if c["ticker"] in seen:
            raise ValueError(f"duplicate company {c['ticker']}")
        seen.add(c["ticker"])
        for d in c.get("drivers", []):
            if d["indicator"] not in ids:
                raise ValueError(f"{c['ticker']}: unknown indicator {d['indicator']}")
            if d["role"] not in ROLES:
                raise ValueError(f"{c['ticker']}: role must be one of {ROLES}")


def segment_name_map() -> dict[str, dict[str, str]]:
    return {c["ticker"]: dict(c.get("segment_names") or {}) for c in companies()}


def drivers() -> pd.DataFrame:
    rows = []
    for c in companies():
        for d in c.get("drivers", []):
            rows.append({"ticker": c["ticker"], **d})
    return pd.DataFrame(rows)


def demand_indicators(ticker: str) -> list[str]:
    d = drivers()
    return d[(d["ticker"] == ticker) & (d["role"] == "demand")]["indicator"].tolist()


def role_indicators(ticker: str, role: str) -> list[str]:
    d = drivers()
    return d[(d["ticker"] == ticker) & (d["role"] == role)]["indicator"].tolist()


# ----------------------------------------------------------------------------- profiles + quote checks

def load_profiles() -> dict[str, dict]:
    out = {}
    for p in sorted(PROFILES.glob("*.yaml")):
        with p.open(encoding="utf-8") as fh:
            out[p.stem] = yaml.safe_load(fh)
    return out


_FOLD = {"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " ",
         "•": " ", "●": " "}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    for a, b in _FOLD.items():
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


@lru_cache(maxsize=64)
def _doc(name: str) -> str:
    return norm((TEXT / name).read_text(encoding="utf-8"))


def iter_quotes(profile: dict):
    for field in ("geography", "customers", "backlog", "pricing", "margin_drivers"):
        block = profile.get(field) or {}
        for ev in block.get("evidence") or []:
            yield field, ev.get("quote", ""), ev.get("document") or profile.get("document")


def verify(profiles: dict | None = None) -> pd.DataFrame:
    profiles = load_profiles() if profiles is None else profiles
    rows = []
    for tk, prof in profiles.items():
        for field, quote, doc in iter_quotes(prof):
            ok = False
            reason = ""
            if not doc or not (TEXT / doc).exists():
                reason = "document missing"
            elif len(quote) < 20:
                reason = "quote too short"
            else:
                ok = norm(quote) in _doc(doc)
                reason = "" if ok else "not found verbatim"
            rows.append({"ticker": tk, "field": field, "document": doc, "ok": ok, "reason": reason,
                         "quote": quote[:120]})
    return pd.DataFrame(rows)


def verify_cli() -> int:
    if not TEXT.exists():
        print("filing texts not downloaded: run `python -m indintel fetch sec_text` first")
        return 1
    res = verify()
    if res.empty:
        print("no profile quotes found")
        return 1
    bad = res[~res["ok"]]
    print(f"{len(res)} quotes checked, {len(res) - len(bad)} verified, {len(bad)} failed")
    for r in bad.itertuples(index=False):
        print(f"  FAIL {r.ticker} {r.field}: {r.reason}: {r.quote}")
    return 0 if bad.empty else 1


if __name__ == "__main__":
    sys.exit(verify_cli())
