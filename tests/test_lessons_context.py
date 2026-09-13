"""Tests for the L1 lessons injection path (step 5)."""

from tradingagents.agents.utils.memory import TradingMemoryLog
from tradingagents.graph.trading_graph import TradingAgentsGraph


def _make_log(tmp_path, protect_threshold=0.10):
    config = {
        "memory_log_path": str(tmp_path / "trading_memory.md"),
        "lessons_path": str(tmp_path / "trading_lessons.md"),
        "event_protect_threshold": protect_threshold,
        "max_event_entries": 200,
    }
    return TradingMemoryLog(config)


def _store(log, eid, ticker, date, alpha, summary):
    log.store_event(eid, ticker, date, "Buy", alpha, alpha - 0.01, summary, f"p-{eid}")


# --- render round-trip -------------------------------------------------------

def test_render_event_entry_roundtrip(tmp_path):
    log = _make_log(tmp_path)
    _store(log, "E-1", "MSFT", "2026-07-23", 0.31, "Line one.\nLine two.")
    e = log.load_lessons()[0]
    rendered = log._render_event_entry(e)
    reparsed = log._parse_lessons_entry(rendered)
    for key in ("entry_id", "ticker", "trade_date", "rating", "alpha", "raw", "summary", "protected"):
        assert reparsed[key] == e[key]


def test_format_pct_none(tmp_path):
    log = _make_log(tmp_path)
    assert log._format_pct(None) == "n/a"


# --- get_lessons_context -----------------------------------------------------

def test_get_lessons_context_order_and_selection(tmp_path):
    log = _make_log(tmp_path)
    _store(log, "E-1", "MSFT", "2026-07-20", 0.10, "older same-ticker")
    _store(log, "E-2", "MSFT", "2026-07-23", 0.31, "newer same-ticker")
    _store(log, "E-3", "NVDA", "2026-03-30", 0.19, "cross-ticker")

    ctx = log.get_lessons_context("MSFT", n_same=2, n_cross=1)

    assert "newer same-ticker" in ctx
    assert "older same-ticker" in ctx
    assert "cross-ticker" in ctx
    assert "Past extreme events for MSFT" in ctx
    assert "Recent cross-ticker extreme events" in ctx
    # newest same-ticker event appears before the older one
    assert ctx.index("newer same-ticker") < ctx.index("older same-ticker")
    # event summaries must not leak the full-process pointer
    assert "p-E-" not in ctx

    # access metadata bumped for all three selected entries
    reloaded = {e["entry_id"]: e for e in log.load_lessons()}
    assert reloaded["E-1"]["hits"] == 1
    assert reloaded["E-2"]["hits"] == 1
    assert reloaded["E-3"]["hits"] == 1


def test_get_lessons_context_empty(tmp_path):
    log = _make_log(tmp_path)
    assert log.get_lessons_context("MSFT") == ""


def test_get_rules_context_empty(tmp_path):
    log = _make_log(tmp_path)
    assert log.get_rules_context() == ""


# --- _build_past_context (three-segment composition) -------------------------

def _make_graph(tmp_path):
    # `config` is required: _build_past_context consults the ablation switch
    # event_memory_enabled (default True when absent, but a real graph always
    # carries a config).
    config = {
        "memory_log_path": str(tmp_path / "trading_memory.md"),
        "lessons_path": str(tmp_path / "trading_lessons.md"),
        "event_protect_threshold": 0.10,
        "max_event_entries": 200,
    }
    inst = object.__new__(TradingAgentsGraph)
    inst.config = config
    inst.memory_log = _make_log(tmp_path)
    return inst


def test_build_past_context_events_only(tmp_path):
    inst = _make_graph(tmp_path)
    _store(inst.memory_log, "E-1", "MSFT", "2026-07-23", 0.31, "summary text")
    ctx = TradingAgentsGraph._build_past_context(inst, "MSFT", as_of="2026-12-31")
    assert "summary text" in ctx
    assert "Past extreme events for MSFT" in ctx


def test_build_past_context_legacy_fallback(tmp_path):
    inst = _make_graph(tmp_path)
    inst.memory_log.store_decision("MSFT", "2026-01-05", "Rating: Buy\nBuy")
    inst.memory_log.update_with_outcome("MSFT", "2026-01-05", 0.05, 0.02, 5, "Good call.")
    ctx = TradingAgentsGraph._build_past_context(inst, "MSFT", as_of="2026-12-31")
    assert "Good call." in ctx
    assert "Past analyses of MSFT" in ctx


def test_build_past_context_l1_replaces_the_legacy_segment(tmp_path):
    """Budget-substitutive arms: with L1 on, the legacy segment is REPLACED.

    Adding L1 on top of legacy would hand the "on" arm ~50–90% more context, so
    the C2 claim ("same budget, better selection") could not be tested.
    """
    inst = _make_graph(tmp_path)
    _store(inst.memory_log, "E-1", "MSFT", "2026-07-23", 0.31, "summary text")
    inst.memory_log.store_decision("MSFT", "2026-01-05", "Rating: Buy\nBuy")
    inst.memory_log.update_with_outcome("MSFT", "2026-01-05", 0.05, 0.02, 5, "Good call.")

    ctx = TradingAgentsGraph._build_past_context(inst, "MSFT", as_of="2026-12-31")

    assert "summary text" in ctx
    assert "Good call." not in ctx          # legacy segment not added on top
    assert "Past analyses of MSFT" not in ctx
