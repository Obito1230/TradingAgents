#!/usr/bin/env python3
"""scan_demo_pool.py — Deterministic, LLM-free pre-scan to build the demo pool.

Why this exists
---------------
Each full TradingAgents run costs many LLM calls (dozens of nodes/tools), so
the demo / A-B scenarios must be fixed up front instead of picked ad hoc.
The L1 postmortem chain (see docs/memory_three_layer_design.md) only triggers
when a decision settles with |alpha| beyond a threshold, which in practice
requires:
  (a) the trade_date is far enough in the past that yfinance already has the
      subsequent price bars (default --end = today - 35d leaves settlement
      room for holding days + buffer), and
  (b) a large realized move happened shortly AFTER that date, so the run has
      a realistic chance to settle as an extreme win / loss.
This script finds candidate (ticker, trade_date) pairs with large realized
forward moves using yfinance only — zero LLM tokens, fully deterministic.

Demo pool vs evaluation set
---------------------------
Demo-pool picks are outcome-informed ON PURPOSE: they exist for human
demonstration (a run that actually triggers the L1 chain). The later paper
evaluation set must be selected by protocol, blind to outcome. Never reuse
pool picks as the reported metric set (see docs/competition_implementation_roadmap.md).

Usage
-----
  python scripts/scan_demo_pool.py                          # defaults
  python scripts/scan_demo_pool.py --tickers AAPL,NVDA --top 8 --min-abs 0.06
  python scripts/scan_demo_pool.py --write-pool demo_pool.json   # also emit skeleton
  python scripts/scan_demo_pool.py --csv pool_scan.csv           # machine-readable
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

# --- ticker → market label for the report -----------------------------------
def market_label(ticker: str) -> str:
    t = ticker.upper()
    for suffix, label in (
        (".HK", "Hong Kong"),
        (".T", "Tokyo"),
        (".L", "London"),
        (".NS", "India NSE"),
        (".BO", "India BSE"),
        (".TO", "Toronto"),
        (".AX", "Australia"),
        (".SS", "Shanghai"),
        (".SZ", "Shenzhen"),
        ("-USD", "Crypto"),
        ("-BTC", "Crypto"),
    ):
        if t.endswith(suffix):
            return label
    return "US"


def parse_args() -> argparse.Namespace:
    today = dt.date.today()
    end_default = today - dt.timedelta(days=35)  # settlement buffer
    start_default = end_default - dt.timedelta(days=240)

    p = argparse.ArgumentParser(
        description="Scan (ticker, trade_date) candidates with large realized forward moves.",
    )
    p.add_argument("--tickers", default="AAPL,NVDA,TSLA,MSFT,META,0700.HK,RELIANCE.NS,BTC-USD",
                   help="Comma-separated tickers (exchange suffix preserved, e.g. 0700.HK).")
    p.add_argument("--start", default=start_default.isoformat(), help="YYYY-MM-DD (inclusive scan window start).")
    p.add_argument("--end", default=end_default.isoformat(), help="YYYY-MM-DD; latest allowed trade_date.")
    p.add_argument("--holdings", default="5,10", help="Comma-separated forward holding windows in trading days.")
    p.add_argument("--min-abs", type=float, default=0.05,
                   help="Minimum |realized move| (any holding window) for a candidate to be reported.")
    p.add_argument("--top", type=int, default=5, help="Max candidates kept per ticker (by largest |move|).")
    p.add_argument("--write-pool", default=None, help="Optional path for a demo_pool.json skeleton.")
    p.add_argument("--csv", default=None, help="Optional path to dump the full candidate table as CSV.")
    return p.parse_args()


def fetch_close(ticker: str, start: str, end: str) -> pd.Series:
    """Close series (adjusted as returned by yfinance, like the framework uses)."""
    hist = yf.Ticker(ticker).history(start=start, end=end)
    if hist is None or hist.empty or "Close" not in hist.columns:
        return pd.Series(dtype=float)
    return hist["Close"].dropna()


def compute_forward_returns(close: pd.Series, holdings: list[int]) -> list[dict]:
    """For every eligible trade date, realized forward return per holding window."""
    out: list[dict] = []
    closes = close.reset_index(drop=True)
    dates = list(close.index)
    for i in range(len(closes) - max(holdings)):
        base = float(closes.iloc[i])
        if base <= 0:
            continue
        row = {
            "trade_date": dates[i].strftime("%Y-%m-%d"),
            "close": round(base, 4),
            "returns": {h: (float(closes.iloc[i + h]) / base - 1.0) for h in holdings},
        }
        out.append(row)
    return out


def main() -> int:
    args = parse_args()
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    holdings = [int(h) for h in args.holdings.split(",") if h.strip()]
    if not tickers or not holdings:
        print("error: --tickers and --holdings must not be empty", file=sys.stderr)
        return 2

    # Fetch a little beyond --end so forward returns exist for late candidates.
    fetch_end = (dt.date.fromisoformat(args.end) + dt.timedelta(days=60)).isoformat()
    records: list[dict] = []
    for ticker in tickers:
        try:
            close = fetch_close(ticker, args.start, fetch_end)
        except Exception as exc:  # noqa: BLE001 — one bad ticker must not kill the scan
            print(f"warn: failed to fetch {ticker}: {exc}", file=sys.stderr)
            continue
        if close.empty:
            print(f"warn: no data for {ticker} in window", file=sys.stderr)
            continue

        candidates = compute_forward_returns(close, holdings)
        # Keep only dates within the allowed trade-date window.
        end_d = dt.date.fromisoformat(args.end)
        candidates = [c for c in candidates if dt.date.fromisoformat(c["trade_date"]) <= end_d]
        # Filter by min |move| in any holding window.
        kept = [c for c in candidates if any(abs(v) >= args.min_abs for v in c["returns"].values())]
        kept.sort(key=lambda c: max(abs(v) for v in c["returns"].values()), reverse=True)
        for c in kept[: args.top]:
            rets = c.pop("returns")
            record = {
                "ticker": ticker,
                "market": market_label(ticker),
                **c,
                **{f"ret_{h}d": round(rets[h], 4) for h in holdings},
            }
            record["max_abs"] = max(abs(rets[h]) for h in holdings)
            records.append(record)

    if not records:
        print("No candidates matched - widen the window or lower --min-abs.")
        return 0

    records.sort(key=lambda r: r["max_abs"], reverse=True)

    # --- report --------------------------------------------------------------
    headers = ["ticker", "market", "trade_date", "close"] + [f"ret_{h}d" for h in holdings] + ["max_abs"]
    table = pd.DataFrame(records)[headers]
    print(table.to_string(index=False))
    print(f"\n{len(records)} candidate(s); top by |realized move|. "
          f"trade_date must stay >= ~{dt.date.today() - dt.timedelta(days=35)} for L1 settlement demo.")

    if args.csv:
        table.to_csv(args.csv, index=False)
        print(f"csv written: {args.csv}")

    if args.write_pool:
        suggestions = []
        for r in records[:12]:
            suggestions.append({k: r[k] for k in ("ticker", "market", "trade_date", "close", "max_abs")})
        skeleton = {
            "_readme": "Manual step: move suitable entries from 'suggestions' into the pool categories below. "
                       "Keep trade_date far enough in the past (>= ~35 days) so the L1 settlement demo can run. "
                       "Pool is for demos only — the paper evaluation set must be protocol-selected, blind to outcome.",
            "suggestions": suggestions,
            "pool": {
                "l1_extreme_win": [],
                "l1_extreme_loss": [],
                "routine_flow": [],
                "cross_market": [],
                "crypto_edge": [],
                "no_data_edge": ["<delisted / uncovered ticker; not scanned — used to demo the NO_DATA sentinel>"],
                "ab_compare": [],
            },
        }
        Path(args.write_pool).write_text(json.dumps(skeleton, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"demo_pool.json skeleton written: {args.write_pool}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
