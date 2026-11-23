# Case study 4: Industrial order backlogs and conversion

**Question.** Industrial companies carry record backlogs. Does the backlog a company reports (its remaining
performance obligations, RPO) tell you next quarter's revenue, and do public indicators tell you its order intake?

## What "backlog" means in the filings

Since ASC 606, US filers disclose **remaining performance obligations**: contracted revenue not yet recognised. Ten
of the modelled companies tag it in XBRL in at least six quarters (CAT, CMI, ETN, GEV, KLAC, LRCX, MTZ, POWL,
PWR, ROK). The measure is not comparable across companies: the standard lets a company leave out contracts of a
year or less, so RPO covers anything from a sliver of revenue to several years of it. Vertiv, the purest data center
supplier, reports its backlog only in text ($15.0 billion at the end of 2025), not in XBRL.

The **conversion ratio** below is quarterly revenue divided by RPO at the previous quarter end (2019–2026):

| Company | Quarters | Median ratio | Variation (CV) | Trend in log ratio per year | Median RPO growth (YoY) |
|---|---:|---:|---:|---:|---:|
| Caterpillar | 23 | 1.29 | 0.39 | −18% | 25% |
| Cummins | 28 | 6.91 | 0.57 | −23% | 12% |
| Eaton | 11 | 0.48 | 0.14 | −16% | 28% |
| GE Vernova | 6 | 0.07 | 0.09 | −9% | 19% |
| KLA | 24 | 0.32 | 0.49 | −18% | 18% |
| Lam Research | 28 | 2.58 | 0.44 | −13% | 16% |
| MasTec | 30 | 0.35 | 0.20 | −1% | 18% |
| Powell | 27 | 0.21 | 0.23 | −7% | 17% |
| Quanta | 30 | 0.50 | 0.28 | −10% | 24% |
| Rockwell | 25 | 1.97 | 0.27 | −13% | 14% |

Two facts stand out. Cummins books quarterly revenue of almost seven times its RPO, GE Vernova 7% of it: RPO
measures very different things. And **every** ratio drifted down: backlogs grew faster than revenue at all ten
companies, so a backlog dollar converts more slowly today than in 2019.

## What the data say

On the 232 company-quarters where RPO is known (2019–2026), nowcasting revenue growth:

| Model | Error (pp) | Ratio to history only |
|---|---:|---:|
| Indicators, partially pooled | 7.94 | 0.99 (Clark-West p = 0.005) |
| History only | 8.02 | 1.00 |
| Backlog growth added to history (pre-specified) | 8.35 | 1.04 |
| Revenue = pooled conversion ratio × RPO (pre-specified) | 34.2 | 4.26 |
| Revenue = last observed ratio × RPO (added after the first run) | 12.0 | 1.50 |
| Revenue grows as fast as RPO did (added after the first run) | 26.5 | 3.30 |

The pre-specified conversion model failed in the development period already (1.47 times the history-only error over
all companies), so two simpler rules were added afterwards and are labelled as such. All backlog rules lose to the
company's own revenue history. The drift in the table above is the reason: any rule that assumes a stable
conversion ratio is biased in a period when backlogs build.

**Order intake.** Implied orders (change in RPO plus revenue, trailing four quarters) are a different matter. The
mapped indicator composite nowcasts their growth better than history: error 9.67 pp against 9.95 pp (ratio 0.97,
Clark-West p = 0.0003, Diebold-Mariano p = 0.59; 150 forecasts over 20 quarters, all after 2021 because RPO history
starts in 2019). Factory orders and customers' capex move with order intake, not with the revenue that converts
from older backlog.

## Could it inform an investment decision?

* **Revenue:** a backlog number alone is a poor guide to next quarter's revenue in a backlog-building cycle. Use the
  company's own disclosure of how much RPO converts within twelve months (for example, GE Vernova states 37% of
  equipment RPO within one year and 17% of services RPO). Some companies tag that timing in XBRL; modelling it is
  the obvious next step.
* **Orders:** public demand indicators do track order intake, with a small, statistically detectable gain. For
  backlog-driven stocks, where order growth matters more to valuation than this quarter's revenue, that is the more
  useful signal, but 20 quarters of RPO history is too short to call it robust.
