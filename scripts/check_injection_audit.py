#!/usr/bin/env python3
"""check_injection_audit.py — prove a specific run actually received memory.

Reads the saved run state
(``results_dir/<TICKER>/TradingAgentsStrategy_logs/full_states_log_<date>.json``)
and reports the ``past_context`` the Portfolio Manager received for that run.

That field is the audit trail written by ``TradingAgentsGraph._log_state``: if it
contains your L1 event, the memory layer was read by a real decision — the
on-disk evidence for "memory entered the decision".

Usage:
  python scripts/check_injection_audit.py --ticker 300750.SZ --date 2026-04-08
  python scripts/check_injection_audit.py --ticker 300750.SZ --date 2026-04-08 --preview 800
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Windows consoles often default to a legacy code page (GBK); memory text can
# contain characters it cannot encode, which would crash the print.
try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):  # pragma: no cover - platform dependent
    pass

from tradingagents.dataflows.utils import safe_ticker_component  # noqa: E402
from tradingagents.default_config import DEFAULT_CONFIG  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Show the past_context a run actually received.")
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--date", required=True, help="YYYY-MM-DD of the run to inspect.")
    ap.add_argument("--preview", type=int, default=400, help="Characters of context to print.")
    args = ap.parse_args()

    path = (
        Path(DEFAULT_CONFIG["results_dir"])
        / safe_ticker_component(args.ticker)
        / "TradingAgentsStrategy_logs"
        / f"full_states_log_{args.date}.json"
    )
    print(f"state file  : {path}")

    if not path.exists():
        print("\nNOT FOUND. A failed run saves no state, so this usually means the run")
        print("did not finish (check the cost_log row's status/error for that date).")
        return 1

    data = json.loads(path.read_text(encoding="utf-8"))
    print(f"run ticker  : {data.get('company_of_interest')}   date: {data.get('trade_date')}")

    if "past_context" not in data:
        print("\nEMPTY: this state file predates the injection-audit field, so it")
        print("cannot show the memory this run received. Re-run to record it.")
        return 2

    ctx = data.get("past_context", "")
    print(f"past_context: {len(ctx)} chars")
    if not ctx:
        print("\nEMPTY: the memory store had nothing to inject for this ticker at run time.")
        return 3

    print("segments    :")
    for marker, label in (
        ("Past extreme events", "L1 events (this ticker)"),
        ("Recent cross-ticker extreme events", "L1 events (other tickers)"),
        ("Past analyses of", "legacy decision-log fallback"),
    ):
        if marker in ctx:
            print(f"  - {label}")
    print(f"\npreview (first {args.preview} chars):")
    print(ctx[: args.preview])
    print("\n=> Memory WAS read by this run: the L1 event above is your architecture working.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
