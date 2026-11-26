# Data sources

Sources were probed before any code was written and ranked by **availability** (can a script get the full
history without accounts or workarounds?), **reliability** (official statistics or filings, with known
revision behaviour?) and **economic relevance** (is there a direct channel to the companies' revenue,
orders or margins?). Every connector honours robots.txt through one shared client
([`indintel/http.py`](../indintel/http.py)) that also caches every response and logs its SHA-256, so each
number can be traced to the bytes it came from. Nothing was scraped against a site's terms and no data was
invented: where a source refused scripted access, it is listed as not used.

## Ranking

| Rank | Source | Why it ranks here |
|---|---|---|
| 1 | SEC EDGAR | Required for the targets themselves; exact acceptance timestamps make it the cleanest point-in-time source; customers' capex (hyperscalers, utilities) from the same filings is a direct demand channel |
| 2 | ALFRED (Census M3, construction spending, PPI, industrial production) | Official statistics with every published vintage: the only macro source here that is fully point in time |
| 3 | EIA-860M generator inventory | Monthly archived snapshots since 2015 of every planned and operating power plant: point in time by construction, and a direct channel to turbines, engines, fuel cells and grid equipment |
| 4 | EIA-930 / EIA-861M electricity | Near-real-time load where data centers cluster; revised history only, so lagged |
| 5 | Interconnection queues (MISO, CAISO, NYISO) | Long-lead developer demand with withdrawn requests kept (no survivorship) |
| 6 | Census data center construction | The most direct data center series, but revised history only |
| 7 | Texas TABS registrations, EU TED awards | Dated public pipelines (permits, tenders), short histories |
| 8 | Indeed postings, UN Comtrade | Hiring and trade; short or incomplete histories |
| 9 | USAspending | Federal awards: dated and reliable, but federal buying is a small share of these companies' sales, so it is timeline context only |

## Registry

The full registry (status, point-in-time treatment, content, terms) is generated from
[`config/sources.yaml`](../config/sources.yaml):

- [SOURCES_TABLE.md](SOURCES_TABLE.md): every source, including those not used and why
- [INDICATORS.md](INDICATORS.md): the 59 indicator series with their coverage

## Access notes

* **SEC.** `www.sec.gov` refuses anonymous scripts. The pipeline reads a declared contact from the
  `SEC_USER_AGENT` environment variable at run time (SEC fair-access policy) and never writes it anywhere.
  `data.sec.gov` APIs answer without it. Requests stay under 7 per second.
* **ALFRED** returns at most 12 vintages per request and silently drops the rest; the connector batches by 12
  and fails loudly if a response has fewer columns than requested.
* **UN Comtrade.** The anonymous preview API allows one period per call and has a quota. After about 110 calls
  it answered HTTP 403; the pipeline stops rather than working around it, so US series end in November 2023
  and the Japan and Korea series are empty. The cache lets a later run resume where it stopped.
* **BLS API and ERCOT:** robots.txt disallows them; the same PPI series come from ALFRED. **Berkeley Lab
  queue data:** bot challenge. **PJM:** API key. **SPP:** active requests only (survivorship), so not used.
* **Satellite data** (NASA Black Marble night lights, Sentinel-5P): free but behind Earthdata or Copernicus
  accounts, which this project does not have. The connector layer is ready for a gridded monthly series with
  the same `available_at` contract.
* **Earnings-call transcripts** are not redistributable; company statements come from 8-K press releases and
  annual reports filed with the SEC.
* **8-K press releases** (dashboard timeline only, no model uses them): the press-release exhibit is found under
  any of the filers' naming styles (`ex99-1`, `exx991`, `kex99`, `…pressrelease`), and an exhibit without a
  release body (slide decks, credit agreements, court filings) is not treated as one: such 8-Ks are shown with
  their item title and first sentence. Headlines and backlog/order sentences are verbatim. Investor-relations
  contact blocks are never used as text, and phone numbers and e-mail addresses are removed before storing.
* **CRSP** (licensed through WRDS) is read from a local path for the excess-return tests only; no CRSP data is
  in the repository or on the dashboard.

## Refreshing

```
python -m indintel fetch all          # every connector (cached; API data refresh daily or weekly)
python scripts/run_all.py             # rebuild datasets, backtests, docs bundle from the cache
```
