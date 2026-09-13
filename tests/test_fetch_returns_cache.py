"""Tests for cache-first price lookup in ``_fetch_returns``.

Scoring and settlement both go through ``_fetch_returns``. Reading the local
OHLCV cache first makes a backtest reproducible and keeps it working when
yfinance throttles the bare ``Ticker.history`` path (which it does, hard, after
a few dozen calls — that throttling silently stops both scoring and event
creation).
"""

import pandas as pd
import pytest

from tradingagents.graph.trading_graph import TradingAgentsGraph

TRADE_DATE = "2026-07-13"


def _frame(closes):
    dates = pd.date_range("2026-07-13", periods=len(closes), freq="D")
    return pd.DataFrame({"Date": dates, "Close": closes})


def _inst():
    return object.__new__(TradingAgentsGraph)


def _patch_cache(monkeypatch, frames):
    """Serve ``frames[symbol]`` from the cache path; raise for unknown symbols."""
    def fake_load_ohlcv(symbol, curr_date):
        if symbol not in frames:
            raise RuntimeError(f"no cached data for {symbol}")
        return frames[symbol]

    monkeypatch.setattr(
        "tradingagents.dataflows.stockstats_utils.load_ohlcv", fake_load_ohlcv
    )


def _patch_live(monkeypatch, frames, record=None):
    class _FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, start=None, end=None):
            if record is not None:
                record.append(self.symbol)
            if self.symbol not in frames:
                raise RuntimeError(f"no live data for {self.symbol}")
            return frames[self.symbol]

    import tradingagents.graph.trading_graph as tg

    monkeypatch.setattr(tg.yf, "Ticker", _FakeTicker)


# --- the cache path ----------------------------------------------------------

def test_cache_path_computes_raw_and_alpha(monkeypatch):
    _patch_cache(monkeypatch, {
        "NVDA": _frame([100, 101, 102, 103, 104, 105]),   # +5.0%
        "SPY": _frame([200, 201, 202, 203, 204, 205]),    # +2.5%
    })

    raw, alpha, days = TradingAgentsGraph._fetch_returns(_inst(), "NVDA", TRADE_DATE, 5, "SPY")

    assert days == 5
    assert raw == pytest.approx(0.05)
    assert alpha == pytest.approx(0.025)


def test_cache_hit_never_touches_the_network(monkeypatch):
    """The whole point: a cached window must cost zero live calls."""
    _patch_cache(monkeypatch, {
        "NVDA": _frame([100, 105]), "SPY": _frame([200, 202]),
    })
    called = []
    _patch_live(monkeypatch, {}, record=called)

    raw, alpha, days = TradingAgentsGraph._fetch_returns(_inst(), "NVDA", TRADE_DATE, 5, "SPY")

    assert called == [], f"live path was used despite a cache hit: {called}"
    assert raw == pytest.approx(0.05)


def test_actual_days_respects_the_shorter_series(monkeypatch):
    _patch_cache(monkeypatch, {
        "NVDA": _frame([100, 101, 102]),   # only 3 bars -> 2 steps
        "SPY": _frame([200, 201, 202, 203, 204, 205]),
    })

    raw, alpha, days = TradingAgentsGraph._fetch_returns(_inst(), "NVDA", TRADE_DATE, 5, "SPY")

    assert days == 2
    assert raw == pytest.approx(0.02)


# --- fallback ----------------------------------------------------------------

def test_falls_back_to_live_when_the_cache_misses(monkeypatch):
    _patch_cache(monkeypatch, {"SPY": _frame([200, 201, 202, 203, 204, 205])})  # NVDA not cached
    called = []
    _patch_live(monkeypatch, {"NVDA": _frame([100, 101, 102, 103, 104, 110])}, record=called)

    raw, alpha, days = TradingAgentsGraph._fetch_returns(_inst(), "NVDA", TRADE_DATE, 5, "SPY")

    assert called == ["NVDA"]
    assert raw == pytest.approx(0.10)
    assert alpha == pytest.approx(0.075)


def test_falls_back_when_the_cache_returns_too_few_bars(monkeypatch):
    _patch_cache(monkeypatch, {
        "NVDA": _frame([100]),                                    # unusable
        "SPY": _frame([200, 201, 202, 203, 204, 205]),
    })
    _patch_live(monkeypatch, {"NVDA": _frame([100, 105])})

    raw, alpha, days = TradingAgentsGraph._fetch_returns(_inst(), "NVDA", TRADE_DATE, 5, "SPY")

    assert days == 1
    assert raw == pytest.approx(0.05)


def test_both_paths_failing_returns_none(monkeypatch):
    _patch_cache(monkeypatch, {})
    _patch_live(monkeypatch, {})

    assert TradingAgentsGraph._fetch_returns(_inst(), "NVDA", TRADE_DATE, 5, "SPY") == (
        None, None, None)


# --- the two paths must agree ------------------------------------------------

def test_cache_and_live_paths_agree(monkeypatch):
    """Only the source changes; the numbers must not."""
    stock = _frame([100, 102, 99, 104, 108, 111])
    bench = _frame([200, 199, 201, 203, 204, 206])

    _patch_cache(monkeypatch, {"NVDA": stock, "SPY": bench})
    from_cache = TradingAgentsGraph._fetch_returns(_inst(), "NVDA", TRADE_DATE, 5, "SPY")

    _patch_cache(monkeypatch, {})           # force the live path
    _patch_live(monkeypatch, {"NVDA": stock, "SPY": bench})
    from_live = TradingAgentsGraph._fetch_returns(_inst(), "NVDA", TRADE_DATE, 5, "SPY")

    assert from_cache == pytest.approx(from_live)


def test_zero_entry_price_is_not_divided_by(monkeypatch):
    _patch_cache(monkeypatch, {"NVDA": _frame([0.0, 5.0]), "SPY": _frame([200, 202])})

    assert TradingAgentsGraph._fetch_returns(_inst(), "NVDA", TRADE_DATE, 5, "SPY") == (
        None, None, None)
