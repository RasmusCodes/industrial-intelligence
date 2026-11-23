"""When each quarter's results first became public.

release_utc = the earliest of (a) the first 8-K carrying Item 2.02 'Results of Operations' accepted after the
quarter ended (within 100 days) and (b) the acceptance of the 10-Q/10-K that first reported the quarter's
revenue. Forecast origins are placed one second before release_utc, so a forecast never sees the quarter it
predicts.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import fundamentals as fu, store
from .universe import target_tickers


def build() -> pd.DataFrame:
    v = store.load("fundamentals_vintages")
    filings = store.load("sec_filings")
    fr = fu.first_reported(v)
    rev = fr[(fr["item"] == "revenue") & (fr["derivation"] != "annual") & fr["ticker"].isin(target_tickers())]
    k8 = filings[(filings["form"].isin(["8-K", "8-K/A"])) & filings["items"].fillna("").str.contains("2.02")]
    rows = []
    for tk, g in rev.groupby("ticker"):
        e8 = np.sort(store.ns(k8[k8["ticker"] == tk]["acceptance_utc"].dropna()))
        for r in g.itertuples(index=False):
            pe = pd.Timestamp(r.period_end).tz_localize("UTC")
            lo, hi = (pe + pd.Timedelta(hours=12)).value, (pe + pd.Timedelta(days=100)).value
            i = int(np.searchsorted(e8, lo))
            first8 = pd.Timestamp(int(e8[i]), tz="UTC") if i < len(e8) and e8[i] <= hi else pd.NaT
            filed = pd.Timestamp(r.avail_utc)
            rel = min([t for t in (first8, filed) if pd.notna(t)])
            rows.append({"ticker": tk, "period_end": r.period_end, "period_start": r.period_start,
                         "release_utc": rel, "release_source": "8-K 2.02" if pd.notna(first8) and rel == first8 else r.form,
                         "filing_utc": filed, "lag_days": (rel - pe).days})
    out = pd.DataFrame(rows).sort_values(["ticker", "period_end"]).reset_index(drop=True)
    store.save("releases", out)
    print(f"  releases: {len(out)} quarters; via 8-K {np.mean(out['release_source'] == '8-K 2.02'):.0%}; "
          f"median lag {out['lag_days'].median():.0f} days", flush=True)
    return out
