"""Tests for the L1 postmortem agent + the settlement hook that fires it."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tradingagents.agents.schemas import EventPostmortem, render_event_postmortem
from tradingagents.agents.utils.memory import TradingMemoryLog
from tradingagents.agents.utils.postmortem_agent import _build_prompt, create_postmortem_agent
from tradingagents.graph.trading_graph import TradingAgentsGraph


# --- schema render -----------------------------------------------------------

def test_render_event_postmortem_headers():
    pm = EventPostmortem(
        decision_summary="Bought on earnings strength.",
        key_basis="Strong margins and momentum.",
        missed_factors="Valuation stretch ignored.",
        transferable_lesson="Check valuation vs sector peers before entry.",
        self_critique="Overweighted momentum, underweighted valuation.",
    )
    text = render_event_postmortem(pm)
    for header in ("Decision Summary", "Key Basis", "Missed Factors", "Transferable Lesson", "Self-critique"):
        assert f"**{header}**" in text


# --- prompt build ------------------------------------------------------------

def test_build_prompt_includes_debate_and_outcome():
    state = {
        "trade_date": "2026-07-23",
        "company_of_interest": "MSFT",
        "market_report": "Market report text.",
        "sentiment_report": "",
        "news_report": "",
        "fundamentals_report": "",
        "investment_debate_state": {"history": "Bull: ...\nBear: ..."},
        "investment_plan": "Buy with 6% cap.",
        "trader_investment_decision": "FINAL TRANSACTION PROPOSAL: **BUY**",
        "risk_debate_state": {"history": "Aggressive: ...\nConservative: ..."},
        "final_trade_decision": "Rating: Buy",
    }
    prompt = _build_prompt(state, "Rating: Buy", 0.31, 0.28, "SPY")
    assert "+31.0%" in prompt
    assert "+28.0%" in prompt
    assert "Bull: ..." in prompt
    assert "Aggressive: ..." in prompt
    assert "Market report text." in prompt


# --- agent factory: structured path + freetext fallback -----------------------

def test_agent_freetext_fallback_when_structured_unavailable():
    llm = MagicMock()
    llm.with_structured_output.side_effect = NotImplementedError
    llm.invoke.return_value = SimpleNamespace(content="plain postmortem text")
    agent = create_postmortem_agent(llm)
    out = agent({}, "Rating: Buy", 0.31, 0.28, "SPY")
    assert out == "plain postmortem text"


def test_agent_structured_path_renders_markdown():
    llm = MagicMock()
    structured = MagicMock()
    structured.invoke.return_value = EventPostmortem(
        decision_summary="Bought on earnings strength.",
        key_basis="Momentum.",
        missed_factors="Valuation.",
        transferable_lesson="Check peers.",
        self_critique="Overweight momentum.",
    )
    llm.with_structured_output.return_value = structured
    agent = create_postmortem_agent(llm)
    out = agent({}, "Rating: Buy", 0.31, 0.28, "SPY")
    assert "**Decision Summary**" in out


# --- settlement hook ---------------------------------------------------------

def _make_graph_instance(tmp_path, alpha=0.08):
    results = tmp_path / "results"
    config = {
        "memory_log_path": str(tmp_path / "trading_memory.md"),
        "lessons_path": str(tmp_path / "trading_lessons.md"),
        "event_alpha_threshold": 0.05,
        "event_protect_threshold": 0.10,
        "results_dir": str(results),
    }
    inst = object.__new__(TradingAgentsGraph)
    inst.config = config
    inst.memory_log = TradingMemoryLog(config)
    inst.reflector = MagicMock()
    inst.reflector.reflect_on_final_decision.return_value = "reflection"
    inst.postmortem_agent = MagicMock(return_value="postmortem summary")
    inst._fetch_returns = MagicMock(return_value=(0.10, alpha, 5))
    inst._resolve_benchmark = MagicMock(return_value="SPY")
    return inst


def _seed_full_state(results, ticker, date):
    d = results / ticker / "TradingAgentsStrategy_logs"
    d.mkdir(parents=True, exist_ok=True)
    payload = {"company_of_interest": ticker, "trade_date": date, "final_trade_decision": "Rating: Buy"}
    (d / f"full_states_log_{date}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_resolve_pending_writes_event_on_extreme_alpha(tmp_path):
    inst = _make_graph_instance(tmp_path, alpha=0.08)
    inst.memory_log.store_decision("NVDA", "2026-01-05", "Rating: Buy\nBuy")
    _seed_full_state(tmp_path / "results", "NVDA", "2026-01-05")

    TradingAgentsGraph._resolve_pending_entries(inst, "NVDA")

    events = inst.memory_log.load_lessons()
    assert len(events) == 1
    assert events[0]["summary"] == "postmortem summary"
    assert events[0]["ticker"] == "NVDA"
    assert events[0]["alpha"] == pytest.approx(0.08)
    # alpha 0.08 < protect threshold 0.10 → not protected
    assert events[0]["protected"] is False
    # decision log entry must be resolved (no pending left)
    assert inst.memory_log.get_pending_entries() == []


def test_resolve_pending_skips_event_below_threshold(tmp_path):
    inst = _make_graph_instance(tmp_path, alpha=0.02)
    inst.memory_log.store_decision("NVDA", "2026-01-05", "Rating: Buy\nBuy")
    _seed_full_state(tmp_path / "results", "NVDA", "2026-01-05")

    TradingAgentsGraph._resolve_pending_entries(inst, "NVDA")

    assert inst.memory_log.load_lessons() == []  # no event, still reflected
    assert inst.memory_log.get_pending_entries() == []


def test_event_still_written_when_postmortem_generation_fails(tmp_path):
    """Provider moderation (or any generation failure) must not drop the event."""
    inst = _make_graph_instance(tmp_path, alpha=0.12)
    inst.postmortem_agent = MagicMock(side_effect=RuntimeError("contentFilter 1301"))
    inst.memory_log.store_decision("NVDA", "2026-01-05", "Rating: Buy\nBuy")
    _seed_full_state(tmp_path / "results", "NVDA", "2026-01-05")

    TradingAgentsGraph._resolve_pending_entries(inst, "NVDA")

    events = inst.memory_log.load_lessons()
    assert len(events) == 1
    assert "unavailable" in events[0]["summary"]
    assert events[0]["alpha"] == pytest.approx(0.12)
    assert events[0]["protected"] is True  # 0.12 >= protect threshold 0.10
    # settlement still completes
    assert inst.memory_log.get_pending_entries() == []
