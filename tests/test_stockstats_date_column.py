"""Tests for tolerating a non-`Date` index column in stockstats_utils (#890).

Guards against a download frame whose date column is `index` or `Datetime`
instead of `Date`, which would otherwise silently drop every indicator.
"""

from __future__ import annotations

import pandas as pd
import pytest

from tradingagents.dataflows import stockstats_utils as su


def _ohlcv(date_col: str) -> pd.DataFrame:
    """OHLCV frame whose date column is named `date_col`."""
    dates = pd.bdate_range("2026-04-01", periods=10)
    return pd.DataFrame({
        date_col: dates,
        "Open": [100.0 + i for i in range(10)],
        "High": [101.0 + i for i in range(10)],
        "Low": [99.0 + i for i in range(10)],
        "Close": [100.5 + i for i in range(10)],
        "Volume": [1_000_000 + i for i in range(10)],
    })


@pytest.mark.unit
class TestEnsureDateColumn:
    def test_renames_index_column(self):
        out = su._ensure_date_column(_ohlcv("index"))
        assert "Date" in out.columns and "index" not in out.columns

    def test_renames_datetime_and_date_variants(self):
        assert "Date" in su._ensure_date_column(_ohlcv("Datetime")).columns
        assert "Date" in su._ensure_date_column(_ohlcv("date")).columns

    def test_leaves_existing_date_untouched(self):
        df = _ohlcv("Date")
        assert su._ensure_date_column(df) is df  # no-op short-circuit

    def test_no_datelike_column_is_left_alone(self):
        df = pd.DataFrame({"Close": [1, 2, 3]})
        out = su._ensure_date_column(df)
        assert "Date" not in out.columns  # nothing to rename; caller handles


@pytest.mark.unit
class TestCleanDataframeAcrossVersions:
    def test_clean_handles_index_column(self):
        """A frame with `index` instead of `Date` must still clean to a
        usable, date-parsed frame (was KeyError: 'Date')."""
        cleaned = su._clean_dataframe(_ohlcv("index"))
        assert "Date" in cleaned.columns
        assert pd.api.types.is_datetime64_any_dtype(cleaned["Date"])
        assert len(cleaned) == 10

    def test_clean_handles_legacy_date_column(self):
        cleaned = su._clean_dataframe(_ohlcv("Date"))
        assert len(cleaned) == 10

    def test_indicators_compute_after_index_rename(self):
        """stockstats must compute indicators on a frame whose date column
        arrived as `index`, instead of erroring per indicator."""
        from stockstats import wrap
        cleaned = su._clean_dataframe(_ohlcv("index"))
        df = wrap(cleaned)
        df["close_5_sma"]  # triggers calculation
        assert "close_5_sma" in df.columns
        assert df["close_5_sma"].notna().any()


@pytest.mark.unit
class TestTimezoneHandling:
    """Ticker.history returns a tz-aware index for many exchanges (Asia/Shanghai
    for A-shares). The loader compares Date against a naive curr_date, which
    raised "Invalid comparison between dtype=datetime64[ns, Asia/Shanghai] and
    Timestamp" until the offset was dropped without conversion.
    """

    def _prices(self, dates):
        return pd.DataFrame({
            "Date": dates,
            "Open": [1.0] * len(dates),
            "High": [1.0] * len(dates),
            "Low": [1.0] * len(dates),
            "Close": [1.0] * len(dates),
            "Volume": [1] * len(dates),
        })

    def test_tz_aware_dates_become_naive_and_keep_local_date(self):
        dates = pd.date_range("2026-04-01", periods=3, freq="B", tz="Asia/Shanghai")
        cleaned = su._clean_dataframe(self._prices(dates))

        assert cleaned["Date"].dt.tz is None
        # A UTC conversion would have shifted this back a day.
        assert cleaned["Date"].iloc[0].strftime("%Y-%m-%d") == "2026-04-01"
        # The naive comparison load_ohlcv performs must work.
        assert (cleaned["Date"] <= pd.Timestamp("2026-04-02")).any()

    def test_cached_csv_offset_strings_become_naive(self):
        """Cache files written from a tz-aware frame carry '+08:00' strings."""
        dates = ["2026-04-01 00:00:00+08:00", "2026-04-02 00:00:00+08:00"]
        cleaned = su._clean_dataframe(self._prices(dates))

        assert cleaned["Date"].dt.tz is None
        assert cleaned["Date"].iloc[0].strftime("%Y-%m-%d") == "2026-04-01"

    def test_coerce_ohlcv_dates_handles_tz_aware_index(self):
        dates = pd.date_range("2026-04-01", periods=2, freq="B", tz="Asia/Shanghai")
        frame = pd.DataFrame({"Close": [1.0, 2.0]}, index=dates)

        parsed = su._coerce_ohlcv_dates(frame)
        assert getattr(parsed.dt, "tz", None) is None
        assert parsed.max().strftime("%Y-%m-%d") == "2026-04-02"

    def test_staleness_check_does_not_raise_on_tz_aware_dates(self):
        dates = pd.date_range("2026-04-01", periods=3, freq="B", tz="Asia/Shanghai")
        cleaned = su._clean_dataframe(self._prices(dates))
        # Used to raise the tz/naive comparison error; now a clean no-op.
        su._assert_ohlcv_not_stale(cleaned, "2026-04-03", "300750.SZ", "300750.SZ")
