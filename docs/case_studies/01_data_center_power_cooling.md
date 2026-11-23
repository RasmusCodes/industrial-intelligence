# Case study 1: Data center power and cooling demand

**Question.** Can public data on data center construction and its funding estimate the revenue of the companies
that sell data centers their power and cooling equipment, before those companies report?

**Companies.** Vertiv (VRT), Eaton (ETN), Modine/Modexus (MOD), nVent (NVT), Powell (POWL), Bloom Energy (BE) and
GE Vernova (GEV). Evidence for each company's exposure is in [COMPANY_MAPPING.md](../COMPANY_MAPPING.md); for
example Modine now reports a separate Data Center segment, and GE Vernova's filing names "rising electricity demand
from hyperscalers and data centers" as a Power-segment driver.

## From physical activity to the income statement

```
cloud operators' capex ─► data center construction ─► orders for switchgear, UPS, busway, chillers, gensets ─► shipments = revenue
   (SEC filings,           (Census value put in place,     (Census M3 HVAC and electrical orders;                 (company 10-Q)
    quarterly)               Texas registrations)            US imports of switchboards and servers)
                                        └──────────────► power drawn once IT load is installed (EIA-930 Dominion zone, Virginia commercial sales)
```

Each link has its own timing. Capital expenditure is cash already spent; a registration precedes construction by
months; equipment ships while a building is under construction; electricity demand rises only after servers are
installed, so grid load should lag equipment revenue rather than lead it.

| Indicator | Source and point-in-time treatment | Mapped to |
|---|---|---|
| Capex of Microsoft, Alphabet, Amazon, Meta | SEC filings, by acceptance time; only members that have reported both years count | all seven |
| Data center construction put in place | Census C30, revised history only (lagged 35 days) | VRT, ETN, MOD, NVT, POWL, BE |
| HVAC and electrical equipment orders | Census M3 via ALFRED, every vintage | VRT, MOD, ETN, NVT, POWL |
| Dominion-zone load, Virginia commercial electricity sales | EIA-930, EIA-861M (lagged 10 and 60 days) | VRT, BE |
| Texas data center registrations | TDLR TABS, by registration date (277 since Dec 2018) | VRT |
| US imports of servers and switchboards | UN Comtrade (to Nov 2023, quota) | VRT, POWL |

## What the data say

**Nowcast (forecast one second before each release), seven companies.**

| Period | Forecasts | Random walk | History only | Indicators (Bayes) | Ratio to history | Clark-West p | Diebold-Mariano p |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2016–20 | 72 | 8.34 | 8.03 | 7.94 | 0.988 | 0.73 | 0.79 |
| 2021–26 holdout | 148 | 8.88 | 8.42 | 8.24 | 0.978 | 0.001 | 0.10 |

Mean absolute error of year-over-year revenue growth, percentage points (log). The indicator model uses each
company's mapped indicators averaged after standardisation, with company slopes partially pooled.

* **The gain is small and real only in the holdout.** Indicators cut the nowcast error by about 2% against the
  company's own history. The Clark-West test says the indicators carry information the history lacks (p = 0.001),
  but the accuracy gain is too small for the Diebold-Mariano test to call (p = 0.10). In 2016–20, when data center
  spending was a smaller part of these companies, there was no gain.
* **By company** (all periods, ratio to history only): GE Vernova 0.88 (ten quarters only), Powell 0.95, Vertiv 0.96,
  nVent 0.97, Eaton 0.99, Bloom Energy 1.00, Modine 1.00.
* **Which indicators carry it.** Out of sample, one at a time: HVAC equipment orders (R² +0.10 against the
  history-only model, p = 0.04, Vertiv and Modine) and hyperscaler capex (R² +0.02, p = 0.05, pooled over the 12
  companies it is mapped to). Census data center construction, Dominion load and Virginia sales add nothing
  (R² −0.06, −0.03, −0.01).

**Lead or coincident?** In sample, hyperscaler capex growth correlates most with Vertiv's revenue growth two
quarters later (0.57, against 0.36 in the same quarter; 0.60 for changes in growth), which looks like a lead. Out of
sample it is not one: forecasts made one or two quarters before the release gain nothing from the indicators (ratio
to history 1.00 and 1.03). HVAC orders and Dominion load line up with the same quarter (correlation 0.67 and 0.56 at
lag zero). The useful information arrives with the quarter itself.

## Could it inform an investment decision?

As a **nowcast**, modestly: in the weeks between quarter-end and the earnings release, the indicators narrow the
estimate of reported growth a little, and the model's 80% intervals are roughly honest (83% of holdout outcomes
fell inside them across all 17 companies). Vertiv's next-quarter forecast on the dashboard is an example.

As a **trading signal**, there is no evidence. Every input is public, much of it (cloud capex in particular) followed
closely by the market, and the indicators' extra information did not predict earnings-day returns (see
[RESULTS.md](../RESULTS.md), excess-return tests). What the platform does not have is the consensus estimate the
market trades against; with it, the test would be whether the indicator nowcast predicts the *surprise*.

**What would improve it:** the companies' own order and backlog figures (Vertiv's 10-K states a combined order
backlog of about $15.0 billion at the end of 2025, double a year earlier, but the figure is not tagged in XBRL, so
the pipeline does not read it), utility filings on large new loads (ERCOT and PJM publish them, but not under
terms this project could use), and a licensed consensus history.
