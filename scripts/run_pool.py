#!/usr/bin/env python3
"""run_pool.py — run demo_pool.json scenarios sequentially, with cost accounting.

Reads the pool file produced by scan_demo_pool.py / curated demo_pool.json,
extracts the runnable scenarios (ticker + trade_date), and runs each one
through cost_accounting.measure_run(), appending one cost row per run to an
append-only CSV. Duplicate (ticker, trade_date) pairs across categories are
deduplicated, so ab_compare entries that reuse a base scenario run only once.

Optional two-run L1 recipe: with --settle, each scenario runs twice — the
analysis date and a later "settle" date that triggers deferred settlement of
the first run's pending decision. This doubles cost; only use it once the L1
postmortem chain exists (docs/memory_three_layer_design.md).

Usage:
  python scripts/run_pool.py --dry-run                       # show what would run
  python scripts/run_pool.py                                 # run all runnable scenarios
  python scripts/run_pool.py --categories l1_extreme_win,l1_extreme_loss
  python scripts/run_pool.py --only MSFT
  python scripts/run_pool.py --settle --settle-days 14        # two-run recipe
  python scripts/run_pool.py --log cost_log.csv
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent

# scripts/ is not a package; load the sibling cost_accounting module by path so
# this runs both from a pip-installed checkout and a raw source clone.
def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cost = _load("cost_accounting", _HERE / "cost_accounting.py")

# Categories that are placeholders, not runnable scenarios.
_PLACEHOLDER_CATEGORIES = {"routine_flow", "no_data_edge"}


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Run demo_pool.json scenarios with cost tracking.")
    ap.add_argument("--pool", default="demo_pool.json")
    ap.add_argument("--log", default="cost_log.csv")
    ap.add_argument("--categories", default=None,
                    help="Comma-separated categories to run (default: all runnable).")
    ap.add_argument("--only", default=None, help="Comma-separated tickers to restrict to.")
    ap.add_argument("--settle", action="store_true",
                    help="Also run a later settle pass per scenario (doubles cost).")
    ap.add_argument("--settle-days", type=int, default=14,
                    help="Calendar days after trade_date for the settle pass.")
    ap.add_argument("--dry-run", action="store_true", help="List scenarios without running.")
    ap.add_argument("--debug", action="store_true",
                    help="Stream node-level output from the graph (verbose progress).")
    ap.add_argument("--heartbeat", type=int, default=60,
                    help="Seconds between 'still running' heartbeat lines (0 disables).")
    ap.add_argument("--analysts", default=None,
                    help="Comma-separated analyst keys (market,social,news,fundamentals); "
                         "default = all four. Use e.g. 'market,social,fundamentals' to skip "
                         "the news analyst (fewer geopolitics-heavy prompts on moderated providers).")
    return ap.parse_args()


def load_pool(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def collect_scenarios(pool: dict, categories: list[str] | None, only: set[str] | None) -> list[tuple[str, dict]]:
    """Return deduplicated runnable (category, entry) scenarios in file order."""
    groups = pool.get("pool", {})
    selected = categories if categories is not None else [k for k in groups if k not in _PLACEHOLDER_CATEGORIES]

    scenarios: list[tuple[str, dict]] = []
    seen: set[tuple[str, str]] = set()
    for cat in groups:
        if cat not in selected:
            continue
        for entry in groups[cat]:
            if not isinstance(entry, dict):
                continue
            ticker = entry.get("ticker")
            date = entry.get("trade_date")
            if not ticker or not date:
                continue  # placeholder entries (routine_flow _todo etc.)
            if only and ticker not in only:
                continue
            key = (ticker, str(date))
            if key in seen:
                continue
            seen.add(key)
            scenarios.append((cat, entry))
    return scenarios


def asset_type_of(ticker: str) -> str:
    return "crypto" if ticker.upper().endswith("-USD") else "stock"


def shift_date(date: str, days: int) -> str:
    return (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")


class Heartbeat:
    """Print a progress line every ``interval`` seconds while a run is in flight.

    The graph is silent between node calls, so a long run looks frozen; this
    makes it obvious the process is alive without waiting for the final line.
    """

    def __init__(self, label: str, interval: float = 60.0):
        self.label = label
        self.interval = interval
        self._stop = threading.Event()
        self._start = time.monotonic()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            print(f"    ... {self.label} still running ({time.monotonic() - self._start:.0f}s)", flush=True)

    def __enter__(self):
        if self.interval > 0:
            self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        return False


def main() -> int:
    args = parse_args()
    pool = load_pool(args.pool)
    only = {t.strip() for t in args.only.split(",") if t.strip()} if args.only else None
    categories = [c.strip() for c in args.categories.split(",") if c.strip()] if args.categories else None
    analysts = [a.strip() for a in args.analysts.split(",") if a.strip()] if args.analysts else None
    scenarios = collect_scenarios(pool, categories, only)

    if not scenarios:
        print("No runnable scenarios matched.")
        return 0

    print(f"Pool: {args.pool}  ({len(scenarios)} unique scenario(s)"
          f"{'; settle pass enabled (+1 run each)' if args.settle else ''})\n")
    if args.dry_run:
        for cat, e in scenarios:
            print(f"  [{cat}] {e['ticker']} @ {e['trade_date']}")
        print("\n(dry-run; nothing executed)")
        return 0

    totals = {"runs": 0, "errors": 0, "tokens_total": 0, "cost_usd": 0.0, "duration_s": 0.0, "cost_unknown": False}
    for cat, e in scenarios:
        ticker, date = e["ticker"], str(e["trade_date"])
        for pass_label, run_date in _passes(date, args):
            started_at = datetime.now(timezone.utc).isoformat()
            label = f"{ticker} @ {run_date}"
            print(f"  [{cat}/{pass_label}] {label} - starting (a full run can take several minutes)", flush=True)
            with Heartbeat(label, interval=args.heartbeat):
                _, _, stats = cost.measure_run(
                    ticker, run_date, asset_type_of(ticker),
                    selected_analysts=analysts, debug=args.debug,
                )
            row = cost.make_csv_row(
                ticker, run_date, asset_type_of(ticker), started_at, stats,
                extra={"category": cat, "pass": pass_label},
            )
            cost.append_csv(args.log, row, extra_columns=["category", "pass"])
            _print_run(cat, pass_label, ticker, run_date, stats)
            _accumulate(totals, stats)

    _print_summary(totals)
    print(f"\ncost log: {args.log}")
    return 1 if totals["errors"] else 0


def _passes(date: str, args: argparse.Namespace):
    yield "analysis", date
    if args.settle:
        yield "settle", shift_date(date, args.settle_days)


def _print_run(cat: str, pass_label: str, ticker: str, date: str, stats: dict) -> None:
    cost_s = f"${stats['cost_usd']:.6f}" if stats["cost_usd"] is not None else "n/a"
    status = stats["status"] + (f" ({stats['error']})" if stats["error"] else "")
    print(f"  [{cat}/{pass_label}] {ticker} @ {date} -> {stats['rating'] or '-'} | "
          f"{stats['tokens_total']} tok | {stats['duration_s']}s | {cost_s} | {status}")


def _accumulate(totals: dict, stats: dict) -> None:
    totals["runs"] += 1
    if stats["status"] != "ok":
        totals["errors"] += 1
    totals["tokens_total"] += stats["tokens_total"]
    totals["duration_s"] += stats["duration_s"]
    if stats["cost_usd"] is not None:
        totals["cost_usd"] += stats["cost_usd"]
    else:
        totals["cost_unknown"] = True


def _print_summary(totals: dict) -> None:
    cost_s = f"${totals['cost_usd']:.6f}" + (" (some models unpriced)" if totals["cost_unknown"] else "")
    print("\n--- summary ---")
    print(f"  runs          : {totals['runs']}")
    print(f"  errors        : {totals['errors']}")
    print(f"  tokens_total  : {totals['tokens_total']}")
    print(f"  duration_s    : {round(totals['duration_s'], 1)}")
    print(f"  cost_usd      : {cost_s}")


if __name__ == "__main__":
    raise SystemExit(main())
