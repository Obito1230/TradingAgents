"""Tests for the declared external-source disable switch (config['disabled_sources']).

Turning a source off must (a) skip its network calls, (b) drop it from tool lists
AND prompts so the model never advertises a call to it, and (c) leave the rest of
the pipeline untouched. Used to hold the input surface constant across experiment
arms when a source is unusable (e.g. A-share runs).
"""

import importlib
import types
from unittest.mock import MagicMock

from langchain_core.runnables import RunnableLambda

import tradingagents.default_config as default_config_module
from tradingagents.agents.analysts.news_analyst import create_news_analyst
from tradingagents.agents.utils.agent_utils import is_source_disabled
from tradingagents.dataflows.config import set_config
from tradingagents.graph.trading_graph import TradingAgentsGraph


def _reload_with_env(monkeypatch, **overrides):
    for key in list(default_config_module._ENV_OVERRIDES):
        monkeypatch.delenv(key, raising=False)
    for key, val in overrides.items():
        monkeypatch.setenv(key, val)
    return importlib.reload(default_config_module)


# --- config plumbing ---------------------------------------------------------

def test_env_parses_comma_separated_list(monkeypatch):
    dc = _reload_with_env(monkeypatch, TRADINGAGENTS_DISABLED_SOURCES="social, macro")
    assert dc.DEFAULT_CONFIG["disabled_sources"] == ["social", "macro"]


def test_default_is_empty(monkeypatch):
    dc = _reload_with_env(monkeypatch)
    assert dc.DEFAULT_CONFIG["disabled_sources"] == []


def test_is_source_disabled_reads_global_config():
    set_config({"disabled_sources": ["social", "MACRO"]})
    assert is_source_disabled("social") is True
    assert is_source_disabled("macro") is True  # case-insensitive
    assert is_source_disabled("prediction_markets") is False


def test_is_source_disabled_accepts_comma_string():
    set_config({"disabled_sources": "social,macro"})
    assert is_source_disabled("macro") is True


# --- news analyst: tools + prompt -------------------------------------------

def _news_state():
    return {
        "company_of_interest": "300750.SZ",
        "trade_date": "2026-03-04",
        "asset_type": "stock",
        "messages": [],
    }


def _bind_stub(captured):
    """Stub for llm.bind_tools: records the tool list and returns a real Runnable.

    A MagicMock would be coerced to RunnableLambda and called directly, so the
    chain result must come from an actual runnable.
    """
    def _bind(tools):
        captured["tools"] = tools
        return RunnableLambda(
            lambda _prompt: types.SimpleNamespace(tool_calls=[], content="report")
        )

    return _bind


def test_news_analyst_drops_disabled_tools():
    set_config({"disabled_sources": ["macro", "prediction_markets"]})
    captured = {}
    llm = MagicMock()
    llm.bind_tools.side_effect = _bind_stub(captured)

    out = create_news_analyst(llm)(_news_state())

    names = {t.name for t in captured["tools"]}
    assert names == {"get_news", "get_global_news"}
    assert out["news_report"] == "report"


def test_news_analyst_keeps_all_tools_by_default():
    set_config({"disabled_sources": []})
    captured = {}
    llm = MagicMock()
    llm.bind_tools.side_effect = _bind_stub(captured)

    create_news_analyst(llm)(_news_state())

    assert {t.name for t in captured["tools"]} == {
        "get_news", "get_global_news", "get_macro_indicators", "get_prediction_markets"
    }


# --- graph tool node --------------------------------------------------------

def test_news_tool_node_drops_disabled_tools():
    set_config({"disabled_sources": ["macro", "prediction_markets"]})
    nodes = TradingAgentsGraph._create_tool_nodes(object.__new__(TradingAgentsGraph))

    node = nodes["news"]
    names = set(getattr(node, "tools_by_name", {}).keys())
    if not names:  # older langgraph layouts
        names = {t.name for t in getattr(node, "tools", [])}

    assert "get_macro_indicators" not in names
    assert "get_prediction_markets" not in names
    assert {"get_news", "get_global_news", "get_insider_transactions"} <= names


# --- sentiment analyst: no social network calls -----------------------------

def test_sentiment_analyst_skips_social_fetches_when_disabled(monkeypatch):
    import tradingagents.agents.analysts.sentiment_analyst as sa

    set_config({"disabled_sources": ["social"]})
    called = []
    monkeypatch.setattr(
        sa, "fetch_stocktwits_messages", lambda *a, **k: called.append("stocktwits") or "st"
    )
    monkeypatch.setattr(
        sa, "fetch_reddit_posts", lambda *a, **k: called.append("reddit") or "rd"
    )
    monkeypatch.setattr(sa, "get_news", types.SimpleNamespace(func=lambda *a, **k: "news block"))

    llm = MagicMock()
    llm.with_structured_output.side_effect = NotImplementedError  # force free-text path
    llm.invoke.return_value = types.SimpleNamespace(content="sentiment report")

    node = sa.create_sentiment_analyst(llm)
    out = node({"company_of_interest": "300750.SZ", "trade_date": "2026-03-04", "messages": []})

    assert called == [], "disabled social sources must not be fetched"
    assert out["sentiment_report"] == "sentiment report"
