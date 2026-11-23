# Methodology

The question is narrow on purpose: **does public, physical-world activity tell you a company's next reported
revenue (and margins) before the company reports it, better than the company's own history does?** Every
design choice below serves that test, and in particular serves keeping it honest about time.

## 1. Point-in-time data

Every stored value carries the moment it became public (`available_at`, UTC). Revisions are new rows, never
overwrites. A forecast made at time τ may only use rows with `available_at < τ`
([`indintel/store.py`](../indintel/store.py)). How `available_at` is set depends on the source, from strongest
to weakest:

| Kind | Meaning | Sources |
|---|---|---|
| vintage | the series as it was published on each date | ALFRED (every vintage since mid-2011, value public from the end of its release day); EIA-860M (one archived file per month, public 30 days after the reference month) |
| filing | acceptance time of the SEC filing that first contained the value | company financials, segment revenue, customers' capex |
| event | the record carries its own public date | interconnection-queue entries, Texas registrations, EU award notices |
| lagged | only today's revised history exists; public `period end + documented lag` | Census data center construction (35 days), EIA-930 load (10), EIA-861M sales (60), Comtrade (75), Indeed (7) |

The *lagged* series are the weak point: their revisions are not point in time. The sensitivity analysis runs
the models on today's revised data with no lag at all to measure how much a careless backtest would flatter
the result (section 6).

### Company financials

[`indintel/fundamentals.py`](../indintel/fundamentals.py) replays every 10-Q and 10-K (XBRL company facts) in
acceptance order. After each filing it derives discrete quarters (direct three-month values, or year-to-date
differences, Q4 = full year − nine months, only within one filing vintage so restated and original bases never
mix). The first vintage of a quarter is what the market saw; later vintages are restatements. Segment revenue
comes the same way from the dimensional facts in 879 XBRL instance documents
([`indintel/segments.py`](../indintel/segments.py)).

### Release times and forecast origins

[`indintel/releases.py`](../indintel/releases.py) dates each quarter's first public report: the 8-K Item 2.02
earnings release when there is one (87% of quarters), else the 10-Q/10-K. The **origin** of a forecast is one
second before that release:

* **h = 0, nowcast:** the quarter has ended and is not yet reported. This is the platform's main use: estimate
  results before the company reports them.
* **h = 1, h = 2:** one second before the release of the quarter one or two quarters earlier.

### Targets and features

Target: year-over-year log growth of first-reported revenue, against the year-ago quarter as stated in the
same filing (so restatements of the base do not create fake growth). Margin targets: change in gross and
operating margin versus the year-ago quarter, in percentage points.

Features ([`indintel/panel.py`](../indintel/panel.py), [`indintel/indicators.py`](../indintel/indicators.py)),
all as of the origin:

* last reported revenue growth (and the quarter before it), last margin change, remaining performance
  obligations (RPO) and their growth
* for each indicator, year-over-year growth over the target quarter's own months. Months not yet published
  at the origin are dropped from *both* years (the "ragged edge"), so growth compares like with like; if no
  month of the quarter is out yet, the latest three published months are used and the staleness is recorded.
  Sparse event series use trailing-twelve-month sums. Customer-capex panels count only members that have
  reported both the quarter and the year-ago quarter, so the total is not distorted by who has reported so far.

A brute-force test recomputes sampled panel features directly from the observation table filtered to
`available_at < origin` and requires an exact match.

## 2. The mapping layer

[`config/companies.yaml`](../config/companies.yaml) links each company to the indicators that should drive it,
with a role (demand, pricing, cost), the segment the channel works through, the expected timing and the
channel in one sentence. The links were written **before any model was fitted**, from each company's own
description of its business ([`config/profiles/`](../config/profiles), quotes verified against the filings).
Models use the links as given, so the results are not the product of searching 59 indicators for the ones
that happened to fit. See [COMPANY_MAPPING.md](COMPANY_MAPPING.md).

## 3. Models

All in [`indintel/models.py`](../indintel/models.py).

| Model | Form | Role |
|---|---|---|
| Random walk (RW) | ŷ = last reported growth | baseline |
| History only (AR) | ŷ = a + b · last growth, pooled | the baseline every indicator model must beat |
| Indicator composite | ŷ = a + b · last growth + c · z, where z averages the company's standardised indicator growth rates | regression |
| Partially pooled (Bayes) | as above with a company slope cᵢ ~ N(c, τ²), empirical-Bayes normal-normal; predictive intervals from shrunk residual variance plus slope uncertainty | Bayesian |
| Ridge | each mapped indicator separately, ridge penalty | regression |
| Gradient boosting | trees on last growth, acceleration, RPO growth and every mapped indicator | machine learning |
| Backlog growth | ŷ = a + b · last growth + c · RPO growth | backlog |
| Backlog conversion | revenue = κᵢ · RPO at the last quarter end, log κᵢ partially pooled across companies | backlog, Bayesian |
| Price-cost (margins) | Δmargin = a + b · last Δmargin + c · pricing composite + d · cost composite + e · last growth | margins |

Standardisation constants for indicators are computed on each training window only. Nothing is tuned on the
holdout; the ridge penalty, tree settings and pooling rules are fixed defaults.

## 4. Walk-forward protocol

[`indintel/backtest.py`](../indintel/backtest.py). Models are refit once per calendar quarter of origins, on
panel rows whose *target* was public before the earliest origin in that quarter. An assertion on the audit
log checks that no training label was released after its forecast's origin
([`out/pit_audit_walkforward.csv`](../out/pit_audit_walkforward.csv)). Evaluation covers targets ending from
2016; **2016-2020 is the development period and 2021 onward the holdout**, run after the specification was
fixed.

## 5. Evaluation

* Mean absolute error and RMSE of growth, by model, horizon, period, case study and company.
* **Diebold-Mariano** (two-sided) against the random walk and the history-only model, and **Clark-West**
  (one-sided, for nested models) for whether indicators add to history. Forecasts made in the same quarter
  share shocks, so loss differentials are averaged across companies within each origin quarter and the
  quarterly series gets Newey-West standard errors.
* Coverage of the 80% predictive intervals.
* **Lead or correlation?** Three checks per mapped indicator: correlations of growth rates at leads −4..+4
  quarters (raw and in first differences, because two trending series correlate at every lag); the
  out-of-sample gain of adding the indicator to the history-only model at h = 0, 1, 2 (an indicator that
  leads should still help one or two quarters ahead); and whether turning points in the indicator composite
  precede turning points in revenue growth.
* **Sensitivity:** look-ahead data (today's revised values, no publication lag), the indicator one quarter
  earlier, a rolling 20-quarter window instead of an expanding one, excluding 2020, and only quarters where
  the indicators were fully published.

## 6. Predicting fundamentals is not predicting returns

[`indintel/returns.py`](../indintel/returns.py) asks the second question separately, with licensed CRSP daily
returns through December 2024 (not redistributed). The signal is what the indicators add to the
history-only forecast. Tests: announcement returns (release day and the next) on the nowcast signal; drift
from the previous release to this one on the signal available a quarter earlier; and, as a check that the
setup can detect anything, announcement returns on the realised revenue surprise. Slopes are estimated per
quarter and averaged (Fama-MacBeth).

## 7. What the platform does not do

* No analyst consensus: there is no licensed consensus history in the pipeline, so "surprise" always means
  surprise against a statistical baseline, not against the market's expectation.
* No satellite data (Earthdata and Copernicus need accounts), no ERCOT or PJM files (robots.txt and API
  keys), and partial UN Comtrade coverage (anonymous quota). See [DATA_SOURCES.md](DATA_SOURCES.md).
* Foreign issuers without quarterly SEC XBRL (ABB annual only, Siemens Energy and Schneider Electric not at
  all) are mapped but not modelled.
