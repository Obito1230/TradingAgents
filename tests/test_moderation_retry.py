"""Tests for the provider content-moderation retry in NormalizedChatOpenAI.

Zhipu GLM can answer a normal request with HTTP 400 + a contentFilter payload
(error code 1301) when the *generated* text trips server-side moderation. That
used to abort a whole multi-minute analysis run; the client now retries a few
times first (TRADINGAGENTS_MODERATION_RETRIES controls the budget).
"""

import pytest
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

from tradingagents.llm_clients.openai_client import (
    NormalizedChatOpenAI,
    is_moderation_error,
)

ZHIPU_FILTER_400 = (
    "Error code: 400 - {'contentFilter': [{'level': 1, 'role': 'assistant'}], "
    "'error': {'code': '1301', 'message': '系统检测到输入或生成内容可能包含不安全或敏感内容'}}"
)


def _make_llm():
    return NormalizedChatOpenAI(
        model="glm-5.3-flash", api_key="test", base_url="https://example.invalid/v1"
    )


def test_is_moderation_error_matches_zhipu_payload():
    assert is_moderation_error(Exception(ZHIPU_FILTER_400))


def test_is_moderation_error_ignores_other_errors():
    assert not is_moderation_error(ValueError("rate limit"))
    assert not is_moderation_error(Exception("Error code: 500 - server error"))


def test_moderation_error_is_retried(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_MODERATION_RETRY_DELAY", "0")
    calls = {"n": 0}

    def fake_invoke(self, input, config=None, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise Exception(ZHIPU_FILTER_400)
        return AIMessage(content="ok")

    monkeypatch.setattr(ChatOpenAI, "invoke", fake_invoke)
    out = _make_llm().invoke("hi")

    assert out.content == "ok"
    assert calls["n"] == 2


def test_moderation_retries_disabled_by_env(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_MODERATION_RETRIES", "0")
    monkeypatch.setenv("TRADINGAGENTS_MODERATION_RETRY_DELAY", "0")
    calls = {"n": 0}

    def fake_invoke(self, input, config=None, **kwargs):
        calls["n"] += 1
        raise Exception(ZHIPU_FILTER_400)

    monkeypatch.setattr(ChatOpenAI, "invoke", fake_invoke)
    with pytest.raises(Exception):
        _make_llm().invoke("hi")

    assert calls["n"] == 1


def test_non_moderation_error_not_retried(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_MODERATION_RETRY_DELAY", "0")
    calls = {"n": 0}

    def fake_invoke(self, input, config=None, **kwargs):
        calls["n"] += 1
        raise ValueError("boom")

    monkeypatch.setattr(ChatOpenAI, "invoke", fake_invoke)
    with pytest.raises(ValueError):
        _make_llm().invoke("hi")

    assert calls["n"] == 1
