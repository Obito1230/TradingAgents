"""Tests for the node-level progress trace (``--progress`` / ``progress=True``).

The graph is silent between LLM calls, so a multi-minute run looks frozen.
With ``progress=True``, ``_run_graph`` streams ``"updates"`` alongside
``"values"``: each updates chunk is emitted when a node FINISHES, so the gap
between two chunks is that node's wall time, while values still carries the
authoritative full state.
"""

from unittest.mock import MagicMock

from tradingagents.graph.trading_graph import TradingAgentsGraph


class _FakeGraph:
    """Yields the tuple sequence LangGraph produces for ['updates','values']."""

    def __init__(self, final: dict | None = None):
        self.calls: list[dict] = []
        self._final = final or {
            "messages": [],
            "market_report": "x",
            "trader_investment_plan": "y",
            "final_trade_decision": "Rating: Buy",
        }

    def stream(self, state, **kwargs):
        self.calls.append(kwargs)
        yield ("values", {"messages": []})
        yield ("updates", {"Market Analyst": {"market_report": "x"}})
        yield ("values", {"messages": [], "market_report": "x"})
        yield ("updates", {"Trader": {"trader_investment_plan": "y"}})
        yield ("values", self._final)


def _instance(tmp_path):
    inst = object.__new__(TradingAgentsGraph)
    inst.debug = False
    inst.progress = True
    inst.ticker = "MSFT"
    inst.config = {"results_dir": str(tmp_path), "checkpoint_enabled": False}
    inst.log_states_dict = {}
    inst.memory_log = MagicMock()
    inst.memory_log.get_rules_context.return_value = ""
    inst.memory_log.get_lessons_context.return_value = ""
    inst.memory_log.get_past_context.return_value = ""
    inst.propagator = MagicMock()
    inst.propagator.create_initial_state.return_value = {"messages": []}
    inst.propagator.get_graph_args.return_value = {
        "stream_mode": "values",
        "config": {"recursion_limit": 100},
    }
    inst.signal_processor = MagicMock()
    inst.signal_processor.process_signal.return_value = "Buy"
    # Network-touching helpers and disk logging are stubbed out.
    inst.resolve_instrument_context = MagicMock(return_value="ctx")
    inst._log_state = MagicMock()
    inst.graph = _FakeGraph()
    return inst


def test_progress_prints_one_line_per_completed_node(tmp_path, capsys):
    inst = _instance(tmp_path)
    TradingAgentsGraph._run_graph(inst, "MSFT", "2026-07-23")

    out = capsys.readouterr().out
    assert "[progress] Market Analyst:" in out
    assert "[progress] Trader:" in out


def test_progress_overrides_stream_mode_without_duplicating_it(tmp_path):
    inst = _instance(tmp_path)
    TradingAgentsGraph._run_graph(inst, "MSFT", "2026-07-23")

    kwargs = inst.graph.calls[0]
    assert kwargs["stream_mode"] == ["updates", "values"]
    # A duplicate stream_mode kwarg would raise TypeError inside LangGraph.
    assert kwargs["config"] == {"recursion_limit": 100}


def test_progress_returns_full_final_state_and_records_decision(tmp_path):
    inst = _instance(tmp_path)
    state, decision = TradingAgentsGraph._run_graph(inst, "MSFT", "2026-07-23")

    assert state["final_trade_decision"] == "Rating: Buy"
    assert decision == "Buy"
    inst.memory_log.store_decision.assert_called_once()


def test_without_flags_the_graph_is_invoked_normally(tmp_path):
    inst = _instance(tmp_path)
    inst.progress = False
    inst.graph = MagicMock()
    inst.graph.invoke.return_value = {"final_trade_decision": "Rating: Hold"}
    inst.signal_processor.process_signal.return_value = "Hold"

    state, decision = TradingAgentsGraph._run_graph(inst, "MSFT", "2026-07-23")

    inst.graph.invoke.assert_called_once()
    assert decision == "Hold"


def test_progress_flag_is_stored_on_the_graph(tmp_path, mock_llm_client):
    from tradingagents.default_config import DEFAULT_CONFIG

    config = dict(DEFAULT_CONFIG)
    config["results_dir"] = str(tmp_path)
    config["data_cache_dir"] = str(tmp_path)

    ta = TradingAgentsGraph(debug=False, config=config, progress=True)
    assert ta.progress is True
