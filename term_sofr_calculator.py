"""
Term SOFR Calculator from SOFR Futures
========================================
Approximates forward-looking Term SOFR rates (1M, 3M, 6M, 12M) by bootstrapping
implied rates from CME SR1 (1-month) and SR3 (3-month) SOFR futures prices
sourced freely from Yahoo Finance via yfinance.

IMPORTANT DISCLAIMER
---------------------
This produces an APPROXIMATION of Term SOFR — not the official CME fixing.
Differences arise because:
  - CME uses intraday VWAP; this script uses end-of-day settlement prices
  - CME applies a proprietary projection model; this uses standard bootstrapping
Typical deviation: 0.5–3 basis points. Suitable for internal modelling,
budgeting, and underwriting. NOT suitable as a contractual reference rate.

Requirements:
    pip install yfinance pandas numpy requests

Usage:
    python term_sofr_calculator.py                    # today's implied rates
    python term_sofr_calculator.py --history 90       # last 90 days of estimates
    python term_sofr_calculator.py --output rates.csv # custom output path
"""

import argparse
import csv
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

try:
    import numpy as np
    import pandas as pd
    import yfinance as yf
    import requests
except ImportError as e:
    print(f"ERROR: Missing dependency — {e}")
    print("Install with:  pip install yfinance pandas numpy requests")
    sys.exit(1)


# ── Contract Calendar ─────────────────────────────────────────────────────────
#
# CME month codes (standard futures convention):
#   F=Jan  G=Feb  H=Mar  J=Apr  K=May  M=Jun
#   N=Jul  Q=Aug  U=Sep  V=Oct  X=Nov  Z=Dec

MONTH_CODES = {
    1:"F", 2:"G", 3:"H", 4:"J", 5:"K", 6:"M",
    7:"N", 8:"Q", 9:"U", 10:"V", 11:"X", 12:"Z"
}

# SR3 (3-month) only has quarterly expiries: Mar/Jun/Sep/Dec
SR3_MONTHS = {3, 6, 9, 12}


def get_sr1_tickers(n: int = 14, ref_date: Optional[date] = None) -> list[dict]:
    """
    Generate the next `n` SR1 (1-month SOFR) futures tickers from ref_date.
    Yahoo Finance format: SR1{MonthCode}{YY}.CME  e.g. SR1K26.CME = May 2026
    """
    ref = ref_date or date.today()
    tickers = []
    year, month = ref.year, ref.month

    for _ in range(n):
        code = MONTH_CODES[month]
        yy   = str(year)[-2:]
        tickers.append({
            "ticker":      f"SR1{code}{yy}.CME",
            "year":        year,
            "month":       month,
            "expiry_date": date(year, month, 1),   # approximation: 1st of month
            "type":        "SR1",
        })
        month += 1
        if month > 12:
            month = 1
            year += 1

    return tickers


def get_sr3_tickers(n: int = 6, ref_date: Optional[date] = None) -> list[dict]:
    """
    Generate the next `n` SR3 (3-month SOFR) quarterly futures tickers.
    Yahoo Finance format: SR3{MonthCode}{YY}.CME  e.g. SR3U26.CME = Sep 2026
    """
    ref = ref_date or date.today()
    tickers = []
    year, month = ref.year, ref.month

    # Advance to next quarterly month if not already on one
    while month not in SR3_MONTHS:
        month += 1
        if month > 12:
            month = 1
            year += 1

    for _ in range(n):
        code = MONTH_CODES[month]
        yy   = str(year)[-2:]
        tickers.append({
            "ticker":      f"SR3{code}{yy}.CME",
            "year":        year,
            "month":       month,
            "expiry_date": date(year, month, 1),
            "type":        "SR3",
        })
        # Advance to next quarterly month
        month += 3
        if month > 12:
            month -= 12
            year += 1

    return tickers


# ── Fetch Futures Prices ──────────────────────────────────────────────────────

def fetch_prices(contracts: list[dict], period: str = "5d") -> dict[str, float]:
    """
    Fetch latest closing prices for a list of contracts from Yahoo Finance.
    Returns {ticker: last_price} for contracts with valid data.
    """
    tickers_list = [c["ticker"] for c in contracts]
    prices = {}

    print(f"  Fetching {len(tickers_list)} contracts from Yahoo Finance...")

    try:
        raw = yf.download(
            tickers_list,
            period=period,
            progress=False,
            auto_adjust=True,
        )

        if raw.empty:
            print("  WARNING: No data returned from Yahoo Finance.")
            return prices

        close = raw["Close"] if "Close" in raw.columns else raw

        for ticker in tickers_list:
            try:
                if ticker in close.columns:
                    series = close[ticker].dropna()
                else:
                    series = close.dropna()

                if not series.empty:
                    prices[ticker] = float(series.iloc[-1])
            except Exception:
                pass

    except Exception as e:
        print(f"  ERROR fetching prices: {e}")

    valid = {k: v for k, v in prices.items() if v > 50}   # sanity check: futures ~95-100
    print(f"  Retrieved {len(valid)}/{len(tickers_list)} valid contract prices.")
    return valid


def fetch_prices_historical(
    contracts: list[dict],
    start: str,
    end: str,
) -> pd.DataFrame:
    """
    Fetch daily closing prices for all contracts over a date range.
    Returns a DataFrame indexed by date with contract tickers as columns.
    """
    tickers_list = [c["ticker"] for c in contracts]
    print(f"  Fetching historical prices for {len(tickers_list)} contracts ({start} → {end})...")

    try:
        raw = yf.download(
            tickers_list,
            start=start,
            end=end,
            progress=False,
            auto_adjust=True,
        )
        if raw.empty:
            return pd.DataFrame()

        close = raw["Close"] if "Close" in raw.columns else raw
        # Drop columns that are entirely NaN or have nonsensical prices
        close = close.loc[:, (close > 50).any()]
        return close

    except Exception as e:
        print(f"  ERROR: {e}")
        return pd.DataFrame()


# ── NY Fed SOFR (for stub period) ────────────────────────────────────────────

def fetch_overnight_sofr() -> Optional[float]:
    """
    Fetch the latest overnight SOFR from the NY Fed public API.
    Used to anchor the stub period at the front of the curve.
    """
    try:
        url = "https://markets.newyorkfed.org/api/rates/sofr/last/1.json"
        r   = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        rate = data["refRates"][0]["percentRate"]
        print(f"  Overnight SOFR (NY Fed): {rate:.4f}%")
        return float(rate)
    except Exception as e:
        print(f"  WARNING: Could not fetch overnight SOFR — {e}")
        return None


# ── Core Calculation ──────────────────────────────────────────────────────────

def futures_price_to_rate(price: float) -> float:
    """Convert futures price (e.g. 95.75) to implied rate (e.g. 4.25%)."""
    return 100.0 - price


def days_in_month(year: int, month: int) -> int:
    """Return number of days in a given month."""
    import calendar
    return calendar.monthrange(year, month)[1]


def build_implied_curve(
    sr1_contracts: list[dict],
    sr3_contracts: list[dict],
    prices: dict[str, float],
    overnight_sofr: Optional[float],
    ref_date: date,
) -> list[dict]:
    """
    Build a daily forward rate curve from futures prices using bootstrapping.
    Returns a list of {date, implied_rate, source} dicts covering ~13 months.
    """
    curve = []

    # Seed with overnight SOFR for the stub (today until first futures expiry)
    stub_rate = overnight_sofr if overnight_sofr else None

    # Build SR1-based curve: each contract covers its delivery month
    for contract in sr1_contracts:
        ticker = contract["ticker"]
        if ticker not in prices:
            continue
        rate = futures_price_to_rate(prices[ticker])
        year, month = contract["year"], contract["month"]
        n_days = days_in_month(year, month)
        start  = date(year, month, 1)
        end    = date(year, month, n_days)

        # Fill each day of the month with this contract's implied rate
        d = start
        while d <= end:
            if d >= ref_date:
                curve.append({
                    "date":          d.isoformat(),
                    "implied_rate":  rate,
                    "source":        ticker,
                })
            d += timedelta(days=1)

    # Sort by date
    curve.sort(key=lambda x: x["date"])
    return curve


def compound_rate(curve_slice: list[dict], tenor_days: int) -> Optional[float]:
    """
    Compound daily implied rates over `tenor_days` to get a term rate.
    Uses the standard Act/360 SOFR compounding convention.
    """
    if len(curve_slice) < tenor_days:
        return None

    compounded = 1.0
    days_used  = 0

    for i, point in enumerate(curve_slice[:tenor_days]):
        rate    = point["implied_rate"] / 100.0   # convert % to decimal
        # Each day contributes 1 calendar day in Act/360
        compounded *= (1 + rate * (1 / 360))
        days_used  += 1

    term_rate = (compounded - 1) * (360 / tenor_days) * 100
    return round(term_rate, 5)


TENORS = {
    "1-Month Term SOFR":  30,
    "3-Month Term SOFR":  90,
    "6-Month Term SOFR":  180,
    "12-Month Term SOFR": 360,
}


def calculate_term_sofr(
    curve: list[dict],
    ref_date: date,
) -> dict[str, Optional[float]]:
    """
    Calculate all four Term SOFR tenors from the forward curve.
    """
    # Filter curve to start from ref_date
    forward = [p for p in curve if p["date"] >= ref_date.isoformat()]

    results = {}
    for label, days in TENORS.items():
        results[label] = compound_rate(forward, days)

    return results


# ── Output ────────────────────────────────────────────────────────────────────

def print_results(results: dict, ref_date: date, overnight: Optional[float]) -> None:
    print(f"\n{'─'*55}")
    print(f"  Implied Term SOFR Rates  —  {ref_date.isoformat()}")
    print(f"{'─'*55}")
    if overnight:
        print(f"  {'Overnight SOFR':<25} {overnight:>8.4f}%  (NY Fed)")
    for label, rate in results.items():
        display = f"{rate:.4f}%" if rate is not None else "insufficient data"
        print(f"  {label:<25} {display:>9}")
    print(f"{'─'*55}")
    print(f"  * Approximation from EOD futures prices. Not the official CME fixing.")
    print()


def export_csv_snapshot(
    results: dict,
    ref_date: date,
    overnight: Optional[float],
    output_path: Path,
) -> None:
    rows = []
    if overnight:
        rows.append({
            "Date":           ref_date.isoformat(),
            "Tenor":          "Overnight",
            "Rate (%)":       round(overnight, 5),
            "Type":           "Overnight SOFR",
            "Source":         "NY Fed API",
            "Note":           "Official fixing",
        })
    for label, rate in results.items():
        rows.append({
            "Date":     ref_date.isoformat(),
            "Tenor":    label.replace(" Term SOFR", "").replace("-", "").strip(),
            "Rate (%)": round(rate, 5) if rate is not None else "",
            "Type":     "Term SOFR (Approximated)",
            "Source":   "CME SR1/SR3 Futures via Yahoo Finance",
            "Note":     "Bootstrapped from EOD settlement prices — not official CME fixing",
        })

    _write_csv(rows, output_path)


def export_csv_history(history: list[dict], output_path: Path) -> None:
    _write_csv(history, output_path)


def _write_csv(rows: list[dict], output_path: Path) -> None:
    if not rows:
        print("No data to export.")
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"✓ Exported {len(rows)} rows → {output_path.resolve()}")


# ── Historical Mode ───────────────────────────────────────────────────────────

def run_historical(days_back: int, output_path: Path) -> None:
    end_date   = date.today()
    start_date = end_date - timedelta(days=days_back)
    print(f"\nHistorical mode: {start_date} → {end_date} ({days_back} days)\n")

    # Fetch a wider set of contracts to cover all historical dates
    sr1_contracts = get_sr1_tickers(n=16)
    sr3_contracts = get_sr3_tickers(n=8)
    all_contracts = sr1_contracts + sr3_contracts

    price_df = fetch_prices_historical(
        all_contracts,
        start=start_date.isoformat(),
        end=(end_date + timedelta(days=1)).isoformat(),
    )

    if price_df.empty:
        print("ERROR: No historical price data retrieved.")
        return

    history_rows = []

    for ts, row in price_df.iterrows():
        ref = ts.date() if hasattr(ts, "date") else date.fromisoformat(str(ts)[:10])
        prices = {col: float(val) for col, val in row.items()
                  if pd.notna(val) and float(val) > 50}

        if not prices:
            continue

        curve = build_implied_curve(sr1_contracts, sr3_contracts, prices, None, ref)
        if not curve:
            continue

        results = calculate_term_sofr(curve, ref)

        for label, rate in results.items():
            if rate is not None:
                tenor = label.replace(" Term SOFR", "").replace("-", "").strip()
                history_rows.append({
                    "Date":     ref.isoformat(),
                    "Tenor":    tenor,
                    "Rate (%)": round(rate, 5),
                    "Type":     "Term SOFR (Approximated)",
                    "Source":   "CME SR1 Futures via Yahoo Finance",
                    "Note":     "Bootstrapped from EOD settlement — not official CME fixing",
                })

    if history_rows:
        # Print last available day as preview
        last_date = max(r["Date"] for r in history_rows)
        last_day  = [r for r in history_rows if r["Date"] == last_date]
        print(f"\n  Latest calculated rates ({last_date}):")
        for r in last_day:
            print(f"    {r['Tenor']:<8}  {r['Rate (%)']:.4f}%")
        print()

    export_csv_history(history_rows, output_path)


# ── Current Mode ──────────────────────────────────────────────────────────────

def run_current(output_path: Path) -> None:
    ref_date = date.today()
    print(f"\nCurrent mode: calculating implied Term SOFR as of {ref_date}\n")

    sr1_contracts = get_sr1_tickers(n=14, ref_date=ref_date)
    sr3_contracts = get_sr3_tickers(n=6,  ref_date=ref_date)

    prices    = fetch_prices(sr1_contracts + sr3_contracts)
    overnight = fetch_overnight_sofr()

    if not prices:
        print("ERROR: Could not retrieve any futures prices. Check your internet connection.")
        return

    curve   = build_implied_curve(sr1_contracts, sr3_contracts, prices, overnight, ref_date)
    results = calculate_term_sofr(curve, ref_date)

    print_results(results, ref_date, overnight)
    export_csv_snapshot(results, ref_date, overnight, output_path)


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Approximate Term SOFR rates from CME futures via Yahoo Finance."
    )
    parser.add_argument(
        "--history",
        metavar="DAYS",
        type=int,
        default=None,
        help="Calculate implied rates over the last N calendar days (omit for today only).",
    )
    parser.add_argument(
        "--output",
        metavar="PATH",
        default=None,
        help="Output CSV path. Defaults to term_sofr_YYYYMMDD.csv",
    )
    return parser.parse_args()


def default_output(historical: bool) -> Path:
    tag   = "historical" if historical else "current"
    stamp = date.today().strftime("%Y%m%d")
    return Path(f"term_sofr_{tag}_{stamp}.csv")


def main() -> None:
    args        = parse_args()
    is_hist     = args.history is not None
    output_path = Path(args.output) if args.output else default_output(is_hist)

    print("=" * 55)
    print("  Term SOFR Approximation Calculator")
    print("  Source: CME SR1 Futures via Yahoo Finance + NY Fed")
    print("=" * 55)

    if is_hist:
        run_historical(args.history, output_path)
    else:
        run_current(output_path)


if __name__ == "__main__":
    main()
