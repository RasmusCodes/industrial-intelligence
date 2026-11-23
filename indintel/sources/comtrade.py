"""UN Comtrade monthly merchandise trade by HS code (public preview API, no key).

One request per reporter and month returns every requested HS code and flow (partner = World). Only the
current database is used (reporters revise), so series are 'lagged' (75 days after month end, longer than
the US Census FT-900 release lag of about 35 days, to allow for Comtrade ingestion).
"""
from __future__ import annotations

from collections import defaultdict

import pandas as pd

from .. import store
from ..http import Fetcher, FetchError
from ..indicators_config import series_for_source

URL = ("https://comtradeapi.un.org/public/v1/preview/C/M/HS?reporterCode={rep}&period={per}&partnerCode=0"
       "&cmdCode={cmds}&flowCode={flows}")
START = "2015-01-01"


def run(f: Fetcher) -> pd.DataFrame:
    specs = series_for_source("comtrade")
    want = defaultdict(lambda: (set(), set()))
    for s in specs:
        p = s["params"]
        want[int(p["reporter"])][0].add(str(p["cmd"]))
        want[int(p["reporter"])][1].add(p["flow"])
    today = pd.Timestamp.today()
    months = pd.date_range(START, today - pd.DateOffset(months=2), freq="MS")
    rows = []
    for rep, (cmds, flows) in want.items():
        n_ok = 0
        for m in months:
            per = m.strftime("%Y%m")
            url = URL.format(rep=rep, per=per, cmds=",".join(sorted(cmds)), flows=",".join(sorted(flows)))
            recent = m >= today - pd.DateOffset(months=8)
            try:
                js = f.get_json(url, "comtrade", max_age_hours=24 * 14 if recent else None)
            except FetchError as e:
                print(f"  comtrade {rep} {per}: HTTP {e.status}", flush=True)
                continue
            for d in (js or {}).get("data", []):
                if d.get("partner2Code") not in (0, None) or d.get("customsCode") not in ("C00", None) \
                        or d.get("motCode") not in (0, None):
                    continue
                rows.append({"reporter": rep, "month": m, "cmd": str(d["cmdCode"]), "flow": d["flowCode"],
                             "value_usd": d.get("primaryValue"), "net_wgt": d.get("netWgt"), "qty": d.get("qty")})
                n_ok += 1
        print(f"  comtrade reporter {rep}: {n_ok} rows over {len(months)} months", flush=True)
    df = pd.DataFrame(rows)
    store.save("comtrade", df)
    frames = []
    for spec in specs:
        p = spec["params"]
        s = df[(df["reporter"] == int(p["reporter"])) & (df["cmd"] == str(p["cmd"])) & (df["flow"] == p["flow"])].copy()
        s["series_id"], s["period"], s["freq"], s["value"] = spec["id"], s["month"], "M", s["value_usd"]
        s["available_at"] = (s["month"] + pd.offsets.MonthEnd(0) + pd.Timedelta(days=spec["pub_lag_days"])).dt.tz_localize("UTC")
        s["vintage_kind"], s["source"] = "lagged", "comtrade"
        frames.append(s[store.OBS_COLUMNS])
    out = store.check_obs(pd.concat(frames, ignore_index=True))
    store.save("obs_comtrade", out)
    return out
