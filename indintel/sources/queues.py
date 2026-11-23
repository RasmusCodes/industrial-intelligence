"""Generator interconnection queues of three US grid operators: MISO, CAISO and NYISO.

Each operator publishes its whole queue, including withdrawn and completed requests, with the date each
request entered the queue. New requests per month (MW by fuel) can therefore be rebuilt from today's files
without survivorship: a request that was later withdrawn still counts in the month it was filed.
'event' series: available 30 days after the month of entry (queue reports are refreshed monthly).

Not used: SPP's public export lists active requests only (survivorship), ERCOT's report pages are disallowed
by robots.txt, and PJM's queue export sits behind an API that needs a registered key.
"""
from __future__ import annotations

import io

import pandas as pd

from .. import store
from ..http import Fetcher
from ..indicators_config import series_for_source

MISO = "https://www.misoenergy.org/api/giqueue/getprojects"
CAISO = "https://www.caiso.com/PublishedDocuments/PublicQueueReport.xlsx"
NYISO = "https://www.nyiso.com/documents/20142/1407078/NYISO-Interconnection-Queue.xlsx"
LAG_DAYS = 30


def fuel_class(text: str) -> str:
    t = str(text or "").strip().lower()
    if not t or t == "nan":
        return "unknown"
    if t in ("es",) or "batter" in t or "storage" in t:
        return "storage"
    if t in ("ng", "ct", "cc", "cs", "d", "dfo", "kt", "gt") or "gas" in t or "combined cycle" in t \
            or "combustion turbine" in t:
        return "gas"
    if t in ("s", "pv") or "solar" in t or "photovoltaic" in t:
        return "solar"
    if t in ("w", "osw", "wt") or "wind" in t:
        return "wind"
    if "hybrid" in t:
        return "hybrid"
    if t in ("l",) or "load" in t:
        return "load"
    return "other"


def miso(f: Fetcher) -> pd.DataFrame:
    js = f.get_json(MISO, "queues", max_age_hours=24 * 7)
    df = pd.DataFrame(js)
    return pd.DataFrame({
        "iso": "MISO", "queue_id": df["projectNumber"], "queue_date": pd.to_datetime(df["queueDate"], utc=True, errors="coerce"),
        "status": df["applicationStatus"], "fuel_raw": df["fuelType"], "mw": pd.to_numeric(df["summerNetMW"], errors="coerce"),
        "state": df["state"], "name": df["poiName"]})


def _sheet(data: bytes, name: str, header_keys=("queue",)) -> pd.DataFrame:
    raw = pd.read_excel(io.BytesIO(data), sheet_name=name, header=None, engine="calamine", dtype=object)
    hdr = next(i for i in range(8) if any(str(x).strip().lower().startswith(header_keys) for x in raw.iloc[i].tolist()))
    cols = [str(c).replace("\n", " ").strip() for c in raw.iloc[hdr].tolist()]
    df = raw.iloc[hdr + 1:].copy()
    df.columns = cols
    return df


def caiso(f: Fetcher) -> pd.DataFrame:
    data = f.get(CAISO, "queues", max_age_hours=24 * 7)
    frames = []
    for sheet, status in (("Grid GenerationQueue", "Active"), ("Completed Generation Projects", "Done"),
                          ("Withdrawn Generation Projects", "Withdrawn")):
        df = _sheet(data, sheet, ("project name",))
        mw = pd.to_numeric(df.get("Net MWs to Grid"), errors="coerce")
        mw = mw.fillna(pd.to_numeric(df.get("MW-1"), errors="coerce"))
        frames.append(pd.DataFrame({
            "iso": "CAISO", "queue_id": df["Queue Position"].astype(str), "queue_date": pd.to_datetime(df["Queue Date"], errors="coerce", utc=True),
            "status": status, "fuel_raw": df["Fuel-1"].astype(str), "mw": mw, "state": df.get("State"),
            "name": df[df.columns[0]].astype(str)}))
    return pd.concat(frames, ignore_index=True)


def nyiso(f: Fetcher) -> pd.DataFrame:
    data = f.get(NYISO, "queues", max_age_hours=24 * 7)
    frames = []
    for sheet, status in (("Interconnection Queue", "Active"), (" Cluster Projects", "Active"), ("Withdrawn", "Withdrawn"),
                          ("Cluster Projects-Withdrawn", "Withdrawn"), ("In Service", "Done")):
        raw = pd.read_excel(io.BytesIO(data), sheet_name=sheet, header=None, engine="calamine", dtype=object)
        if sheet == "In Service":       # two-row header
            cols = ["queue_pos", "developer", "project", "date_ir", "sp_mw", "wp_mw", "fuel", "county", "state"]
            df = raw.iloc[2:, :9].copy()
            df.columns = cols
        else:
            hdr = next(i for i in range(5) if str(raw.iloc[i, 0]).strip().lower().startswith("queue"))
            df = raw.iloc[hdr + 1:].copy()
            df.columns = [str(c).strip() for c in raw.iloc[hdr].tolist()]
            df = df.rename(columns={"Queue Pos.": "queue_pos", "Date of IR": "date_ir", "SP (MW)": "sp_mw",
                                    "Type/ Fuel": "fuel", "Project Name": "project", "State": "state"})
        frames.append(pd.DataFrame({
            "iso": "NYISO", "queue_id": df["queue_pos"].astype(str), "queue_date": pd.to_datetime(df["date_ir"], errors="coerce", utc=True),
            "status": status, "fuel_raw": df["fuel"].astype(str), "mw": pd.to_numeric(df["sp_mw"], errors="coerce"),
            "state": df.get("state"), "name": df["project"].astype(str)}))
    return pd.concat(frames, ignore_index=True)


def run(f: Fetcher) -> pd.DataFrame:
    q = pd.concat([miso(f), caiso(f), nyiso(f)], ignore_index=True)
    q = q.dropna(subset=["queue_date"])
    q = q[q["queue_id"].astype(str).str.lower().ne("nan")]
    q = q.drop_duplicates(["iso", "queue_id"], keep="first")
    q["fuel"] = q["fuel_raw"].map(fuel_class)
    q["month"] = q["queue_date"].dt.tz_convert(None).dt.to_period("M").dt.to_timestamp()
    store.save("queue_projects", q)
    print(q.groupby(["iso", "status"]).size().to_string(), flush=True)
    frames = []
    for spec in series_for_source("queues"):
        fuel = spec["params"]["fuel"]
        sub = q if fuel == "all" else q[q["fuel"] == fuel]
        sub = sub[sub["fuel"] != "load"]
        m = sub.groupby("month")["mw"].sum()
        full = pd.date_range("2010-01-01", q["month"].max(), freq="MS")
        m = m.reindex(full, fill_value=0.0)
        s = pd.DataFrame({"period": m.index, "value": m.values})
        s["series_id"], s["freq"] = spec["id"], "M"
        s["available_at"] = (s["period"] + pd.offsets.MonthEnd(0) + pd.Timedelta(days=LAG_DAYS)).dt.tz_localize("UTC")
        s["vintage_kind"], s["source"] = "event", "queues"
        frames.append(s[store.OBS_COLUMNS])
    out = store.check_obs(pd.concat(frames, ignore_index=True))
    store.save("obs_queues", out)
    return out
