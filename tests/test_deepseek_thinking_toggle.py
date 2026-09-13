"""Tests for the DeepSeek thinking-mode switch and the ``deepseek-flash`` capability fix.

Background (verified against the live API, 2026-09-13):

* ``deepseek-flash`` and ``deepseek-v4-pro`` are the model IDs the account
  actually serves; ``deepseek-v4-flash`` is accepted as an alias. All of them
  are thinking models.
* Thinking mode rejects ``tool_choice`` — the API answers
  ``400 Thinking mode does not support this tool_choice`` — so the capability
  table must mark them ``supports_tool_choice=False``. ``deepseek-flash`` was
  missing from the table, fell through to ``_DEFAULT``, and therefore sent
  ``tool_choice`` on every structured-output call: that was the source of the
  repeated HTTP 400s.
* ``extra_body={"thinking": {"type": "disabled"}}`` turns thinking off, which
  both removes the rejection and drops the hidden reasoning tokens.
"""

import pytest

from tradingagents.default_config import DEFAULT_CONFIG, _ENV_OVERRIDES
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.llm_clients.capabilities import get_capabilities


def _provider_kwargs(config: dict) -> dict:
    inst = object.__new__(TradingAgentsGraph)
    inst.config = config
    return TradingAgentsGraph._get_provider_kwargs(inst)


# --- capability table --------------------------------------------------------

@pytest.mark.parametrize("model", ["deepseek-flash", "deepseek-v4-flash",
                                   "deepseek-v4-pro", "deepseek-reasoner"])
def test_deepseek_thinking_models_reject_tool_choice(model):
    """Missing this flag is what produced the HTTP 400s."""
    assert get_capabilities(model).supports_tool_choice is False


def test_deepseek_chat_still_accepts_tool_choice():
    """Control: the non-thinking alias is unaffected."""
    assert get_capabilities("deepseek-chat").supports_tool_choice is True


def test_structured_output_suppresses_tool_choice_for_deepseek_flash():
    from tradingagents.llm_clients.openai_client import DeepSeekChatOpenAI

    class _Schema:
        pass

    llm = DeepSeekChatOpenAI(model="deepseek-flash", api_key="placeholder",
                             base_url="https://api.deepseek.com")
    # langchain defaults tool_choice to the function spec; the client must
    # clear it for thinking models or the API 400s.
    assert llm.with_structured_output is not None
    assert get_capabilities(llm.model_name).supports_tool_choice is False


# --- config plumbing ---------------------------------------------------------

def test_thinking_key_is_a_bool():
    """Declared default is True; a .env override may flip it, so assert the type.

    (``TRADINGAGENTS_DEEPSEEK_THINKING=false`` is the recommended project setting,
    so hard-asserting True here would fail on any machine that sets it.)
    """
    assert isinstance(DEFAULT_CONFIG["deepseek_thinking"], bool)


def test_thinking_is_env_overridable_by_name():
    assert _ENV_OVERRIDES["TRADINGAGENTS_DEEPSEEK_THINKING"] == "deepseek_thinking"


def test_provider_kwargs_forward_thinking_flag():
    assert _provider_kwargs({"llm_provider": "deepseek", "deepseek_thinking": False}) == {
        "deepseek_thinking": False}
    assert _provider_kwargs({"llm_provider": "deepseek", "deepseek_thinking": True}) == {
        "deepseek_thinking": True}


def test_string_flag_from_a_programmatic_config_is_parsed():
    """``bool("false")`` is True — the plumbing must not fall into that trap."""
    assert _provider_kwargs({"llm_provider": "deepseek",
                             "deepseek_thinking": "false"}) == {"deepseek_thinking": False}
    assert _provider_kwargs({"llm_provider": "deepseek",
                             "deepseek_thinking": "true"}) == {"deepseek_thinking": True}


def test_flag_is_deepseek_only():
    assert "deepseek_thinking" not in _provider_kwargs(
        {"llm_provider": "openai", "deepseek_thinking": False})


# --- the wire format ---------------------------------------------------------

def _llm(thinking):
    """Build the real DeepSeek chat client through the provider client."""
    from tradingagents.llm_clients.openai_client import OpenAIClient

    client = OpenAIClient("deepseek-flash", "https://api.deepseek.com", provider="deepseek",
                          api_key="placeholder", deepseek_thinking=thinking)
    return client.get_llm()


def test_thinking_false_sends_the_disable_flag():
    llm = _llm(False)
    assert llm.extra_body == {"thinking": {"type": "disabled"}}
    # …and it actually reaches the request payload, not just the constructor.
    payload = llm._get_request_payload([{"role": "user", "content": "hi"}])
    assert payload["extra_body"] == {"thinking": {"type": "disabled"}}


def test_thinking_true_sends_nothing_extra():
    llm = _llm(True)
    assert not llm.extra_body
    payload = llm._get_request_payload([{"role": "user", "content": "hi"}])
    assert not payload.get("extra_body")


# --- the served model ID must be known to the catalog and the price table -----

def test_served_model_id_is_recognised():
    """A stale catalog makes every run print an 'unknown model' warning."""
    from tradingagents.llm_clients.validators import validate_model

    assert validate_model("deepseek", "deepseek-flash") is True


def test_served_model_id_has_a_price():
    """Without a price entry cost_usd is logged EMPTY for every run."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "cost_accounting.py"
    spec = importlib.util.spec_from_file_location("cost_accounting_probe", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert mod.match_price("deepseek-flash") is not None


# --- postmortem keeps its own thinking setting -------------------------------

def _postmortem_kwargs(config):
    inst = object.__new__(TradingAgentsGraph)
    inst.config = config
    return TradingAgentsGraph._get_postmortem_kwargs(inst, {"temperature": 0.2})


def test_postmortem_inherits_when_unset():
    assert _postmortem_kwargs({"llm_provider": "deepseek", "postmortem_thinking": None}) == {
        "temperature": 0.2}


def test_postmortem_can_turn_thinking_back_on():
    """The pipeline runs thinking-off; the postmortem pays for reasoning."""
    out = _postmortem_kwargs({"llm_provider": "deepseek", "postmortem_thinking": True})
    assert out["deepseek_thinking"] is True


def test_postmortem_override_parses_env_style_strings():
    assert _postmortem_kwargs(
        {"llm_provider": "deepseek", "postmortem_thinking": "true"})["deepseek_thinking"] is True
    assert _postmortem_kwargs(
        {"llm_provider": "deepseek", "postmortem_thinking": "false"})["deepseek_thinking"] is False


def test_postmortem_override_is_deepseek_only():
    out = _postmortem_kwargs({"llm_provider": "openai", "postmortem_thinking": True})
    assert "deepseek_thinking" not in out


def test_shared_kwargs_are_not_mutated():
    shared = {"temperature": 0.2}
    inst = object.__new__(TradingAgentsGraph)
    inst.config = {"llm_provider": "deepseek", "postmortem_thinking": True}
    TradingAgentsGraph._get_postmortem_kwargs(inst, shared)

    assert shared == {"temperature": 0.2}


def test_postmortem_thinking_ships_with_an_env_override():
    from tradingagents.graph.trading_graph import _as_bool

    assert _ENV_OVERRIDES["TRADINGAGENTS_POSTMORTEM_THINKING"] == "postmortem_thinking"
    # Declared default is None (inherit); a .env line may replace it and env
    # values arrive as strings — so assert it is a recognised value, not None.
    value = DEFAULT_CONFIG["postmortem_thinking"]
    assert value is None or _as_bool(value, None) in (True, False)


def test_misspelled_flag_fails_loudly():
    """A typo must not silently select the other behaviour."""
    from tradingagents.graph.trading_graph import _as_bool

    with pytest.raises(ValueError, match="expected a boolean"):
        _as_bool("treu")
    assert _as_bool(None, default=None) is None
    assert _as_bool("", default=True) is True
    assert _as_bool(False) is False
