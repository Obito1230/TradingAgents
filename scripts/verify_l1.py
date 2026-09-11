#!/usr/bin/env python3
"""verify_l1.py — deterministic acceptance checks for the L1 demo (step 7).

Runs AFTER the two-run settle recipe and checks the on-disk artifacts, no LLM:
  1. trading_lessons.md has an EVENT entry for (ticker, trade_date)
  2. the decision log entry is resolved (no pending left)
  3. cost_log.csv recorded the analysis (and settle) pass(es)
  4. get_lessons_context() actually injects that event (recency/direction floor)

Usage:
  python scripts/verify_l1.py --ticker MSFT --trade-date 2026-07-23 --expect-settle
  python scripts/verify_l1.py --ticker MSFT --trade-date 2026-07-23 \
      --lessons /tmp/l.md --memory-log /tmp/m.md --cost-log /tmp/c.csv

Exit code 0 = all checks passed; 1 = at least one failed.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tradingagents.agents.utils.memory import TradingMemoryLog  # noqa: E402
from tradingagents.default_config import DEFAULT_CONFIG  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify the L1 memory-chain demo artifacts.")
    ap.add_argument("--ticker", default="MSFT")
    ap.add_argument("--trade-date", default="2026-07-23", help="The analysis date that should have been settled.")
    ap.add_argument("--cost-log", default="cost_log.csv")
    ap.add_argument("--lessons", default=None, help="Override lessons_path.")
    ap.add_argument("--memory-log", default=None, help="Override memory_log_path.")
    ap.add_argument("--distill-queue", default=None, help="Override distill_queue_path.")
    ap.add_argument("--expect-settle", action="store_true",
                    help="Also require a 'settle' pass row in the cost log.")
    args = ap.parse_args()

    config = DEFAULT_CONFIG.copy()
    if args.lessons:
        config["lessons_path"] = args.lessons
    if args.memory_log:
        config["memory_log_path"] = args.memory_log
    if args.distill_queue:
        config["distill_queue_path"] = args.distill_queue
    log = TradingMemoryLog(config)

    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))

    # 1. EVENT entry in lessons
    events = [
        e for e in log.load_lessons()
        if e["ticker"] == args.ticker and e["trade_date"] == args.trade_date
    ]
    if events:
        e = events[0]
        ok = bool(e["summary"]) and bool(e["context_pointer"])
        check(
            "L1 EVENT entry in lessons",
            ok,
            f"entry_id={e['entry_id']} rating={e['rating']} alpha={e['alpha']} protected={e['protected']}",
        )
    else:
        check("L1 EVENT entry in lessons", False, f"no EVENT for {args.ticker}@{args.trade_date}")

    # 2. decision log resolved
    pending = [
        e for e in log.get_pending_entries()
        if e["ticker"] == args.ticker and e["date"] == args.trade_date
    ]
    check("decision log resolved (no pending)", len(pending) == 0, f"pending_count={len(pending)}")

    # 3. cost log rows
    cost_path = Path(args.cost_log)
    if cost_path.exists():
        rows = list(csv.DictReader(cost_path.open(encoding="utf-8")))
        ticker_rows = [r for r in rows if r.get("ticker") == args.ticker]
        passes = {r.get("pass") for r in ticker_rows}
        if args.expect_settle:
            cost_ok = "analysis" in passes and "settle" in passes
        else:
            cost_ok = len(ticker_rows) >= 1
        check("cost_log.csv recorded the run(s)", cost_ok,
              f"rows_for_ticker={len(ticker_rows)} passes={sorted(p for p in passes if p)}")
    else:
        check("cost_log.csv recorded the run(s)", False, f"not found: {cost_path}")

    # 4. injection surfaces the event
    ctx = log.get_lessons_context(args.ticker)
    check("event is injected into past_context", args.trade_date in ctx,
          "get_lessons_context contains the event date")

    print(f"L1 acceptance check for {args.ticker} @ {args.trade_date}")
    print(f"lessons_path = {log._lessons_path}")
    print("-" * 60)
    for name, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    print("-" * 60)
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"  {passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
