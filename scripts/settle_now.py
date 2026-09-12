#!/usr/bin/env python3
"""settle_now.py — resolve pending decisions WITHOUT running a full analysis.

The L1 write path normally fires at the start of the *next* run for a ticker
(``propagate`` -> ``_resolve_pending_entries``), which costs a full pipeline
(~12 LLM calls / ~15 min on a slow provider). This script calls the settlement
step directly for one ticker, so you can:

  * complete the L1 write path cheaply (a couple of LLM calls, seconds),
  * demo "pending -> settled -> EVENT written" without a 15-minute run,
  * recover an event after an analysis run failed for an unrelated reason
    (settlement happens before the analysis, so it is not blocked by it).

It does exactly what propagate() does at settlement time — fetch realized
returns, generate the reflection, run the postmortem agent when |alpha| passes
the threshold, write the EVENT, then maintain_memory() — and appends one cost
row per invocation so the ledger stays complete.

Usage:
  python scripts/settle_now.py --ticker 300750.SZ
  python scripts/settle_now.py --ticker 300750.SZ --dry-run   # show pendings only
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

# Windows consoles often default to a legacy code page (GBK); memory text can
# contain characters it cannot encode, which would crash the print.
try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):  # pragma: no cover - platform dependent
    pass


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cost = _load("cost_accounting", _HERE / "cost_accounting.py")

from tradingagents.default_config import DEFAULT_CONFIG  # noqa: E402
from tradingagents.graph.trading_graph import TradingAgentsGraph  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Settle pending decisions for one ticker.")
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--log", default="cost_log.csv")
    ap.add_argument("--dry-run", action="store_true", help="List pendings without settling.")
    args = ap.parse_args()

    config = DEFAULT_CONFIG.copy()
    handler = cost.CostTrackingHandler()
    ta = TradingAgentsGraph(debug=False, config=config, callbacks=[handler])

    pending = [e for e in ta.memory_log.get_pending_entries() if e["ticker"] == args.ticker]
    print(f"ticker            : {args.ticker}")
    print(f"lessons_path      : {ta.memory_log._lessons_path}")
    print(f"pending entries   : {len(pending)}")
    for e in pending:
        print(f"  - {e['date']}  rating={e['rating']}")

    if not pending:
        print("\nNothing to settle for this ticker. (A pending entry is created by a")
        print("completed analysis run; settle the ticker you actually ran.)")
        return 0

    if args.dry_run:
        print("\n(dry-run; nothing settled)")
        return 0

    before = {e["entry_id"] for e in ta.memory_log.load_lessons()}
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.monotonic()
    status, error = "ok", ""
    print("\nsettling (fetch returns -> reflection -> postmortem when |alpha| passes)...", flush=True)
    try:
        ta._resolve_pending_entries(args.ticker)
        ta.memory_log.maintain_memory()
    except Exception as exc:  # noqa: BLE001 — report, still log the cost
        status, error = "error", f"{type(exc).__name__}: {exc}"
    duration = time.monotonic() - started

    est_cost, _unknown = cost.estimate_cost(handler)
    stats = {
        "status": status, "error": error, "rating": "",
        "duration_s": round(duration, 2),
        "llm_calls": handler.llm_calls, "tool_calls": handler.tool_calls,
        "tokens_in": handler.tokens_in, "tokens_out": handler.tokens_out,
        "tokens_total": handler.tokens_in + handler.tokens_out,
        "cost_usd": est_cost, "per_model": handler.per_model,
    }
    cost.append_csv(
        args.log,
        cost.make_csv_row(
            args.ticker, pending[0]["date"], "stock", started_at, stats,
            extra={"category": "manual_settle", "pass": "settle"},
        ),
        extra_columns=["category", "pass"],
    )

    after = ta.memory_log.load_lessons()
    new_events = [e for e in after if e["entry_id"] not in before]
    print(f"\nstatus            : {status}" + (f" ({error})" if error else ""))
    print(f"llm_calls         : {handler.llm_calls}   tokens: {stats['tokens_total']}   "
          f"cost: {'n/a' if est_cost is None else f'${est_cost:.6f}'}   {duration:.1f}s")
    print(f"EVENT before={len(before)} after={len(after)} new={len(new_events)}")
    for e in new_events:
        print(
            f"  + {e['entry_id']}  {e['trade_date']}  {e['rating']}  "
            f"alpha={e['alpha']}  protected={e['protected']}"
        )
        print(f"    summary head: {e['summary'].splitlines()[0][:120]}")

    still_pending = [e for e in ta.memory_log.get_pending_entries() if e["ticker"] == args.ticker]
    print(f"pending after     : {len(still_pending)}")

    if status == "ok" and not new_events:
        print(
            f"\nNo EVENT written. Either |alpha| stayed below "
            f"event_alpha_threshold={config.get('event_alpha_threshold')} (an ordinary "
            f"outcome, by design), or realized returns were unavailable yet."
        )
    print(f"\ncost row appended to {args.log} (category=manual_settle)")
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
