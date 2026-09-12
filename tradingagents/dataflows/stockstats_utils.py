import logging
import os
import time
from typing import Annotated

import pandas as pd
import yfinance as yf
from stockstats import wrap
from yfinance.exceptions import YFRateLimitError

from .config import get_config
from .symbol_utils import NoMarketDataError, normalize_symbol
from .utils import safe_ticker_component

logger = logging.getLogger(__name__)

# A vendor's latest OHLCV row this many calendar days before the requested date
# is treated as stale. Generous enough to span long holiday weekends, tight
# enough to catch the year-old frames yfinance occasionally returns (#1021).
MAX_OHLCV_STALE_DAYS = 10

# How long a same-day cache that does not yet reach the requested day may be
# reused before it is refetched (#1150). Short enough that an intraday run picks
# up today's close soon after it publishes, long enough that a day with no bar
# at all (weekend, holiday) cannot trigger a download on every call.
OHLCV_CACHE_TTL_SECONDS = 900


def yf_retry(func, max_retries=3, base_delay=2.0):
    """Execute a yfinance call with exponential backoff on rate limits.

    yfinance raises YFRateLimitError on HTTP 429 responses but does not
    retry them internally. This wrapper adds retry logic specifically
    for rate limits. Other exceptions propagate immediately.
    """
    for attempt in range(max_retries + 1):
        try:
            return func()
        except YFRateLimitError:
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.warning(f"Yahoo Finance rate limited, retrying in {delay:.0f}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
            else:
                raise


def _ensure_date_column(data: pd.DataFrame) -> pd.DataFrame:
    """Normalize the date column to ``Date``.

    Some yfinance builds leave the index unnamed (so ``reset_index()`` yields
    ``index``) or use ``Datetime`` for intraday data. Rename the first
    date-like column so indicators don't silently drop when it isn't ``Date``.
    """
    if "Date" in data.columns:
        return data
    for candidate in ("index", "Datetime", "date"):
        if candidate in data.columns:
            return data.rename(columns={candidate: "Date"})
    return data


# Per-request window for the OHLCV history fetch. A single multi-year request can
# come back empty on some networks (the wide range is rejected, or the
# crumb/cookie handshake fails and yfinance logs "possibly delisted"), so the
# 5-year history is requested in bounded chunks and concatenated. Set
# TRADINGAGENTS_OHLCV_CHUNK_DAYS=0 to disable chunking (one wide request).
OHLCV_CHUNK_DAYS = 365


def _chunk_days() -> int:
    """Resolve the chunk size in days from the environment (0 = no chunking)."""
    raw = os.environ.get("TRADINGAGENTS_OHLCV_CHUNK_DAYS")
    if raw is None or raw == "":
        return OHLCV_CHUNK_DAYS
    try:
        return max(0, int(raw))
    except ValueError:
        logger.warning(
            "Invalid TRADINGAGENTS_OHLCV_CHUNK_DAYS=%r; using %d", raw, OHLCV_CHUNK_DAYS
        )
        return OHLCV_CHUNK_DAYS


def _fetch_via_history(canonical: str, start_str: str, end_str: str) -> pd.DataFrame:
    """Fetch via ``Ticker.history`` — the API the OHLCV tool path uses."""
    hist = yf_retry(lambda: yf.Ticker(canonical).history(
        start=start_str, end=end_str, auto_adjust=True
    ))
    if hist is None or hist.empty:
        return pd.DataFrame()
    return _ensure_date_column(hist.reset_index())


def _fetch_via_download(canonical: str, start_str: str, end_str: str) -> pd.DataFrame:
    """Fetch via ``yf.download`` — the fallback path."""
    downloaded = yf_retry(lambda: yf.download(
        canonical,
        start=start_str,
        end=end_str,
        multi_level_index=False,
        progress=False,
        auto_adjust=True,
    ))
    if downloaded is None or downloaded.empty:
        return pd.DataFrame()
    return _ensure_date_column(downloaded.reset_index())


def _fetch_ohlcv_window(canonical: str, start_str: str, end_str: str) -> pd.DataFrame:
    """Fetch one window, trying ``Ticker.history`` then ``yf.download``.

    The two yfinance entry points use different request paths; on restricted
    networks one can return rows while the other returns an empty frame (which
    yfinance reports as "possibly delisted"). Trying both keeps the indicator
    fetch working without changing the caller.
    """
    for fetch in (_fetch_via_history, _fetch_via_download):
        try:
            frame = fetch(canonical, start_str, end_str)
        except Exception as exc:  # noqa: BLE001 — try the next path
            logger.warning("%s failed for %s: %s", fetch.__name__, canonical, exc)
            continue
        if not frame.empty:
            return frame
    return pd.DataFrame()


def _fetch_ohlcv_history(
    canonical: str, start_dt: pd.Timestamp, end_dt: pd.Timestamp
) -> pd.DataFrame:
    """Fetch ``[start_dt, end_dt)`` in chunks and concatenate (dedup + sort)."""
    chunk_days = _chunk_days()
    if chunk_days <= 0:
        return _fetch_ohlcv_window(
            canonical, start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")
        )

    windows = []
    cursor = start_dt
    while cursor < end_dt:
        chunk_end = min(cursor + pd.Timedelta(days=chunk_days), end_dt)
        windows.append((cursor, chunk_end))
        cursor = chunk_end

    frames: list[pd.DataFrame] = []
    failed: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    for w_start, w_end in windows:
        frame = _fetch_ohlcv_window(
            canonical, w_start.strftime("%Y-%m-%d"), w_end.strftime("%Y-%m-%d")
        )
        if frame.empty:
            failed.append((w_start, w_end))
        else:
            frames.append(frame)

    # One extra pass over failed chunks — transient rejections usually clear.
    for w_start, w_end in failed:
        frame = _fetch_ohlcv_window(
            canonical, w_start.strftime("%Y-%m-%d"), w_end.strftime("%Y-%m-%d")
        )
        if frame.empty:
            logger.warning(
                "OHLCV chunk %s..%s unavailable for %s", w_start.date(), w_end.date(), canonical
            )
        else:
            frames.append(frame)

    if not frames:
        return pd.DataFrame()
    merged = pd.concat(frames, ignore_index=True)
    if "Date" in merged.columns:
        merged = (
            merged.drop_duplicates(subset=["Date"]).sort_values("Date").reset_index(drop=True)
        )
    return merged


def _to_naive_dates(values) -> pd.Series:
    """Parse dates to tz-naive, preserving the exchange's local trading date.

    ``Ticker.history`` attaches the venue timezone to the index for many
    exchanges (e.g. ``Asia/Shanghai`` for A-shares). Converting to UTC would
    shift the date back a day for east-of-UTC venues, so the offset is dropped
    WITHOUT conversion. Without this, the naive ``curr_date`` filters in
    ``load_ohlcv`` raise "Invalid comparison between dtype=datetime64[ns,
    Asia/Shanghai] and Timestamp".
    """
    try:
        parsed = pd.to_datetime(values, errors="coerce")
    except (ValueError, TypeError):
        # Mixed tz-aware / tz-naive values cannot be parsed in one pass; strip
        # the offset text and retry.
        stripped = [str(v).replace("Z", "").split("+")[0].split("-0")[0] for v in values]
        return pd.to_datetime(stripped, errors="coerce")
    tz = getattr(getattr(parsed, "dt", None), "tz", None)
    if tz is not None:
        return parsed.dt.tz_localize(None)
    return parsed


def _clean_dataframe(data: pd.DataFrame) -> pd.DataFrame:
    """Normalize a stock DataFrame for stockstats: parse dates, drop invalid rows, fill price gaps."""
    data = _ensure_date_column(data)
    data["Date"] = _to_naive_dates(data["Date"])
    data = data.dropna(subset=["Date"])

    price_cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in data.columns]
    data[price_cols] = data[price_cols].apply(pd.to_numeric, errors="coerce")
    data = data.dropna(subset=["Close"])
    data[price_cols] = data[price_cols].ffill().bfill()

    return data


def _coerce_ohlcv_dates(data: pd.DataFrame) -> pd.Series:
    """Return parsed dates from an OHLCV frame, whether Date is a column or the index.

    Uses ``_to_naive_dates`` so a tz-aware index (Ticker.history on many
    exchanges) cannot break downstream naive date comparisons.
    """
    if "Date" in data.columns:
        return _to_naive_dates(data["Date"]).dropna()
    # yfinance keeps the dates in the index (a DatetimeIndex, sometimes unnamed).
    if isinstance(data.index, pd.DatetimeIndex):
        return _to_naive_dates(pd.Series(data.index)).dropna()
    # Fallback: expose the index and look for any date-like column.
    df = data.reset_index()
    for col in ("Date", "Datetime", "date", "index"):
        if col in df.columns:
            parsed = _to_naive_dates(df[col]).dropna()
            if not parsed.empty:
                return parsed
    return pd.Series(dtype="datetime64[ns]")


def _assert_ohlcv_not_stale(
    data: pd.DataFrame,
    curr_date: str,
    symbol: str,
    canonical: str | None = None,
    *,
    max_stale_days: int = MAX_OHLCV_STALE_DAYS,
) -> None:
    """Reject OHLCV whose latest row is far older than curr_date.

    Raises NoMarketDataError (with a stale-specific detail) so the router treats
    it like any other "no usable data from this vendor" — try the next vendor,
    then emit one clear unavailable signal. Empty frames are left to the
    caller's existing no-data handling; this guards only the dangerous case of
    present-but-stale rows (a vendor returning a year-old frame that would
    otherwise feed wrong prices to the agent, #1021).
    """
    if data is None or data.empty:
        return
    requested = pd.to_datetime(curr_date, errors="coerce")
    if pd.isna(requested):
        return
    requested = requested.normalize()
    dates = _coerce_ohlcv_dates(data)
    if dates.empty:
        return
    latest = dates.max().normalize()
    stale_days = (requested - latest).days
    if stale_days > max_stale_days:
        raise NoMarketDataError(
            symbol,
            canonical,
            f"latest row is {latest.date()}, {stale_days} days before the "
            f"requested {requested.date()} (stale) — refusing to use it",
        )


def _needs_same_day_refresh(data_file, curr_date_dt, today_date) -> bool:
    """Whether a cached frame must be refetched to reflect the requested day.

    The cache file is keyed per day, so without this a run started before the
    day's bar was final keeps serving that snapshot to every later run (#1150).
    Two distinct staleness cases exist for a current-day request: the bar may be
    missing entirely, or present but still in progress — Yahoo publishes a
    partial daily candle during market hours, whose ``Close`` is not the closing
    price. Row inspection cannot tell a partial bar from a final one, so the TTL
    governs every current-day cache. Historical requests always reuse the cache,
    since those rows are immutable.
    """
    if curr_date_dt.date() < today_date.date():
        return False
    return time.time() - os.path.getmtime(data_file) > OHLCV_CACHE_TTL_SECONDS


def load_ohlcv(symbol: str, curr_date: str) -> pd.DataFrame:
    """Fetch OHLCV data with caching, filtered to prevent look-ahead bias.

    Downloads 5 years of data up to today and caches per symbol. The history is
    fetched in bounded chunks (``TRADINGAGENTS_OHLCV_CHUNK_DAYS``, default 365;
    0 disables chunking) because a single wide request can come back empty on
    restricted networks. On subsequent calls the cache is reused. Rows after
    curr_date are filtered out so backtests never see future prices.
    """
    # Resolve broker/forex symbols (XAUUSD+ -> GC=F) to Yahoo's convention,
    # then reject values that would escape the cache directory when
    # interpolated into the cache filename (e.g. ``../../tmp/x``).
    canonical = normalize_symbol(symbol)
    safe_symbol = safe_ticker_component(canonical)

    config = get_config()
    curr_date_dt = pd.to_datetime(curr_date)

    # Cache uses a fixed window (5y to today) so one file per symbol.
    today_date = pd.Timestamp.today()
    start_date = today_date - pd.DateOffset(years=5)
    start_str = start_date.strftime("%Y-%m-%d")
    # yfinance ``end`` is EXCLUSIVE; request tomorrow so today's row is included
    # when curr_date is the current day (#986). Look-ahead is still prevented by
    # the curr_date filter below.
    end_str = (today_date + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    os.makedirs(config["data_cache_dir"], exist_ok=True)
    data_file = os.path.join(
        config["data_cache_dir"],
        f"{safe_symbol}-YFin-data-{start_str}-{end_str}.csv",
    )

    # A cached file may be empty if a prior fetch failed (unknown symbol,
    # transient rate limit). Treat an empty/columnless cache as a miss and
    # re-fetch rather than serving the poisoned file forever.
    data = None
    if os.path.exists(data_file):
        cached = pd.read_csv(data_file, on_bad_lines="skip", encoding="utf-8")
        # Serve the cache only when it is usable and not a stale snapshot of the
        # day being requested (#1150); otherwise fall through and refetch.
        if (
            not cached.empty
            and "Close" in cached.columns
            and not _needs_same_day_refresh(data_file, curr_date_dt, today_date)
        ):
            data = cached

    if data is None:
        downloaded = _fetch_ohlcv_history(
            canonical, start_date, today_date + pd.Timedelta(days=1)
        )
        # Only cache real data — never persist an empty frame.
        if downloaded.empty or "Close" not in downloaded.columns:
            raise NoMarketDataError(
                symbol, canonical, "Yahoo Finance returned no rows"
            )
        downloaded.to_csv(data_file, index=False, encoding="utf-8")
        data = downloaded

    data = _clean_dataframe(data)

    # Filter to curr_date to prevent look-ahead bias in backtesting
    data = data[data["Date"] <= curr_date_dt]

    # Reject a stale frame (latest row far older than curr_date) rather than
    # feeding year-old prices into indicators (#1021).
    _assert_ohlcv_not_stale(data, curr_date, symbol, canonical)

    return data


def filter_financials_by_date(data: pd.DataFrame, curr_date: str) -> pd.DataFrame:
    """Drop financial statement columns (fiscal period timestamps) after curr_date.

    yfinance financial statements use fiscal period end dates as columns.
    Columns after curr_date represent future data and are removed to
    prevent look-ahead bias.
    """
    if not curr_date or data.empty:
        return data
    cutoff = pd.Timestamp(curr_date)
    mask = pd.to_datetime(data.columns, errors="coerce") <= cutoff
    return data.loc[:, mask]


class StockstatsUtils:
    @staticmethod
    def get_stock_stats(
        symbol: Annotated[str, "ticker symbol for the company"],
        indicator: Annotated[
            str, "quantitative indicators based off of the stock data for the company"
        ],
        curr_date: Annotated[
            str, "curr date for retrieving stock price data, YYYY-mm-dd"
        ],
    ):
        data = load_ohlcv(symbol, curr_date)
        df = wrap(data)
        df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")
        curr_date_str = pd.to_datetime(curr_date).strftime("%Y-%m-%d")

        df[indicator]  # trigger stockstats to calculate the indicator
        matching_rows = df[df["Date"].str.startswith(curr_date_str)]

        if not matching_rows.empty:
            indicator_value = matching_rows[indicator].values[0]
            return indicator_value
        else:
            return "N/A: Not a trading day (weekend or holiday)"
