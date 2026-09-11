#!/usr/bin/env python3
"""cost_accounting.py — per-run token + wall-clock cost ledger for TradingAgents.

Wraps a single TradingAgentsGraph.propagate() run with a LangChain callback
handler that records, per run:

  * wall-clock duration (seconds)
  * LLM call count and tool call count
  * input / output / total tokens (aggregate AND per-model)
  * estimated USD cost (from a per-model rate card you maintain)
  * the final rating + status/error

Each run appends one row to an append-only CSV (default: cost_log.csv), so you
can watch cost accumulate across the demo pool / A-B experiments instead of
guessing after the fact. A run that crashes is still recorded (status=error)
with whatever tokens it consumed before dying — that partial cost matters.

Usage:
  python scripts/cost_accounting.py --ticker MSFT --date 2026-07-23
  python scripts/cost_accounting.py --ticker BTC-USD --date 2026-01-26 --asset-type crypto
  python scripts/cost_accounting.py --ticker AAPL --date 2026-06-25 --log cost_log.csv
  python scripts/cost_accounting.py --ticker AAPL --date 2026-06-25 --price-json prices.json

Programmatic:
  from scripts.cost_accounting import measure_run   # (when run from repo root)
  _, decision, stats = measure_run("MSFT", "2026-07-23")
  print(stats["tokens_total"], stats["cost_usd"])

Notes:
  * Requires the package installed (pip install .) or run from the repo root;
    a sys.path shim below also makes `python scripts/cost_accounting.py` work
    from a source checkout.
  * The price table below is a PLACEHOLDER rate card (USD per 1M tokens).
    Replace it with your provider's current prices, or pass --price-json.
    Models with no price entry still report tokens; cost is reported as None
    if any model's price is unknown.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Standalone-script safety: import the repo even when run as `python scripts/...`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cli.stats_handler import StatsCallbackHandler  # noqa: E402
from langchain_core.outputs import LLMResult  # noqa: E402
from tradingagents.agents.utils.rating import parse_rating  # noqa: E402
from tradingagents.default_config import DEFAULT_CONFIG  # noqa: E402
from tradingagents.graph.trading_graph import TradingAgentsGraph  # noqa: E402

# ---------------------------------------------------------------------------
# PLACEHOLDER rate card (USD per 1,000,000 tokens): (input, output).
# These are NOT authoritative — update to your provider's current pricing or
# pass --price-json. Exact model-id match wins, then longest-prefix match.
# ---------------------------------------------------------------------------
PRICE_USD_PER_1M: dict[str, tuple[float, float]] = {
    "gpt-5.5": (1.25, 10.0),
    "gpt-5.4": (2.50, 10.0),
    "gpt-5.4-mini": (0.15, 0.60),
    "gpt-5.2": (1.25, 5.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "gemini-3.5-flash": (0.10, 0.40),
    "gemini-3.1-flash-lite": (0.10, 0.40),
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    "deepseek-v4-flash": (0.20, 0.80),
    "qwen3.7-plus": (0.20, 0.80),
    "glm-5": (0.30, 1.20),
    "grok-4.3": (2.0, 8.0),
}


def match_price(model: str) -> tuple[float, float] | None:
    """Resolve a model id to (input, output) USD per 1M tokens, or None."""
    if model in PRICE_USD_PER_1M:
        return PRICE_USD_PER_1M[model]
    for key in sorted(PRICE_USD_PER_1M, key=len, reverse=True):
        if model.startswith(key):
            return PRICE_USD_PER_1M[key]
    return None


def _extract_usage(response: LLMResult) -> tuple[int, int]:
    """Best-effort (input_tokens, output_tokens) from an LLMResult."""
    llm_output = getattr(response, "llm_output", None) or {}
    usage = llm_output.get("token_usage") or {}
    inp = usage.get("prompt_tokens", usage.get("input_tokens"))
    out = usage.get("completion_tokens", usage.get("output_tokens"))
    if inp is None and out is None:
        try:
            gen = response.generations[0][0]
            msg = getattr(gen, "message", None)
            um = getattr(msg, "usage_metadata", None) or {}
            inp, out = um.get("input_tokens"), um.get("output_tokens")
        except (IndexError, TypeError, AttributeError):
            inp = out = 0
    return int(inp or 0), int(out or 0)


def _model_name(response: LLMResult) -> str:
    llm_output = getattr(response, "llm_output", None) or {}
    name = llm_output.get("model_name")
    if name:
        return str(name)
    try:
        msg = getattr(response.generations[0][0], "message", None)
        meta = getattr(msg, "response_metadata", None) or {}
        return str(meta.get("model_name") or "unknown")
    except (IndexError, TypeError, AttributeError):
        return "unknown"


class CostTrackingHandler(StatsCallbackHandler):
    """StatsCallbackHandler + per-model token breakdown."""

    def __init__(self) -> None:
        super().__init__()
        self.per_model: dict[str, dict[str, int]] = {}

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        inp, out = _extract_usage(response)
        model = _model_name(response)
        with self._lock:
            self.tokens_in += inp
            self.tokens_out += out
            bucket = self.per_model.setdefault(model, {"calls": 0, "in": 0, "out": 0})
            bucket["calls"] += 1
            bucket["in"] += inp
            bucket["out"] += out


def estimate_cost(handler: CostTrackingHandler) -> tuple[float | None, int]:
    """Sum per-model USD cost; returns (cost_or_None, unknown_model_count)."""
    total = 0.0
    unknown = 0
    for model, bucket in handler.per_model.items():
        price = match_price(model)
        if price is None:
            unknown += 1
            continue
        total += bucket["in"] / 1e6 * price[0] + bucket["out"] / 1e6 * price[1]
    return (round(total, 6) if unknown == 0 else None), unknown


def measure_run(
    ticker: str,
    trade_date: str,
    asset_type: str = "stock",
    config: dict[str, Any] | None = None,
    selected_analysts: list[str] | None = None,
    debug: bool = False,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    """Run one propagate() under cost tracking; returns (state, decision, stats).

    ``selected_analysts`` restricts the analyst team (e.g. drop "news" for
    A-share runs or to shrink the prompt surface); None keeps the framework
    default (market, social, news, fundamentals). ``debug`` streams node-level
    output so a long run shows progress.
    """
    handler = CostTrackingHandler()
    graph_kwargs: dict[str, Any] = {}
    if selected_analysts:
        graph_kwargs["selected_analysts"] = tuple(selected_analysts)
    ta = TradingAgentsGraph(
        debug=debug,
        config=config if config is not None else DEFAULT_CONFIG.copy(),
        callbacks=[handler],
        **graph_kwargs,
    )

    # Propagate callbacks into the graph invocation too, so tool calls are
    # counted (LLM-level callbacks alone only see the model calls).
    orig = ta.propagator.get_graph_args
    def patched(callbacks: list | None = None) -> dict[str, Any]:
        args = orig(callbacks)
        args.setdefault("config", {})["callbacks"] = [handler]
        return args
    ta.propagator.get_graph_args = patched  # type: ignore[method-assign]

    start = time.monotonic()
    status, error = "ok", ""
    final_state: dict[str, Any] = {}
    decision = ""
    try:
        final_state, decision = ta.propagate(ticker, trade_date, asset_type=asset_type)
    except Exception as exc:  # noqa: BLE001 — partial cost still gets recorded
        status = "error"
        error = f"{type(exc).__name__}: {exc}"
    duration = time.monotonic() - start

    cost, unknown = estimate_cost(handler)
    rating = parse_rating(decision) if decision else ""
    stats = {
        "status": status,
        "error": error,
        "rating": rating,
        "duration_s": round(duration, 2),
        "llm_calls": handler.llm_calls,
        "tool_calls": handler.tool_calls,
        "tokens_in": handler.tokens_in,
        "tokens_out": handler.tokens_out,
        "tokens_total": handler.tokens_in + handler.tokens_out,
        "cost_usd": cost,
        "pricing_unknown_models": unknown,
        "per_model": handler.per_model,
    }
    return final_state, decision, stats


CSV_COLUMNS = [
    "started_at", "ticker", "trade_date", "asset_type",
    "provider", "deep_model", "quick_model",
    "status", "error", "rating",
    "duration_s", "llm_calls", "tool_calls",
    "tokens_in", "tokens_out", "tokens_total", "cost_usd", "per_model",
]


def append_csv(path: str, row: dict[str, Any], extra_columns: list[str] | None = None) -> None:
    fieldnames = CSV_COLUMNS + (extra_columns or [])
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        if new:
            writer.writeheader()
        writer.writerow(row)


def make_csv_row(
    ticker: str,
    trade_date: str,
    asset_type: str,
    started_at: str,
    stats: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one cost CSV row from a measure_run() stats dict."""
    cfg = DEFAULT_CONFIG
    row = {
        "started_at": started_at,
        "ticker": ticker,
        "trade_date": trade_date,
        "asset_type": asset_type,
        "provider": cfg.get("llm_provider", ""),
        "deep_model": cfg.get("deep_think_llm", ""),
        "quick_model": cfg.get("quick_think_llm", ""),
        "status": stats["status"],
        "error": stats["error"],
        "rating": stats["rating"],
        "duration_s": stats["duration_s"],
        "llm_calls": stats["llm_calls"],
        "tool_calls": stats["tool_calls"],
        "tokens_in": stats["tokens_in"],
        "tokens_out": stats["tokens_out"],
        "tokens_total": stats["tokens_total"],
        "cost_usd": stats["cost_usd"],
        "per_model": json.dumps(stats["per_model"], ensure_ascii=False),
    }
    if extra:
        row.update(extra)
    return row


def _summary(stats: dict[str, Any]) -> str:
    lines = [
        f"status        : {stats['status']}" + (f" ({stats['error']})" if stats["error"] else ""),
        f"rating        : {stats['rating'] or 'n/a'}",
        f"duration_s    : {stats['duration_s']}",
        f"llm_calls     : {stats['llm_calls']}",
        f"tool_calls    : {stats['tool_calls']}",
        f"tokens_in     : {stats['tokens_in']}",
        f"tokens_out    : {stats['tokens_out']}",
        f"tokens_total  : {stats['tokens_total']}",
        f"cost_usd      : {stats['cost_usd'] if stats['cost_usd'] is not None else 'n/a (unknown pricing)'}",
    ]
    if stats["per_model"]:
        lines.append("per_model:")
        for model, b in stats["per_model"].items():
            lines.append(f"  - {model}: calls={b['calls']} in={b['in']} out={b['out']}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Run one TradingAgents analysis with cost tracking.")
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--date", required=True, help="YYYY-MM-DD analysis date.")
    ap.add_argument("--asset-type", default="stock", choices=["stock", "crypto"])
    ap.add_argument("--log", default="cost_log.csv")
    ap.add_argument("--price-json", default=None, help="Optional JSON {model: [in$, out$] per 1M}.")
    args = ap.parse_args()

    if args.price_json:
        data = json.loads(Path(args.price_json).read_text(encoding="utf-8"))
        PRICE_USD_PER_1M.update({k: tuple(v) for k, v in data.items()})

    started_at = datetime.now(timezone.utc).isoformat()
    _, _, stats = measure_run(args.ticker, args.date, args.asset_type)

    row = make_csv_row(args.ticker, args.date, args.asset_type, started_at, stats)
    append_csv(args.log, row)

    print(_summary(stats))
    print(f"\nappended to {args.log}")
    print("NOTE: cost is an ESTIMATE from the placeholder rate card "
          "(edit PRICE_USD_PER_1M or pass --price-json).")
    return 0 if stats["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
