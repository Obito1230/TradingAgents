"""Tests for the L1 lessons store (EVENT entries) added to TradingMemoryLog."""

from tradingagents.agents.utils.memory import TradingMemoryLog

_SEP = TradingMemoryLog._SEPARATOR


def make_lessons_log(tmp_path, protect_threshold=0.10, filename="trading_lessons.md"):
    config = {
        "lessons_path": str(tmp_path / filename),
        "event_protect_threshold": protect_threshold,
        "max_event_entries": 200,
    }
    return TradingMemoryLog(config), tmp_path / filename


def test_store_event_roundtrip(tmp_path):
    log, path = make_lessons_log(tmp_path)
    log.store_event(
        entry_id="E-1",
        ticker="MSFT",
        trade_date="2026-07-23",
        rating="Buy",
        alpha=0.31,
        raw=0.30,
        summary="Decision: entered on earnings strength. Missed the valuation stretch.",
        context_pointer="results/MSFT/TradingAgentsStrategy_logs/full_states_log_2026-07-23.json",
        fingerprint="0.02,1,55,1.2,0.03",
    )
    entries = log.load_lessons()
    assert len(entries) == 1
    e = entries[0]
    assert e["kind"] == "EVENT"
    assert e["entry_id"] == "E-1"
    assert e["ticker"] == "MSFT"
    assert e["trade_date"] == "2026-07-23"
    assert e["rating"] == "Buy"
    assert e["alpha"] == 0.31
    assert e["raw"] == 0.30
    assert e["summary"].startswith("Decision:")
    assert e["context_pointer"].endswith("full_states_log_2026-07-23.json")
    assert e["fingerprint"] == "0.02,1,55,1.2,0.03"
    assert e["hits"] == 0
    assert e["last_accessed"] == "never"
    # alpha 0.31 >= protect threshold 0.10 → protected
    assert e["protected"] is True
    # The raw file must NOT contain the full summary duplicated into a pointer-less blob.
    assert "CONTEXT_POINTER:" in path.read_text(encoding="utf-8")


def test_protected_threshold_boundary(tmp_path):
    log, _ = make_lessons_log(tmp_path, protect_threshold=0.10)
    log.store_event("E-1", "A", "2026-01-01", "Buy", alpha=0.02, raw=0.01, summary="s", context_pointer="p")
    log.store_event("E-2", "A", "2026-01-02", "Buy", alpha=0.15, raw=0.14, summary="s", context_pointer="p")
    entries = {e["entry_id"]: e for e in log.load_lessons()}
    assert entries["E-1"]["protected"] is False
    assert entries["E-2"]["protected"] is True


def test_multiline_summary_preserved(tmp_path):
    log, _ = make_lessons_log(tmp_path)
    log.store_event("E-1", "MSFT", "2026-07-23", "Buy", 0.31, 0.30,
                    "Line one.\nLine two.", "p")
    e = log.load_lessons()[0]
    assert e["summary"] == "Line one.\nLine two."


def test_skips_non_event_tags(tmp_path):
    log, path = make_lessons_log(tmp_path)
    # A decision-log entry (or a future RULE entry) must not be parsed as an EVENT.
    decision_entry = "[2026-01-01 | MSFT | Buy | +1.0% | +0.5% | 5d]\n\nDECISION:\nBuy" + _SEP
    path.write_text(decision_entry, encoding="utf-8")
    log.store_event("E-1", "MSFT", "2026-07-23", "Buy", 0.31, 0.30, "s", "p")
    entries = log.load_lessons()
    assert [e["kind"] for e in entries] == ["EVENT"]
    assert len(entries) == 1


def test_load_lessons_missing_file(tmp_path):
    log, _ = make_lessons_log(tmp_path)
    assert log.load_lessons() == []


def test_store_event_no_lessons_path(tmp_path):
    log = TradingMemoryLog({"memory_log_path": str(tmp_path / "trading_memory.md")})
    log.store_event("E-1", "MSFT", "2026-07-23", "Buy", 0.31, 0.30, "s", "p")
    # No lessons path configured → no-op, nothing created.
    assert log.load_lessons() == []
