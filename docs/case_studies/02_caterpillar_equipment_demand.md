# Case study 2: Caterpillar equipment demand

**Question.** Do public construction, mining and energy indicators tell you Caterpillar's sales before it reports?
Cummins (engines, power systems) and United Rentals (equipment rental) are modelled alongside as machinery peers.

## The business the indicators have to explain

From Caterpillar's 10-K (quotes verified in [COMPANY_MAPPING.md](../COMPANY_MAPPING.md)):

* Three primary segments: **Construction Industries**, **Resource Industries** (mining) and **Power & Energy**
  (renamed from Energy & Transportation in 2025: engines, turbines, locomotives, power generation including data
  center applications), plus Financial Products.
* It sells "primarily through an independent dealer network", 41 dealers in the US and 109 outside, serving 190
  countries, and is "subject to risks relating to their inventory management decisions". Reported sales are
  shipments to dealers, not dealers' sales to end users.
* Backlog believed firm was about $51.2 billion at the end of 2025, against $30.0 billion a year earlier, with the
  largest increase in Power & Energy.
* Price realization was unfavourable in 2025, and higher tariffs raised manufacturing costs; operating margin fell
  to 16.5% from 20.2%.

## Indicators and channels

| Segment | Indicator (source) | Expected timing |
|---|---|---|
| Construction | US construction-machinery orders (Census M3, ALFRED vintages); total and highway construction spending; housing starts; US exports of earthmoving machinery (Comtrade, to Nov 2023) | coincident to 2 quarters |
| Resource | US mining and oilfield machinery orders; mining output; copper price | 0–4 quarters |
| Power & Energy | Oil and gas drilling output; planned engine, fuel-cell and gas-turbine capacity (EIA-860M, monthly vintages); hyperscaler capex | 0–4 quarters |
| Pricing and cost | Construction-machinery PPI; iron and steel PPI | coincident to 3 quarters |

## What the data say

**The pre-specified composite does not help.** For the three machinery companies the indicator model is slightly
*worse* than the company's own history at the nowcast horizon (error ratio 1.05 in 2016–20 and 1.03 in the
2021–26 holdout), and no better one or two quarters ahead. For Caterpillar alone: 6.62 pp against 6.46 pp
(ratio 1.03, 43 quarters).

**One indicator does carry information, and it leads.** Tested one at a time, US construction-machinery orders
improve Caterpillar's forecasts out of sample at every horizon: R² +0.17 at the nowcast, +0.24 one quarter earlier,
+0.17 two quarters earlier (Clark-West p = 0.010, 0.001, 0.007; 33–35 quarters). The descriptive lag profile agrees:
the correlation of growth rates peaks with the indicator one quarter ahead (0.59), and so does the correlation of
changes in growth (0.33). Highway construction spending helps a little (R² +0.07 and +0.09 at the first two
horizons). Housing starts (−0.37), planned engine capacity (−0.26) and planned gas turbines (−0.27) hurt.
Averaging all twelve links let the noisy ones drown the useful one.

**Caution: this is found, not confirmed.** About 135 single-indicator tests were run (45 links × 3 horizons). At a
Bonferroni threshold of 0.05/135 ≈ 0.0004, even the best result (p = 0.001) does not pass. The honest status is a
hypothesis worth a pre-registered test on the next earnings seasons: *US construction-machinery orders lead
Caterpillar's reported sales growth by about a quarter.*

**Why the composite fails here.** The structure of the business explains most of it:

1. **Dealer inventory.** Caterpillar's sales follow dealers' stocking decisions as well as end demand, so a quarter
   can grow or shrink with no change in construction activity. 21 of its 33 results releases since 2019 attribute
   part of the change in sales to dealer inventories; in the third quarter of 2026, "Higher sales volume was
   primarily driven by the impact from changes in dealer inventories." US industry orders track what dealers order,
   which may be why that one series works when activity measures do not.
2. **Geography.** With 109 of 150 dealers outside the US, US construction, housing and mining series cover a
   minority of the demand.
3. **Mix shift.** Power & Energy's growth now comes from data center power generation and oil and gas, which US
   construction indicators do not see.

## Could it inform an investment decision?

Not as built: for Caterpillar the platform's standard model adds nothing to the company's history. The single
leading series is the useful output, and it is a hypothesis, not a tested signal. A decision-grade version would
need the dealer-inventory effect as a dated series (the releases describe it each quarter; turning those sentences
into numbers from the 8-K exhibits the pipeline already stores is the obvious next step) and non-US activity
indicators.
