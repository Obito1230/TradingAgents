"""Tests for the ablation switch ``event_memory_enabled`` (the A/B "memory off" arm).

With the switch off, the L1 layer must neither READ (no L1 segment in
past_context) nor WRITE (no EVENT at settlement). The legacy decision-log
reflections stay in both arms, so the only difference between arms is L1.
"""

from unittest.mock import MagicMock

import pytest

from tradingagents.agents.utils.memory import TradingMemoryLog
from tradingagents.graph.trading_graph import TradingAgentsGraph


def _instance(tmp_path, enabled: bool):
    config = {
        "memory_log_path": str(tmp_path / "trading_memory.md"),
        "lessons_path": str(tmp_path / "trading_lessons.md"),
        "results_dir": str(tmp_path / "results"),
        "event_alpha_threshold": 0.05,
        "event_protect_threshold": 0.10,
        "event_summary_max_chars": 1600,
        "event_memory_enabled": enabled,
    }
    inst = object.__new__(TradingAgentsGraph)
    inst.config = config
    inst.memory_log = TradingMemoryLog(config)
    return inst


def _seed_legacy_resolved(log):
    log.store_decision("NVDA", "2026-01-05", "Rating: Buy\nBuy")
    log.update_with_outcome("NVDA", "2026-01-05", 0.05, 0.02, 5, "Good call.")


# --- READ side ---------------------------------------------------------------

def test_l1_segment_injected_when_enabled(tmp_path):
    inst = _instance(tmp_path, enabled=True)
    inst.memory_log.store_event("E-1", "NVDA", "2026-01-05", "Buy", 0.31, 0.30, "summary text", "p")

    ctx = TradingAgentsGraph._build_past_context(inst, "NVDA", as_of="2026-12-31")

    assert "summary text" in ctx
    assert "Past extreme events for NVDA" in ctx


def test_l1_segment_omitted_when_disabled(tmp_path):
    inst = _instance(tmp_path, enabled=False)
    inst.memory_log.store_event("E-1", "NVDA", "2026-01-05", "Buy", 0.31, 0.30, "summary text", "p")

    ctx = TradingAgentsGraph._build_past_context(inst, "NVDA", as_of="2026-12-31")

    assert "summary text" not in ctx
    assert "Past extreme events" not in ctx


def test_legacy_reflections_present_in_both_arms(tmp_path):
    """The baseline injection must not change between arms (clean comparison)."""
    for enabled in (True, False):
        inst = _instance(tmp_path / str(enabled), enabled=enabled)
        inst.config["memory_log_path"] = str(tmp_path / str(enabled) / "trading_memory.md")
        inst.config["lessons_path"] = str(tmp_path / str(enabled) / "trading_lessons.md")
        inst.memory_log = TradingMemoryLog(inst.config)
        _seed_legacy_resolved(inst.memory_log)

        ctx = TradingAgentsGraph._build_past_context(inst, "NVDA", as_of="2026-12-31")
        assert "Good call." in ctx
        assert "Past analyses of NVDA" in ctx


# --- WRITE side --------------------------------------------------------------

def test_no_event_written_when_disabled(tmp_path):
    inst = _instance(tmp_path, enabled=False)
    inst.postmortem_agent = MagicMock(return_value="should not be called")
    entry = {"date": "2026-01-05", "decision": "Rating: Buy", "rating": "Buy"}

    TradingAgentsGraph._maybe_write_event(inst, entry, "NVDA", 0.10, 0.20, 5, "SPY")

    assert inst.memory_log.load_lessons() == []
    inst.postmortem_agent.assert_not_called()  # no wasted LLM call in the off arm


def test_event_written_when_enabled(tmp_path):
    inst = _instance(tmp_path, enabled=True)
    inst.postmortem_agent = MagicMock(return_value="postmortem summary")
    entry = {"date": "2026-01-05", "decision": "Rating: Buy", "rating": "Buy"}

    TradingAgentsGraph._maybe_write_event(inst, entry, "NVDA", 0.10, 0.20, 5, "SPY")

    events = inst.memory_log.load_lessons()
    assert len(events) == 1
    assert events[0]["alpha"] == pytest.approx(0.20)
