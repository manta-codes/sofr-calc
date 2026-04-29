# Term SOFR Approximation — Methodology

## What Term SOFR Is

Term SOFR is a **forward-looking** interest rate — it tells you what the market expects overnight SOFR to average over the next 1, 3, 6, or 12 months. The official version is published daily by CME Group using intraday trade data. This notebook produces an approximation using publicly available end-of-day prices.

---

## Step 1 — Fetch Futures Prices

The notebook pulls settlement prices for **CME SR1 futures** (1-month SOFR contracts) from Yahoo Finance. Each SR1 contract covers a single calendar month and is priced as:

> **Price = 100 − Expected Average SOFR Rate for that month**

So a price of **95.64** implies an expected rate of **4.36%** for that month.

The notebook fetches the next 14 monthly contracts, giving a forward view of roughly 14 months.

---

## Step 2 — Build a Daily Forward Rate Curve

Each futures contract's implied rate is spread across every calendar day in that contract's month. The result is a day-by-day schedule of expected overnight SOFR rates — for example:

| Date | Implied Rate |
|---|---|
| May 1 | 4.36% |
| May 2 | 4.36% |
| … | … |
| Jun 1 | 4.28% |
| Jun 2 | 4.28% |

If the available contract data doesn't extend far enough to cover the 12-month tenor, the last known rate is carried forward flat.

---

## Step 3 — Compound the Rates

To get a Term SOFR rate for a given tenor (e.g. 3-Month = 90 days), the notebook compounds the daily implied rates over that number of days using the **Act/360** day-count convention — the same method used for actual SOFR:

> Compound each day: multiply (1 + daily rate × 1/360) together for 90 days, subtract 1, then annualize back to a percentage.

This mirrors how a floating-rate loan accrues interest day by day.

---

## Step 4 — Output

The four compounded results are labeled:

| Tenor | Days Compounded |
|---|---|
| 1 Month | 30 |
| 3 Month | 90 |
| 6 Month | 180 |
| 12 Month | 360 |

These are written to the Domo dataset alongside official NY Fed overnight SOFR and 30/90/180-day backward-looking averages (sourced from FRED) for comparison.

---

## Why It's an Approximation

| Factor | Official CME Term SOFR | This Notebook |
|---|---|---|
| Price source | Intraday VWAP | End-of-day settlement |
| Timing | Published at market close | Calculated anytime |
| Day count | Exact business-day calendar | Simple calendar days |
| Accuracy | Exact | ~0.5–3 bps off |

The approximation is suitable for budgeting, underwriting, and internal modelling — but should not be used as a contractual rate reference.
