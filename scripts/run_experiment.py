#!/usr/bin/env python3
"""run_experiment.py — paired A/B runner for the L1 event-memory layer.

Design (see docs/experiment_plan.md):

  * arms: ``on`` (config ``event_memory_enabled=True``) vs ``off`` (False).
    The legacy decision-log reflections are injected in BOTH arms, so the only
    difference is the L1 layer.
  * for each scenario x arm x repetition: run one analysis and log rating,
    tokens, cost and duration.
  * the realized outcome is resolved WITHOUT any LLM (yfinance forward return vs
    the regional benchmark), so a decision does not need a second pipeline run
    to be scored.

Guards — a leaked or empty comparison is worse than no experiment:

  * LEAK    a scenario whose own (ticker, date) already has an EVENT would be
            shown the outcome of the very decision being tested. Skipped unless
            ``--allow-leak`` is passed.
  * EMPTY   if the lessons store has no events at all, the "on" arm is identical
            to "off". Refuses to run unless ``--force``.
  * PENDING settlement of an older pending entry injects an extra LLM call
    asymmetrically. Warns and lists them; ``settle_now.py`` clears them cheaply.

Usage:
  python scripts/run_experiment.py --scenarios exp_scenarios.json --reps 3
  python scripts/run_experiment.py --scenarios exp_scenarios.json --reps 3 \
      --analysts market,social,fundamentals --progress --heartbeat 60
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

# Windows consoles often default to a legacy code page (GBK).
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

from tradingagents.agents.utils.memory import TradingMemoryLog  # noqa: E402
from tradingagents.default_config import DEFAULT_CONFIG  # noqa: E402
from tradingagents.graph.trading_graph import TradingAgentsGraph  # noqa: E402

BULLISH = {"Buy", "Overweight"}
BEARISH = {"Sell", "Underweight"}


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Paired A/B runner for the L1 memory layer.")
    ap.add_argument("--scenarios", default="exp_scenarios.json",
                    help='JSON list of {"ticker": ..., "date": "YYYY-MM-DD"}.')
    ap.add_argument("--arms", default="on,off", help="Comma-separated arms to run.")
    ap.add_argument("--reps", type=int, default=3, help="Repetitions per (scenario, arm).")
    ap.add_argument("--holding-days", type=int, default=5, help="Forward window for scoring.")
    ap.add_argument("--analysts", default=None, help="Comma-separated analyst keys.")
    ap.add_argument("--log", default="experiments/p1_runs.csv", help="Experiment rows CSV.")
    ap.add_argument("--cost-log", default="cost_log.csv", help="Shared cost ledger.")
    ap.add_argument("--progress", action="store_true", help="Node-level progress trace.")
    ap.add_argument("--debug", action="store_true", help="Verbose node messages.")
    ap.add_argument("--heartbeat", type=int, default=120, help="Seconds between heartbeats.")
    ap.add_argument("--allow-leak", action="store_true", help="Run leaky scenarios anyway.")
    ap.add_argument("--force", action="store_true", help="Run even with an empty memory.")
    ap.add_argument("--est-per-run", type=float, default=None,
                    help="Seconds per run to use for the estimate, instead of the ledger.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the planned matrix (guards + realized outcomes + estimate) "
                         "without running any LLM call.")
    return ap.parse_args()


def resolve_realized(ticker: str, date: str, holding: int, config: dict, attempts: int = 3):
    """Realized (benchmark, raw_return, alpha_return, days) with no LLM call.

    yfinance is rate-limited on the same hosts the pipeline uses, so the scoring
    lookup is retried with backoff before it is allowed to report "unavailable".
    """
    inst = object.__new__(TradingAgentsGraph)
    inst.config = config
    benchmark = TradingAgentsGraph._resolve_benchmark(inst, ticker)
    for attempt in range(1, attempts + 1):
        raw, alpha, days = TradingAgentsGraph._fetch_returns(
            inst, ticker, date, holding_days=holding, benchmark=benchmark
        )
        if raw is not None:
            return benchmark, raw, alpha, days
        if attempt < attempts:
            wait = 5.0 * attempt
            print(f"    (scoring lookup for {ticker}@{date} failed; retrying in {wait:.0f}s "
                  f"- {attempt}/{attempts})", flush=True)
            time.sleep(wait)
    return benchmark, None, None, None


def hit_of(rating: str, alpha: float | None) -> float | None:
    """1.0 / 0.0 for a directional call; None for Hold or an unknown outcome."""
    if alpha is None or alpha == 0 or rating not in (BULLISH | BEARISH):
        return None
    return 1.0 if (alpha > 0) == (rating in BULLISH) else 0.0


def injected_chars(ticker: str, trade_date: str, config: dict | None = None) -> int | None:
    """Characters of memory this run actually received, read from its own archive.

    The two arms write the SAME filename (``full_states_log_<date>.json``), so the
    arm that runs second overwrites the first one's copy. Reading the file
    immediately after each run is therefore the only way to capture BOTH arms'
    injection budgets — the C2 claim is about injected budget, and it should be
    measured per run rather than derived.

    ``config`` is injectable so tests can point at a temporary results dir.
    """
    inst = object.__new__(TradingAgentsGraph)
    inst.config = config or DEFAULT_CONFIG
    try:
        path = TradingAgentsGraph._full_state_path(inst, ticker, trade_date)
        payload = json.loads(path.read_text(encoding="utf-8"))
        return len(payload.get("past_context", "") or "")
    except (OSError, json.JSONDecodeError, KeyError, AttributeError):
        return None


def _fmt(value, spec: str = ".4f") -> str:
    return "n/a" if value is None else format(value, spec)


def _print_estimate(run_count: int, cost_log: str, per_run_s: float | None = None) -> None:
    """Estimate wall clock and spend from previous rows in the shared cost ledger.

    Only *analysis* rows count: settlement rows are a small fraction of a run's
    cost and averaging them in silently halves the projected wall clock.
    """
    if per_run_s:
        print(f"estimate      : ~{per_run_s * run_count / 60:.0f} min wall clock "
              f"(--est-per-run {per_run_s:.0f}s x {run_count})")
        return
    path = Path(cost_log)
    if not path.exists():
        print("estimate      : (no cost ledger yet — no basis for an estimate)")
        return
    rows = [r for r in csv.DictReader(path.open(encoding="utf-8"))
            if r.get("status") == "ok" and r.get("duration_s")
            and str(r.get("pass", "")).strip() != "settle"
            and "settle" not in str(r.get("category", ""))]
    if not rows:
        print("estimate      : (no successful analysis rows in the cost ledger yet)")
        return
    durations = [float(r["duration_s"]) for r in rows]
    costs = [float(r["cost_usd"]) for r in rows if r.get("cost_usd")]
    mean_s = sum(durations) / len(durations)
    mean_cost = (sum(costs) / len(costs)) if costs else None
    print(f"estimate      : ~{mean_s * run_count / 60:.0f} min wall clock "
          f"(based on {len(durations)} prior analysis run(s) averaging {mean_s:.0f}s)"
          + (f", ~${mean_cost * run_count:.2f}" if mean_cost is not None else ""))


def summarize(rows: list[dict], arms: list[str]) -> None:
    print("\n--- per arm ---")
    # NOTE: no ``mean_alpha`` column. Realized alpha is a property of the
    # SCENARIO, not of the arm — it is identical in both arms by construction
    # (measured: 0.0614 in each), so printing it per arm invites the false
    # reading "both arms performed the same". It is reported once, below.
    header = (f"{'arm':6} {'n':>3} {'hit':>7} {'inj_chars':>10} "
              f"{'mean_tok':>9} {'mean_$':>8} {'mean_s':>7} {'hold%':>6}")
    print(header)
    print("-" * len(header))
    for arm in arms:
        arows = [r for r in rows if r["arm"] == arm and r["status"] == "ok"]
        if not arows:
            print(f"{arm:6}   0   (no successful runs)")
            continue
        hits = [r["hit"] for r in arows if r["hit"] is not None]
        injected = [r["injected_chars"] for r in arows if r.get("injected_chars")]
        holds = sum(1 for r in arows if r["rating"] == "Hold")
        mean_hit = statistics.mean(hits) if hits else None
        mean_inj = statistics.mean(injected) if injected else None
        print(
            f"{arm:6} {len(arows):>3} {_fmt(mean_hit, '.2f'):>7} "
            f"{(f'{mean_inj:.0f}' if mean_inj is not None else 'n/a'):>10} "
            f"{statistics.mean([r['tokens_total'] for r in arows]):>9.0f} "
            f"{statistics.mean([r['cost_usd'] or 0 for r in arows]):>8.4f} "
            f"{statistics.mean([r['duration_s'] for r in arows]):>7.0f} "
            f"{100 * holds / len(arows):>5.0f}%"
        )

    alphas = [r["alpha"] for r in rows if r.get("alpha") is not None]
    if alphas:
        print(f"\n  mean realized alpha of these scenarios: {statistics.mean(alphas):+.4f}"
              "  (scenario property — arm-independent, NOT an arm-level return)")
    hits_all = [r["hit"] for r in rows if r.get("hit") is not None]
    holds_all = sum(1 for r in rows if r["rating"] == "Hold")
    if rows:
        # Printed even when there are ZERO hits: an all-Hold pilot yields no
        # scoreable observation at all, which is exactly the case where nobody
        # should be reading a hit rate off the table above.
        print(f"  scored observations (non-Hold, alpha known): {len(hits_all)} of {len(rows)}"
              f"   |  Hold (unscored): {holds_all}")
        if len(hits_all) < 10:
            print("  *** fewer than 10 scored observations — report effect sizes only,"
                  " do NOT claim significance ***")

    # Injected budget ratio: the deterministic quantity C2 reports. Labelled with
    # the arm names so it does not depend on the --arms ordering.
    per_arm_inj = {a: [r["injected_chars"] for r in rows
                       if r["arm"] == a and r.get("injected_chars")] for a in arms}
    if len(arms) == 2 and all(per_arm_inj.values()):
        a0, a1 = arms
        m0 = statistics.mean(per_arm_inj[a0])
        m1 = statistics.mean(per_arm_inj[a1])
        print(f"  injected budget: {a0}={m0:.0f} chars, {a1}={m1:.0f} chars"
              f"  -> {a0}/{a1} = {m0 / m1:.0%}")

    if len(arms) == 2 and rows:
        print("\n--- paired (same scenario + repetition) ---")
        keyed: dict[tuple, dict] = {}
        for r in rows:
            keyed.setdefault((r["ticker"], r["trade_date"], r["rep"]), {})[r["arm"]] = r
        both = {k: v for k, v in keyed.items() if all(a in v for a in arms)}
        print(f"  pairs with both arms completed : {len(both)}")
        if both:
            same = sum(1 for v in both.values()
                       if v[arms[0]]["rating"] == v[arms[1]]["rating"])
            print(f"  identical rating across arms   : {same}/{len(both)}")
            print(f"  scored pairs (both arms non-Hold): "
                  f"{sum(1 for v in both.values() if all(v[a]['hit'] is not None for a in arms))}")
            for key in sorted(both):
                a0, a1 = both[key][arms[0]], both[key][arms[1]]
                print(f"    {key[0]}@{key[1]} rep{key[2]}: "
                      f"{arms[0]}={a0['rating'] or '-'} ({a0['tokens_total']} tok, "
                      f"{a0.get('injected_chars') or '?'} inj) | "
                      f"{arms[1]}={a1['rating'] or '-'} ({a1['tokens_total']} tok, "
                      f"{a1.get('injected_chars') or '?'} inj)")


def main() -> int:
    args = parse_args()
    scenarios = json.loads(Path(args.scenarios).read_text(encoding="utf-8"))
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    analysts = [a.strip() for a in args.analysts.split(",") if a.strip()] if args.analysts else None

    log = TradingMemoryLog(DEFAULT_CONFIG)
    events = log.load_lessons()
    event_keys = {(e["ticker"], e["trade_date"]) for e in events}
    print(f"lessons store : {len(events)} EVENT(s) at {log._lessons_path}")
    for e in events:
        print(f"  - {e['entry_id']}  {e['trade_date']}  {e['ticker']}  alpha={e['alpha']}")

    # --- guard: LEAK --------------------------------------------------------
    # Two ways a scenario can be shown its own future:
    #   1. an EVENT for the exact (ticker, date) — its own outcome;
    #   2. an EVENT for the SAME TICKER dated on/after the scenario date — the
    #      prompt is now time-sliced (get_lessons_context(as_of=...)), so this
    #      is belt-and-braces, but it also catches a corpus that is temporally
    #      incompatible with the scenario (nothing usable to remember).
    events_by_ticker: dict[str, list[str]] = {}
    for e in events:
        events_by_ticker.setdefault(e["ticker"], []).append(str(e["trade_date"]))

    runnable = []
    for scen in scenarios:
        key = (scen["ticker"], str(scen["date"]))
        if args.allow_leak:
            runnable.append(scen)
            continue
        if key in event_keys:
            print(f"  [SKIP-LEAK] {key[0]} @ {key[1]}: an EVENT for this exact decision is "
                  f"in the store — the 'on' arm would be shown its own outcome.")
            continue
        usable = [d for d in events_by_ticker.get(key[0], []) if d < key[1]]
        blocked = [d for d in events_by_ticker.get(key[0], []) if d >= key[1]]
        if blocked:
            print(f"  [NOTE] {key[0]} @ {key[1]}: {len(blocked)} EVENT(s) for this ticker are "
                  f"dated on/after the scenario ({', '.join(sorted(blocked))}) and are "
                  f"time-sliced out of the prompt.")
        if not usable:
            print(f"  [WARN] {key[0]} @ {key[1]}: no usable same-ticker EVENT before this "
                  f"date — the 'on' arm sees only cross-ticker events (or nothing). "
                  f"Consider a later scenario date or earlier corpus events.")
        runnable.append(scen)

    # --- guard: EMPTY memory -------------------------------------------------
    if "on" in arms and not events and not args.force:
        print("\n[ABORT] the lessons store has no events, so the 'on' arm would be")
        print("        identical to 'off'. Build memory first, e.g.")
        print("          python scripts/run_pool.py --settle --only <TICKER>")
        print("          python scripts/settle_now.py --ticker <TICKER>")
        print("        (pass --force to run anyway)")
        return 2

    # --- guard: PENDING entries ---------------------------------------------
    tickers = {s["ticker"] for s in runnable}
    pending = [e for e in log.get_pending_entries() if e["ticker"] in tickers]
    if pending:
        listing = ", ".join(f"{e['date']}@{e['ticker']}" for e in pending)
        print(f"\n[WARN] {len(pending)} pending entry(ies) for these tickers: {listing}")
        print("       settling them adds one reflection call to whichever arm runs")
        print("       first; clear them with scripts/settle_now.py --ticker <TICKER>")

    Path(args.log).parent.mkdir(parents=True, exist_ok=True)

    # Resolve realized outcomes once per scenario (yfinance only, no LLM) and show
    # them BEFORE anything expensive runs — a scenario that cannot be scored
    # should be discovered now, not after 5 hours of calls.
    realized: dict[tuple[str, str], tuple] = {}
    print("\n--- scenarios ---")
    for scen in runnable:
        key = (scen["ticker"], str(scen["date"]))
        benchmark, raw, alpha, days = resolve_realized(
            scen["ticker"], key[1], args.holding_days, DEFAULT_CONFIG
        )
        realized[key] = (benchmark, raw, alpha, days)
        if raw is None:
            print(f"  {key[0]} @ {key[1]}: realized outcome UNAVAILABLE "
                  f"(rate-limited, too recent, or no data) - decisions cannot be scored")
        else:
            print(f"  {key[0]} @ {key[1]}: realized raw={raw:+.2%} alpha={alpha:+.2%} "
                  f"vs {benchmark} over {days}d")

    rows: list[dict] = []
    planned = [(s, r, a) for s in runnable for r in range(1, args.reps + 1) for a in arms]
    print(f"\nplanned runs  : {len(planned)} "
          f"({len(runnable)} scenario(s) x {args.reps} rep(s) x {len(arms)} arm(s))")
    _print_estimate(len(planned), args.cost_log, args.est_per_run)

    if args.dry_run:
        print("\n(dry-run: nothing executed)")
        return 0

    for scen in runnable:
        ticker, date = scen["ticker"], str(scen["date"])
        benchmark, raw, alpha, days = realized[(ticker, date)]
        print(f"\nscenario {ticker} @ {date}")

        # Re-check the LEAK guard here, not just once at start-up: the store is
        # allowed to grow between scenarios, and a scenario whose own outcome
        # became an EVENT must never reach the "on" arm.
        if not args.allow_leak:
            fresh = {(e["ticker"], e["trade_date"]) for e in log.load_lessons()}
            if (ticker, date) in fresh:
                print(f"  [SKIP-LEAK] an EVENT for {ticker} @ {date} appeared after the "
                      f"initial guards — refusing to score this scenario.")
                continue

        for rep in range(1, args.reps + 1):
            for arm in arms:
                config = DEFAULT_CONFIG.copy()
                config["event_memory_enabled"] = arm == "on"
                # FROZEN MEMORY — both arms read the same store and neither may
                # write to it. An analysis run otherwise appends a pending entry
                # for (ticker, date); the next run on that ticker settles it and
                # writes an L1 EVENT for the scenario's OWN decision, which the
                # "on" arm of the next rep would then be shown (self-leak).
                config["memory_readonly"] = True
                label = f"{ticker}@{date} [{arm}] rep{rep}"
                print(f"  {label} - starting", flush=True)
                started_at = datetime.now(timezone.utc).isoformat()
                with cost.Heartbeat(label, interval=args.heartbeat):
                    _, _, stats = cost.measure_run(
                        ticker, date, "stock", config=config,
                        selected_analysts=analysts, debug=args.debug, progress=args.progress,
                    )
                rows.append({
                    "ticker": ticker,
                    "trade_date": date,
                    "arm": arm,
                    "rep": rep,
                    "rating": stats["rating"],
                    "benchmark": benchmark,
                    "raw": raw,
                    "alpha": alpha,
                    "hit": hit_of(stats["rating"], alpha),
                    "status": stats["status"],
                    "error": stats["error"],
                    "injected_chars": injected_chars(ticker, date),
                    "tokens_total": stats["tokens_total"],
                    "cost_usd": stats["cost_usd"],
                    "duration_s": stats["duration_s"],
                })
                cost.append_csv(
                    args.cost_log,
                    cost.make_csv_row(ticker, date, "stock", started_at, stats,
                                      extra={"category": f"experiment_{arm}_rep{rep}",
                                             "pass": "analysis"}),
                    extra_columns=["category", "pass"],
                )
                print(f"    -> {stats['rating'] or '-'} | {stats['tokens_total']} tok | "
                      f"{stats['duration_s']}s | {stats['status']}")

    if rows:
        with open(args.log, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nexperiment rows written: {args.log}")

    summarize(rows, arms)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
