# Case study 3: Electrical equipment and grid investment

**Question.** Utilities, data centers and factories are buying record amounts of grid equipment. Do the public
traces of that spending (utility capital expenditure, factory orders, equipment prices, power-plant pipelines,
interconnection requests, European tenders) estimate revenue and margins of the equipment makers and grid
contractors before they report?

**Companies.** Eaton (ETN), Hubbell (HUBB), GE Vernova (GEV), Quanta Services (PWR), MasTec (MTZ), Powell (POWL),
nVent (NVT); ABB, Siemens Energy and Schneider Electric are mapped but not modelled (no quarterly SEC XBRL).

## Channels

| Channel | Indicator (source, point-in-time treatment) | Companies |
|---|---|---|
| Utility budgets | Capital expenditure of 22 investor-owned utilities (SEC filings by acceptance time; members matched year over year) | ETN, HUBB, GEV, PWR, MTZ, POWL, NVT |
| Factory demand | Electrical-equipment new orders and backlog (Census M3, ALFRED vintages) | ETN, HUBB, GEV, POWL, NVT |
| Prices | PPI for transformers and switchgear (ALFRED vintages); copper, iron and steel as costs | ETN, HUBB, GEV, POWL, NVT |
| Generation pipeline | Planned battery, solar, wind and gas capacity due within 24 months (EIA-860M monthly vintages); new interconnection requests (MISO, CAISO, NYISO, by entry date, withdrawn requests included) | PWR, MTZ, GEV |
| Construction | Power and nonresidential construction spending (ALFRED); data center construction (Census, lagged) | PWR, MTZ, ETN, HUBB |
| Europe | Contract awards for transformers, switchgear and power cable in the EU journal (TED, by publication date) | ABB, Siemens Energy, Schneider (mapped only) |

## What the data say

**Revenue nowcasts, seven companies.**

| Period | Forecasts | Random walk | History only | Indicators (Bayes) | Ratio to history | Clark-West p | Diebold-Mariano p |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2016–20 | 99 | 7.19 | 6.57 | 6.57 | 0.999 | 0.41 | 0.86 |
| 2021–26 holdout | 148 | 6.88 | 6.58 | 6.40 | 0.972 | 0.004 | 0.09 |

* A **3% holdout gain**, detectable (Clark-West p = 0.004), short of Diebold-Mariano significance (p = 0.09), and
  nothing in 2016–20. One and two quarters before the release the gain disappears (ratio 1.00 and 1.04).
* By company (all periods): GE Vernova 0.88 (ten quarters), Powell 0.95, nVent 0.97, Eaton 0.99, MasTec 1.00,
  Hubbell 1.00, Quanta 1.01.
* **Utility capex is not a quarterly revenue signal.** Pooled over the seven companies it is mapped to, it does not
  improve forecasts (R² −0.02, p = 0.55); its correlation with Hubbell's revenue growth is at most 0.17 at any lead,
  and with Quanta's it is 0.03 in the same quarter. Utility budgets are multi-year plans that move smoothly; contractor and
  supplier revenue moves with project timing. **Electrical-equipment orders** do not help either at the company
  level (R² −0.01), although they line up with Powell's growth in the same quarter (correlation 0.61). The data
  center channel (hyperscaler capex) and the battery pipeline for Quanta (R² +0.07, p = 0.03) carry most of what
  there is.

**Margins.** The price-cost model (pricing composite from the transformer and switchgear PPIs, cost composite from
copper and steel, plus margin momentum) does **not** beat a simple mean-reverting model of margin changes, across
all 17 companies: gross-margin change error 1.99 pp against 1.93 pp (ratio 1.03), operating margin 1.06. The
mean-reverting model itself beats the random walk by 11%. Industry prices are visible monthly, but how much of a
price increase reaches a given company's margin, and when, depends on its backlog: Vertiv's filing notes that
"due to our large backlog, pricing changes may take longer to be reflected in our financial results".

## Could it inform an investment decision?

For **revenue**, the same answer as data centers: a small, honest improvement in the nowcast for the weeks before a
release, concentrated in the companies most exposed to data center electrification (Powell, nVent, GE Vernova),
and no lead. For **margins**, no: the public price indexes did not improve on margin momentum. The supportable
investment use is narrower: the platform documents that the 2021–26 growth in these companies coincided with
measurable physical demand (orders, pipelines, prices), which is evidence about the *quality* of reported growth,
not a forecast edge.

**What would improve it:** transformer and switchgear order data by voltage class (not public), utility rate-case
capital plans by project (public but scattered across state commissions), and PJM and ERCOT large-load
interconnection data (terms or keys needed).
