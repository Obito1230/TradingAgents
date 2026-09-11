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
    inst = object.__new__(TradingAgentsGraph)
    inst.memory_log = _make_log(tmp_path)
    return inst


def test_build_past_context_events_only(tmp_path):
    inst = _make_graph(tmp_path)
    _store(inst.memory_log, "E-1", "MSFT", "2026-07-23", 0.31, "summary text")
    ctx = TradingAgentsGraph._build_past_context(inst, "MSFT")
    assert "summary text" in ctx
    assert "Past extreme events for MSFT" in ctx


def test_build_past_context_legacy_fallback(tmp_path):
    inst = _make_graph(tmp_path)
    inst.memory_log.store_decision("MSFT", "2026-01-05", "Rating: Buy\nBuy")
    inst.memory_log.update_with_outcome("MSFT", "2026-01-05", 0.05, 0.02, 5, "Good call.")
    ctx = TradingAgentsGraph._build_past_context(inst, "MSFT")
    assert "Good call." in ctx
    assert "Past analyses of MSFT" in ctx


def test_build_past_context_combines_segments(tmp_path):
    inst = _make_graph(tmp_path)
    _store(inst.memory_log, "E-1", "MSFT", "2026-07-23", 0.31, "summary text")
    inst.memory_log.store_decision("MSFT", "2026-01-05", "Rating: Buy\nBuy")
    inst.memory_log.update_with_outcome("MSFT", "2026-01-05", 0.05, 0.02, 5, "Good call.")
    ctx = TradingAgentsGraph._build_past_context(inst, "MSFT")
    # events segment (L1) precedes the legacy reflection segment
    assert ctx.index("summary text") < ctx.index("Good call.")
