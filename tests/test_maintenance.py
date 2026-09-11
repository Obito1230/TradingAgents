"""Tests for maintain_memory (step 6): distill queue, protection symmetry, direction floor, dedupe."""

from tradingagents.agents.utils.memory import TradingMemoryLog


def _make_log(tmp_path, max_event_entries=10, min_per_direction=3, protect=0.10):
    config = {
        "lessons_path": str(tmp_path / "trading_lessons.md"),
        "distill_queue_path": str(tmp_path / "trading_distill_queue.md"),
        "event_protect_threshold": protect,
        "max_event_entries": max_event_entries,
        "min_events_per_direction": min_per_direction,
    }
    return TradingMemoryLog(config)


def _store(log, eid, ticker, date, alpha, rating="Buy"):
    log.store_event(eid, ticker, date, rating, alpha, alpha - 0.01, f"summary-{eid}", f"p-{eid}")


def _ids(log):
    return {e["entry_id"] for e in log.load_lessons()}


# --- protection is symmetric: big losses are also never evicted ---------------

def test_protected_loss_never_evicted(tmp_path):
    log = _make_log(tmp_path, max_event_entries=1, min_per_direction=0, protect=0.10)
    _store(log, "L", "MSFT", "2026-01-05", -0.15)  # |alpha| 0.15 >= 0.10 → protected
    _store(log, "W", "MSFT", "2026-02-05", 0.05)   # unprotected
    log.maintain_memory()
    assert "L" in _ids(log)
    assert "W" not in _ids(log)


# --- direction floor keeps a bear lesson amid a long bull streak ---------------

def test_direction_floor_preserves_loss(tmp_path):
    log = _make_log(tmp_path, max_event_entries=3, min_per_direction=1, protect=0.10)
    _store(log, "L", "MSFT", "2026-01-05", -0.02)  # old loss, unprotected
    for i in range(5):
        _store(log, f"W{i}", "MSFT", f"2026-02-{i + 1:02d}", 0.05)  # wins, unprotected
    log.maintain_memory()
    ids = _ids(log)
    assert "L" in ids           # loss kept by the floor despite being oldest
    assert len(ids) == 3        # cap respected


# --- eviction is not hard-delete: it buffers into the distill queue ------------

def test_evicted_goes_to_distill_queue(tmp_path):
    log = _make_log(tmp_path, max_event_entries=1, min_per_direction=0, protect=0.10)
    _store(log, "A", "MSFT", "2026-01-05", 0.05)
    _store(log, "B", "MSFT", "2026-02-05", 0.06)
    log.maintain_memory()
    assert _ids(log) == {"B"}
    queue_text = (tmp_path / "trading_distill_queue.md").read_text(encoding="utf-8")
    assert "EVENT | A |" in queue_text


# --- redundancy: only literal duplicates are removed (semantic dedupe → v3) ----

def test_exact_duplicate_removed_to_queue(tmp_path):
    log = _make_log(tmp_path, max_event_entries=10, min_per_direction=0, protect=0.10)
    _store(log, "A", "MSFT", "2026-01-05", 0.05)
    _store(log, "A2", "MSFT", "2026-01-05", 0.09)  # same ticker+date+rating → literal dup
    log.maintain_memory()
    assert _ids(log) == {"A"}
    queue_text = (tmp_path / "trading_distill_queue.md").read_text(encoding="utf-8")
    assert "EVENT | A2 |" in queue_text


def test_distinct_events_not_over_merged(tmp_path):
    """Six distinct same-ticker events must NOT collapse into one (no chain merge)."""
    log = _make_log(tmp_path, max_event_entries=10, min_per_direction=0, protect=0.10)
    _store(log, "L", "MSFT", "2026-01-05", -0.02)
    for i in range(5):
        _store(log, f"W{i}", "MSFT", f"2026-02-{i + 1:02d}", 0.05)
    log.maintain_memory()
    assert len(_ids(log)) == 6


# --- retrieval surfaces a bear lesson even when recent wins dominate -----------

def test_retrieval_surfaces_loss_amid_wins(tmp_path):
    log = _make_log(tmp_path, min_per_direction=1, protect=0.10)
    _store(log, "L", "MSFT", "2026-01-05", -0.02)   # old loss
    for i in range(4):
        _store(log, f"W{i}", "MSFT", f"2026-03-{i + 1:02d}", 0.05)  # recent wins
    ctx = log.get_lessons_context("MSFT", n_same=3, n_cross=0)
    assert "summary-L" in ctx   # bear lesson surfaced despite recency
    assert "summary-W3" in ctx  # recent wins still present


# --- no-op guards -------------------------------------------------------------

def test_maintain_noop_without_lessons(tmp_path):
    log = TradingMemoryLog({"memory_log_path": str(tmp_path / "m.md")})
    log.maintain_memory()  # must not raise


def test_maintain_noop_empty(tmp_path):
    log = _make_log(tmp_path)
    log.maintain_memory()  # empty lessons file → no-op
