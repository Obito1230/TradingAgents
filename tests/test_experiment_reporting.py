"""Tests for the P1 reporting fixes.

Two problems found by running P1 itself:

1. Both arms write ``full_states_log_<date>.json``, so the second arm overwrites
   the first one's archive — the injected budget must be captured per run.
2. The per-arm summary printed ``mean_alpha``, which is a property of the
   SCENARIO (identical in both arms by construction: 0.0614 and 0.0614). Printed
   per arm it invites the false reading "both arms performed the same".
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def rex():
    spec = importlib.util.spec_from_file_location(
        "run_experiment_under_test", _HERE / "scripts" / "run_experiment.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_experiment_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def _archive(results_dir, ticker, date, past_context):
    d = Path(results_dir) / ticker / "TradingAgentsStrategy_logs"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"full_states_log_{date}.json").write_text(
        json.dumps({"past_context": past_context}), encoding="utf-8")


def _row(arm, injected=None, hit=1.0, alpha=0.10, rating="Buy"):
    row = {"ticker": "NVDA", "trade_date": "2026-01-05", "arm": arm, "rep": 1,
           "rating": rating, "benchmark": "SPY", "raw": 0.10, "alpha": alpha,
           "hit": hit, "status": "ok", "error": "", "tokens_total": 1000,
           "cost_usd": 0.01, "duration_s": 100.0}
    if injected is not None:
        row["injected_chars"] = injected
    return row


# --- injected_chars ----------------------------------------------------------

def test_reads_the_archive_just_written(tmp_path, rex):
    cfg = {"results_dir": str(tmp_path)}
    _archive(tmp_path, "NVDA", "2026-01-05", "x" * 1234)

    assert rex.injected_chars("NVDA", "2026-01-05", cfg) == 1234


def test_missing_archive_returns_none(tmp_path, rex):
    """A failed run (or a ticker sanitised differently) must not crash the loop."""
    assert rex.injected_chars("NVDA", "2026-01-05", {"results_dir": str(tmp_path)}) is None


def test_corrupt_archive_returns_none(tmp_path, rex):
    d = Path(tmp_path) / "NVDA" / "TradingAgentsStrategy_logs"
    d.mkdir(parents=True)
    (d / "full_states_log_2026-01-05.json").write_text("{not json", encoding="utf-8")

    assert rex.injected_chars("NVDA", "2026-01-05", {"results_dir": str(tmp_path)}) is None


def test_second_arm_overwrites_the_first(tmp_path, rex):
    """Documents WHY per-run capture is needed: the archive only keeps one arm."""
    cfg = {"results_dir": str(tmp_path)}
    _archive(tmp_path, "NVDA", "2026-01-05", "a" * 100)   # arm "on"
    _archive(tmp_path, "NVDA", "2026-01-05", "b" * 500)   # arm "off" overwrites

    assert rex.injected_chars("NVDA", "2026-01-05", cfg) == 500


# --- summarize ---------------------------------------------------------------

def test_per_arm_table_has_no_mean_alpha(rex, capsys):
    rows = [_row("on", injected=700, hit=1.0, alpha=0.0614),
            _row("off", injected=1000, hit=0.0, alpha=0.0614)]

    rex.summarize(rows, ["on", "off"])
    out = capsys.readouterr().out

    assert "mean_alpha" not in out
    assert "inj_chars" in out


def test_scenario_alpha_is_reported_once_and_labelled_arm_independent(rex, capsys):
    rows = [_row("on", injected=700, alpha=0.0614), _row("off", injected=1000, alpha=0.0614)]

    rex.summarize(rows, ["on", "off"])
    out = capsys.readouterr().out

    assert out.count("+0.0614") == 1          # printed once, not once per arm
    assert "arm-independent" in out
    assert "NOT an arm-level return" in out


def test_budget_ratio_is_reported(rex, capsys):
    rows = [_row("on", injected=700), _row("off", injected=1000)]

    rex.summarize(rows, ["on", "off"])
    out = capsys.readouterr().out

    # labelled with arm names, so it never depends on --arms ordering
    assert "injected budget" in out
    assert "on=700 chars, off=1000 chars" in out
    assert "on/off = 70%" in out


def test_budget_ratio_follows_arm_order(rex, capsys):
    """Flipping --arms must flip the label, not silently invert the finding."""
    rows = [_row("on", injected=700), _row("off", injected=1000)]

    rex.summarize(rows, ["off", "on"])
    out = capsys.readouterr().out

    assert "off/on = 143%" in out


def test_small_sample_warning_fires(rex, capsys):
    rows = [_row("on", injected=700, hit=1.0), _row("off", injected=1000, hit=0.0)]

    rex.summarize(rows, ["on", "off"])
    out = capsys.readouterr().out

    assert "fewer than 10 scored observations" in out
    assert "do NOT claim significance" in out


def test_no_warning_with_enough_observations(rex, capsys):
    rows = [_row("on", injected=700, hit=1.0) for _ in range(6)]
    rows += [_row("off", injected=1000, hit=0.0) for _ in range(6)]

    rex.summarize(rows, ["on", "off"])
    out = capsys.readouterr().out

    assert "fewer than 10 scored observations" not in out


def test_hold_only_runs_do_not_crash(rex, capsys):
    """A Pilot where every call is Hold has no hits at all — must still report."""
    rows = [_row("on", injected=700, hit=None, rating="Hold"),
            _row("off", injected=1000, hit=None, rating="Hold")]

    rex.summarize(rows, ["on", "off"])
    out = capsys.readouterr().out

    assert "Hold (unscored): 2" in out


def test_rows_without_injected_chars_degrade_gracefully(rex, capsys):
    """Old CSVs (pre-fix) have no such column; summarize must not crash."""
    rows = [_row("on", injected=None), _row("off", injected=None)]

    rex.summarize(rows, ["on", "off"])
    out = capsys.readouterr().out

    assert "n/a" in out
    assert "injected budget" not in out      # nothing to compute
