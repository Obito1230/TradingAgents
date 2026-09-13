#!/usr/bin/env python3
"""show_injection.py — print exactly what memory the Portfolio Manager receives.

Deterministic, zero LLM calls. Assembles the same three-segment ``past_context``
the graph builds at run start (``TradingAgentsGraph._build_past_context``):

  1. L3 team rules (preamble; empty until v2 distillation ships)
  2. L1 event summaries   <- written by the postmortem agent, i.e. YOUR memory
  3. legacy decision-log reflections (upstream baseline, kept as fallback)

Use it to prove the memory layer is actually READ, not just written — the write
path is ``trading_lessons.md``; this shows the retrieval/injection path.

Note: segment 2 bumps each selected event's hits / last_accessed (that is the
access-tracking side effect), so running this counts as a real retrieval.

Usage:
  python scripts/show_injection.py --ticker 300750.SZ
  python scripts/show_injection.py --ticker 300750.SZ --raw      # only the raw string
  python scripts/show_injection.py --ticker 300750.SZ --n-same 8 --n-cross 2
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Windows consoles often default to a legacy code page (GBK); memory text can
# contain characters it cannot encode (e.g. U+2212 minus), which would crash the
# print. Degrade those characters instead of failing.
try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):  # pragma: no cover - platform dependent
    pass

from tradingagents.agents.utils.memory import TradingMemoryLog  # noqa: E402
from tradingagents.default_config import DEFAULT_CONFIG  # noqa: E402
from tradingagents.graph.trading_graph import TradingAgentsGraph  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Show the past_context the PM would receive.")
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--date", required=True,
                    help="The decision date the memory would be injected for (YYYY-MM-DD). "
                         "Required: memory is time-sliced, so without a date there is no "
                         "single correct answer to 'what would the PM receive'.")
    ap.add_argument("--n-same", type=int, default=5, help="Max same-ticker items per segment.")
    ap.add_argument("--n-cross", type=int, default=3, help="Max cross-ticker event items.")
    ap.add_argument("--raw", action="store_true", help="Print only the assembled string.")
    args = ap.parse_args()

    log = TradingMemoryLog(DEFAULT_CONFIG)
    events = log.load_lessons()
    ticker_events = [e for e in events if e["ticker"] == args.ticker]

    if not args.raw:
        print(f"lessons_path = {log._lessons_path}")
        print(f"lessons file exists = {bool(log._lessons_path and log._lessons_path.exists())}")
        print(f"EVENT entries total = {len(events)}  | for {args.ticker} = {len(ticker_events)}")
        for e in ticker_events:
            sliced = "  [SLICED OUT: on/after as_of]" if e["trade_date"] >= args.date else ""
            print(
                f"  - {e['entry_id']}  {e['trade_date']}  {e['rating']}  "
                f"alpha={e['alpha']}  protected={e['protected']}  hits={e['hits']}{sliced}"
            )

    inst = object.__new__(TradingAgentsGraph)
    inst.memory_log = log
    inst.config = DEFAULT_CONFIG
    context = TradingAgentsGraph._build_past_context(inst, args.ticker, as_of=args.date)

    if args.raw:
        print(context)
        return 0

    print("\n--- assembled past_context (this is injected as \"Team memory:\") ---")
    print(context if context else "(empty: no rules, no events, no legacy reflections)")
    print("--- end ---")

    if not ticker_events:
        print("\nNo EVENT entries for this ticker yet: the write path has not produced")
        print("memory for it, so there is nothing for the PM to be given.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
