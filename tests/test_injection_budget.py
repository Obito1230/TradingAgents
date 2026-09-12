"""Tests for the injection-budget cap, reflection language, and injection audit.

Three fixes for the L1 memory layer:

1. summaries are hard-capped so one event cannot blow the prompt budget
   (up to n_same summaries are re-injected into every future run);
2. the legacy reflection follows ``output_language`` like every other agent, so
   injected memory does not mix languages;
3. the saved run state records the exact ``past_context`` the PM received, which
   is the audit trail proving the memory layer was actually read.
"""

import json

import pytest

from tradingagents.agents.utils.memory import TradingMemoryLog
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.reflection import Reflector
from tradingagents.graph.trading_graph import TradingAgentsGraph


def _make_log(tmp_path, max_chars=100):
    config = {
        "lessons_path": str(tmp_path / "trading_lessons.md"),
        "memory_log_path": str(tmp_path / "trading_memory.md"),
        "event_protect_threshold": 0.10,
        "max_event_entries": 200,
        "event_summary_max_chars": max_chars,
    }
    return TradingMemoryLog(config), tmp_path / "trading_lessons.md"


# --- 1. summary cap ----------------------------------------------------------

def test_store_event_truncates_over_long_summary(tmp_path):
    log, _ = _make_log(tmp_path, max_chars=50)
    log.store_event("E-1", "MSFT", "2026-07-23", "Buy", 0.31, 0.30, "x" * 500, "p")

    summary = log.load_lessons()[0]["summary"]
    assert len(summary) <= 50 + len("\n...[truncated]")
    assert summary.endswith("...[truncated]")


def test_short_summary_is_not_touched(tmp_path):
    log, _ = _make_log(tmp_path, max_chars=200)
    log.store_event("E-1", "MSFT", "2026-07-23", "Buy", 0.31, 0.30, "short summary", "p")
    assert log.load_lessons()[0]["summary"] == "short summary"


def test_injection_truncates_legacy_over_long_entry(tmp_path):
    """Entries written before the cap existed are capped at injection time too."""
    log, path = _make_log(tmp_path, max_chars=40)
    legacy = (
        "[EVENT | E-legacy | 2026-01-05 | MSFT | Buy | +5.0% | +4.0%]\n\n"
        "SUMMARY:\n" + "y" * 400 + "\n\nCONTEXT_POINTER: p\n"
        "LAST_ACCESSED: never  |  HITS: 0  |  PROTECTED: false"
    )
    path.write_text(legacy + TradingMemoryLog._SEPARATOR, encoding="utf-8")

    ctx = log.get_lessons_context("MSFT")
    assert "...[truncated]" in ctx
    assert "y" * 400 not in ctx


def test_default_cap_comes_from_config():
    assert DEFAULT_CONFIG["event_summary_max_chars"] == 1600
    assert TradingMemoryLog(DEFAULT_CONFIG)._summary_max_chars == 1600


# --- 2. reflection language --------------------------------------------------

def test_reflection_follows_output_language():
    from unittest.mock import MagicMock

    from tradingagents.dataflows.config import set_config

    llm = MagicMock()
    llm.invoke.return_value.content = "反思"
    reflector = Reflector(llm)

    set_config({"output_language": "English"})
    reflector.reflect_on_final_decision("Rating: Buy", 0.05, 0.02)
    english_prompt = llm.invoke.call_args[0][0][0][1]
    assert "Write your entire response in" not in english_prompt

    set_config({"output_language": "Chinese"})
    reflector.reflect_on_final_decision("Rating: Buy", 0.05, 0.02)
    chinese_prompt = llm.invoke.call_args[0][0][0][1]
    assert "Write your entire response in Chinese" in chinese_prompt


# --- 3. injection audit trail ------------------------------------------------

def test_log_state_records_injected_past_context(tmp_path):
    config = dict(DEFAULT_CONFIG)
    config["results_dir"] = str(tmp_path / "logs")

    inst = object.__new__(TradingAgentsGraph)
    inst.config = config
    inst.ticker = "MSFT"
    inst.log_states_dict = {}

    final_state = {
        "company_of_interest": "MSFT",
        "trade_date": "2026-07-23",
        "market_report": "m",
        "sentiment_report": "s",
        "news_report": "n",
        "fundamentals_report": "f",
        "investment_debate_state": {
            "bull_history": "", "bear_history": "", "history": "",
            "current_response": "", "judge_decision": "",
        },
        "trader_investment_plan": "t",
        "risk_debate_state": {
            "aggressive_history": "", "conservative_history": "",
            "neutral_history": "", "history": "", "judge_decision": "",
        },
        "investment_plan": "i",
        "final_trade_decision": "Rating: Buy",
        "past_context": "[2026-07-23 | MSFT | Buy | +31.0%] some memory",
    }

    TradingAgentsGraph._log_state(inst, "2026-07-23", final_state)

    log_path = (
        tmp_path / "logs" / "MSFT" / "TradingAgentsStrategy_logs"
        / "full_states_log_2026-07-23.json"
    )
    payload = json.loads(log_path.read_text(encoding="utf-8"))
    assert payload["past_context"] == "[2026-07-23 | MSFT | Buy | +31.0%] some memory"
