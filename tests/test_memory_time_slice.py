"""Time-slicing: a decision must never be shown material dated on/after it.

Without this, building memory and then back-testing on earlier dates leaks
hindsight: the "memory on" arm wins by clairvoyance rather than by using
memory. Both the L1 event store and the legacy decision log are filtered, so
the two A/B arms still differ only in *which* memory they read.
"""

import pytest

from tradingagents.agents.utils.memory import TradingMemoryLog
from tradingagents.graph.trading_graph import TradingAgentsGraph


def _config(tmp_path, **over):
    config = {
        "memory_log_path": str(tmp_path / "trading_memory.md"),
        "lessons_path": str(tmp_path / "trading_lessons.md"),
        "distill_queue_path": str(tmp_path / "trading_distill_queue.md"),
        "results_dir": str(tmp_path / "results"),
        "event_alpha_threshold": 0.05,
        "event_protect_threshold": 0.10,
        "event_summary_max_chars": 1600,
        "max_event_entries": 200,
        "min_events_per_direction": 3,
        "event_memory_enabled": True,
    }
    config.update(over)
    return config


def _seeded(tmp_path):
    log = TradingMemoryLog(_config(tmp_path))
    log.store_event("E-old", "NVDA", "2026-01-05", "Buy", 0.31, 0.30, "OLD summary", "p")
    log.store_event("E-new", "NVDA", "2026-06-15", "Sell", -0.22, -0.21, "NEW summary", "p")
    log.store_decision("NVDA", "2026-01-05", "Rating: Buy\nBuy")
    log.update_with_outcome("NVDA", "2026-01-05", 0.31, 0.30, 5, "OLD reflection")
    log.store_decision("NVDA", "2026-06-15", "Rating: Sell\nSell")
    log.update_with_outcome("NVDA", "2026-06-15", -0.22, -0.21, 5, "NEW reflection")
    return log


# --- L1 event store ----------------------------------------------------------

def test_events_after_the_decision_date_are_excluded(tmp_path):
    log = _seeded(tmp_path)

    ctx = log.get_lessons_context("NVDA", as_of="2026-03-01")

    assert "OLD summary" in ctx
    assert "NEW summary" not in ctx


def test_event_on_the_decision_date_is_excluded(tmp_path):
    """Same-day material is not yet knowable when the decision is made."""
    log = _seeded(tmp_path)

    ctx = log.get_lessons_context("NVDA", as_of="2026-06-15")

    assert "NEW summary" not in ctx


def test_no_as_of_keeps_previous_behaviour(tmp_path):
    """Backwards compatibility: callers that pass no date are unfiltered."""
    log = _seeded(tmp_path)

    ctx = log.get_lessons_context("NVDA")

    assert "OLD summary" in ctx and "NEW summary" in ctx


def test_both_directions_respect_the_cutoff(tmp_path):
    """The per-direction floor must not smuggle a future loss back in."""
    log = _seeded(tmp_path)

    ctx = log.get_lessons_context("NVDA", as_of="2026-03-01")

    assert "Sell" not in ctx and "NEW summary" not in ctx


# --- the cutoff must survive non-string date types ---------------------------

def test_datetime_cutoff_still_excludes_the_same_day(tmp_path):
    """A datetime renders as '2026-06-15 00:00:00', which sorts AFTER the same
    calendar day as a plain date string — comparing without truncating would
    silently re-admit same-day material."""
    from datetime import datetime

    log = _seeded(tmp_path)

    ctx = log.get_lessons_context("NVDA", as_of=datetime(2026, 6, 15, 0, 0, 0))

    assert "NEW summary" not in ctx
    assert "OLD summary" in ctx


def test_date_object_cutoff_works(tmp_path):
    from datetime import date

    log = _seeded(tmp_path)

    ctx = log.get_lessons_context("NVDA", as_of=date(2026, 3, 1))

    assert "OLD summary" in ctx and "NEW summary" not in ctx


def test_legacy_log_handles_datetime_cutoff(tmp_path):
    from datetime import datetime

    log = _seeded(tmp_path)

    ctx = log.get_past_context("NVDA", as_of=datetime(2026, 6, 15, 12, 30))

    assert "OLD reflection" in ctx and "NEW reflection" not in ctx


# --- a sliced READ must never delete what it filters out ---------------------

def test_a_sliced_read_does_not_delete_filtered_events(tmp_path):
    """Regression: get_lessons_context rewrites the store to bump hit counters.

    Handing that rewrite the time-sliced subset DELETED every event dated
    on/after ``as_of`` — a read that destroyed exactly the data it was excluding
    from the prompt.
    """
    log = _seeded(tmp_path)

    log.get_lessons_context("NVDA", as_of="2026-03-01")   # slices out the 06-15 event
    log.get_lessons_context("NVDA", as_of="2025-01-01")   # slices out everything

    ids = sorted(e["entry_id"] for e in log.load_lessons())
    assert ids == ["E-new", "E-old"]


def test_other_tickers_survive_a_sliced_read(tmp_path):
    log = _seeded(tmp_path)
    log.store_event("E-amd", "AMD", "2026-06-20", "Buy", 0.25, 0.24, "AMD summary", "p")

    log.get_lessons_context("NVDA", as_of="2026-03-01")

    assert any(e["entry_id"] == "E-amd" for e in log.load_lessons())


def test_rules_context_accepts_as_of(tmp_path):
    """L3's injection point must take the decision date too.

    v1 returns nothing, but the parameter has to exist now — otherwise the L3
    implementation injects rules distilled *after* the decision it informs,
    repeating the hindsight bug one layer up.
    """
    log = TradingMemoryLog(_config(tmp_path))

    assert log.get_rules_context(as_of="2026-03-01") == ""
    assert log.get_rules_context() == ""


def test_sliced_read_bumps_hits_without_losing_entries(tmp_path):
    """The hit counter still works on the in-window entries."""
    log = _seeded(tmp_path)

    log.get_lessons_context("NVDA", as_of="2026-03-01")

    by_id = {e["entry_id"]: e for e in log.load_lessons()}
    assert by_id["E-old"]["hits"] == 1
    assert by_id["E-new"]["hits"] == 0


# --- legacy decision log -----------------------------------------------------

def test_legacy_log_is_time_sliced_too(tmp_path):
    log = _seeded(tmp_path)

    ctx = log.get_past_context("NVDA", as_of="2026-03-01")

    assert "OLD reflection" in ctx
    assert "NEW reflection" not in ctx


# --- the assembled prompt ----------------------------------------------------

def _graph(tmp_path):
    config = _config(tmp_path)
    inst = object.__new__(TradingAgentsGraph)
    inst.config = config
    inst.memory_log = TradingMemoryLog(config)
    return inst


def test_build_past_context_hides_future_material(tmp_path):
    inst = _graph(tmp_path)
    _seeded(tmp_path)  # same files, written through a throwaway handle

    ctx = TradingAgentsGraph._build_past_context(inst, "NVDA", as_of="2026-03-01")

    assert "OLD summary" in ctx
    assert "NEW summary" not in ctx
    # L1 is on and non-empty, so the legacy segment is replaced (not added):
    assert "OLD reflection" not in ctx
    assert "NEW reflection" not in ctx


def test_nothing_before_the_decision_yields_no_memory(tmp_path):
    """A scenario dated before the whole corpus has nothing to inject at all —
    neither L1 nor the legacy fallback."""
    inst = _graph(tmp_path)
    _seeded(tmp_path)

    ctx = TradingAgentsGraph._build_past_context(inst, "NVDA", as_of="2025-12-01")

    assert ctx == ""


def test_l1_empty_falls_back_to_legacy(tmp_path):
    """No L1 event in the window → the legacy segment is used instead, so an arm
    is never left with no memory at all (this can only dilute an effect, never
    inflate it)."""
    config = _config(tmp_path)
    log = TradingMemoryLog(config)
    log.store_decision("NVDA", "2026-01-05", "Rating: Buy\nBuy")
    log.update_with_outcome("NVDA", "2026-01-05", 0.005, 0.002, 5, "tiny call")
    inst = object.__new__(TradingAgentsGraph)
    inst.config = config
    inst.memory_log = log

    ctx = TradingAgentsGraph._build_past_context(inst, "NVDA", as_of="2026-06-01")

    assert "tiny call" in ctx
    assert log.load_lessons() == []          # no L1 event exists, hence the fallback
    assert "Past extreme events" not in ctx


def test_as_of_is_required(tmp_path):
    """Omitting it must fail loudly, not fall back to an unfiltered read.

    An optional ``as_of`` is how the hindsight leak would come back: a new
    caller forgets the argument and silently gets memory from the future.
    """
    inst = _graph(tmp_path)
    _seeded(tmp_path)

    with pytest.raises(TypeError):
        TradingAgentsGraph._build_past_context(inst, "NVDA")


def test_as_of_must_be_keyword(tmp_path):
    """Positional passing is rejected, so `_build_past_context(t, d)` can't drift."""
    inst = _graph(tmp_path)

    with pytest.raises(TypeError):
        TradingAgentsGraph._build_past_context(inst, "NVDA", "2026-03-01")


# --- entry_id idempotency ----------------------------------------------------

def test_second_event_with_the_same_id_is_not_written(tmp_path):
    """Re-running an already-settled (ticker, date) must not duplicate the event."""
    log = TradingMemoryLog(_config(tmp_path))
    log.store_event("E-1", "NVDA", "2026-01-05", "Buy", 0.31, 0.30, "first", "p")
    log.store_event("E-1", "NVDA", "2026-01-05", "Hold", 0.31, 0.30, "second", "p")

    events = log.load_lessons()
    assert len(events) == 1
    assert events[0]["rating"] == "Buy" and "first" in events[0]["summary"]


def test_maintenance_repairs_a_store_that_already_has_duplicate_ids(tmp_path):
    """Stores written before the guard existed must be repairable."""
    log = TradingMemoryLog(_config(tmp_path))
    log.store_event("E-1", "NVDA", "2026-01-05", "Buy", 0.31, 0.30, "first", "p")
    # Forge the pre-guard damage: two events sharing an entry_id.
    log.store_event("E-2", "AMD", "2026-02-05", "Buy", 0.20, 0.20, "other", "p")
    path = tmp_path / "trading_lessons.md"
    path.write_text(path.read_text(encoding="utf-8").replace(
        "E-2", "E-1", 1), encoding="utf-8")

    log.maintain_memory()

    events = log.load_lessons()
    assert len(events) == 1
    assert events[0]["entry_id"] == "E-1"
    # The removed duplicate is buffered for L3 distillation, not silently lost.
    queue = tmp_path / "trading_distill_queue.md"
    assert "other" in queue.read_text(encoding="utf-8")


def test_distinct_events_with_different_ids_survive(tmp_path):
    """Control: the repair must not merge genuinely different decisions."""
    log = TradingMemoryLog(_config(tmp_path))
    log.store_event("E-1", "NVDA", "2026-01-05", "Buy", 0.31, 0.30, "a", "p")
    log.store_event("E-2", "NVDA", "2026-01-05", "Hold", 0.31, 0.30, "b", "p")

    log.maintain_memory()

    assert len(log.load_lessons()) == 2
