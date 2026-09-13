"""Tests for frozen-memory mode (``memory_readonly``).

The paired A/B harness must observe an immutable memory store. Without this,
each analysis run appends a *pending* decision entry, the next run on the same
ticker settles it, and ``_maybe_write_event`` turns the scenario's OWN outcome
into an L1 event that the next "on"-arm rep is shown — a self-inflicted leak
the start-of-run guards cannot catch.

Contract: every write path is a no-op; every read path still works.
"""

from unittest.mock import MagicMock

import pytest

from tradingagents.agents.utils.memory import TradingMemoryLog
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph


def _paths(tmp_path):
    return {
        "memory_log_path": str(tmp_path / "trading_memory.md"),
        "lessons_path": str(tmp_path / "trading_lessons.md"),
        "distill_queue_path": str(tmp_path / "trading_distill_queue.md"),
        "event_alpha_threshold": 0.05,
        "event_protect_threshold": 0.10,
        "event_summary_max_chars": 1600,
        "max_event_entries": 200,
        "min_events_per_direction": 3,
    }


def _log(tmp_path, readonly: bool) -> TradingMemoryLog:
    cfg = _paths(tmp_path)
    cfg["memory_readonly"] = readonly
    return TradingMemoryLog(cfg)


def _seed_event(log, ticker="NVDA", date="2026-01-05"):
    log.store_event("E-1", ticker, date, "Buy", 0.31, 0.30, "frozen summary", "p")


# --- config plumbing ---------------------------------------------------------

def test_default_config_is_writable():
    assert DEFAULT_CONFIG["memory_readonly"] is False


def test_freeze_switch_is_not_env_overridable():
    """A stray .env line must not be able to freeze memory store-wide.

    Frozen mode fails *silently* — runs still succeed, they just record
    nothing — so the switch is reachable only from code (the A/B harness).
    """
    from tradingagents.default_config import _ENV_OVERRIDES

    assert "memory_readonly" not in _ENV_OVERRIDES.values()
    assert not any("READONLY" in var or "FROZEN" in var for var in _ENV_OVERRIDES)


# --- write paths are no-ops --------------------------------------------------

def test_store_decision_is_noop(tmp_path):
    log = _log(tmp_path, readonly=True)

    log.store_decision("NVDA", "2026-01-05", "Rating: Buy")

    assert log.get_pending_entries() == []


def test_store_event_is_noop(tmp_path):
    log = _log(tmp_path, readonly=True)

    _seed_event(log)

    assert log.load_lessons() == []


def test_update_paths_are_noop(tmp_path):
    log = _log(tmp_path, readonly=True)
    log.store_decision("NVDA", "2026-01-05", "Rating: Buy")

    log.update_with_outcome("NVDA", "2026-01-05", 0.05, 0.02, 5, "reflection")
    log.batch_update_with_outcomes([{
        "ticker": "NVDA", "trade_date": "2026-01-05", "raw_return": 0.05,
        "alpha_return": 0.02, "holding_days": 5, "reflection": "reflection",
    }])

    assert log.load_entries() == []


def test_maintain_memory_is_noop(tmp_path):
    """Maintenance must not rewrite or queue anything in frozen mode."""
    log = _log(tmp_path, readonly=True)
    for i in range(5):
        log.store_event(f"E-{i}", "NVDA", f"2026-01-0{i + 1}", "Buy", 0.2, 0.2, "s", "p")

    log.maintain_memory()

    assert log.load_lessons() == []
    queue = tmp_path / "trading_distill_queue.md"
    assert not queue.exists() or queue.read_text(encoding="utf-8") == ""


# --- settlement is skipped ---------------------------------------------------

def _graph(tmp_path, readonly: bool):
    cfg = _paths(tmp_path)
    cfg["memory_readonly"] = readonly
    inst = object.__new__(TradingAgentsGraph)
    inst.config = cfg
    inst.memory_log = TradingMemoryLog(cfg)
    inst.reflector = MagicMock()
    inst.reflector.reflect_on_final_decision.return_value = "reflection"
    inst._resolve_benchmark = MagicMock(return_value="SPY")
    inst._fetch_returns = MagicMock(return_value=(0.10, 0.20, 5))
    inst._full_state_path = MagicMock(return_value=tmp_path / "full_states_log.json")
    inst.postmortem_agent = MagicMock(return_value="postmortem")
    return inst


def test_settlement_skipped_in_readonly(tmp_path):
    """A pending entry is neither reflected nor turned into an EVENT."""
    inst = _graph(tmp_path, readonly=True)
    inst.memory_log.store_decision("NVDA", "2026-01-05", "Rating: Buy")

    TradingAgentsGraph._resolve_pending_entries(inst, "NVDA")

    inst.reflector.reflect_on_final_decision.assert_not_called()
    inst.postmortem_agent.assert_not_called()
    assert inst.memory_log.get_pending_entries() == []  # nothing was written at all
    assert inst.memory_log.load_lessons() == []


def test_settlement_still_runs_when_writable(tmp_path):
    """Control: the same call settles and writes when the store is writable."""
    inst = _graph(tmp_path, readonly=False)
    inst.memory_log.store_decision("NVDA", "2026-01-05", "Rating: Buy")

    TradingAgentsGraph._resolve_pending_entries(inst, "NVDA")

    inst.reflector.reflect_on_final_decision.assert_called_once()
    events = inst.memory_log.load_lessons()
    assert len(events) == 1
    assert events[0]["alpha"] == pytest.approx(0.20)
    assert not inst.memory_log.get_pending_entries()  # the entry was settled


# --- read paths still work ---------------------------------------------------

def test_reads_work_against_a_store_that_was_frozen(tmp_path):
    """What the harness relies on: an existing store is readable but immutable."""
    writable = _log(tmp_path, readonly=False)
    _seed_event(writable)
    writable.store_decision("NVDA", "2026-01-05", "Rating: Buy\nBuy")
    writable.update_with_outcome("NVDA", "2026-01-05", 0.05, 0.02, 5, "Good call.")

    frozen = _log(tmp_path, readonly=True)
    assert len(frozen.load_lessons()) == 1
    assert len(frozen.load_entries()) == 1
    assert "frozen summary" in frozen.get_lessons_context("NVDA")
    assert "Good call." in frozen.get_past_context("NVDA")


# --- GET paths must not write, even though they normally do ------------------

def test_frozen_read_does_not_rewrite_the_store(tmp_path):
    """``get_lessons_context`` bumps hit counters and rewrites the file.

    Eviction priority is access-based (``_recency_key`` sorts on
    last_accessed/hits first), so an experiment read that rewrote the store
    would silently change which events survive future maintenance.
    """
    writable = _log(tmp_path, readonly=False)
    _seed_event(writable)
    path = tmp_path / "trading_lessons.md"
    before = path.read_bytes()

    frozen = _log(tmp_path, readonly=True)
    assert "frozen summary" in frozen.get_lessons_context("NVDA")   # read works
    assert path.read_bytes() == before                             # …write did not


def test_writable_read_does_rewrite_the_store(tmp_path):
    """Control: without the guard the same read bumps hits on disk."""
    log = _log(tmp_path, readonly=False)
    _seed_event(log)
    path = tmp_path / "trading_lessons.md"
    before = path.read_bytes()

    log.get_lessons_context("NVDA")

    assert path.read_bytes() != before
    assert log.load_lessons()[0]["hits"] == 1
