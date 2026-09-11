#!/usr/bin/env python3
"""diagnose_yfinance.py — find which Yahoo fetch path works on this network.

Background: the indicator path (``stockstats_utils.load_ohlcv``) needs ~5 years
of daily bars. On some networks a single wide request comes back empty (Yahoo
rejects the range, or the cookie/crumb handshake fails) and yfinance logs
"possibly delisted". This script probes both yfinance entry points at several
window sizes so you can see exactly what your network supports:

  * Ticker.history  <- primary path used by the OHLCV tool
  * yf.download     <- fallback path

Usage:
  python scripts/diagnose_yfinance.py                 # default A-share + US symbols
  python scripts/diagnose_yfinance.py 600519.SS 300750.SZ
  TRADINGAGENTS_OHLCV_CHUNK_DAYS=0 python scripts/diagnose_yfinance.py   # test unchunked

Interpretation:
  * 30d/1y OK but 5y EMPTY  -> the wide range is the problem; keep chunking on
                               (TRADINGAGENTS_OHLCV_CHUNK_DAYS=365, the default).
  * everything EMPTY        -> Yahoo unreachable: set HTTPS_PROXY/HTTP_PROXY, or
                               install curl_cffi so yfinance can impersonate a browser.
  * Ticker.history OK but yf.download EMPTY -> expected on some networks; the
                               framework now prefers Ticker.history.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import yfinance as yf  # noqa: E402

FIVE_YEARS_AGO = (pd.Timestamp.today() - pd.DateOffset(years=5)).strftime("%Y-%m-%d")
ONE_YEAR_AGO = (pd.Timestamp.today() - pd.DateOffset(years=1)).strftime("%Y-%m-%d")
ONE_MONTH_AGO = (pd.Timestamp.today() - pd.DateOffset(months=1)).strftime("%Y-%m-%d")
TOMORROW = (pd.Timestamp.today() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")


def probe(name: str, fn) -> bool:
    start = time.monotonic()
    try:
        frame = fn()
    except Exception as exc:  # noqa: BLE001 — report and continue
        print(f"  [FAIL ] {name}: {type(exc).__name__}: {exc}  ({time.monotonic() - start:.1f}s)")
        return False
    rows = 0 if frame is None else len(frame)
    ok = rows > 0
    print(f"  [{'OK   ' if ok else 'EMPTY'}] {name}: rows={rows}  ({time.monotonic() - start:.1f}s)")
    return ok


def main() -> int:
    symbols = sys.argv[1:] or ["600519.SS", "300750.SZ", "AAPL"]
    print(f"yfinance {getattr(yf, '__version__', '?')}")
    try:
        import curl_cffi  # noqa: F401

        print(f"curl_cffi {getattr(curl_cffi, '__version__', '?')} — browser impersonation available")
    except ImportError:
        print("curl_cffi NOT installed — yfinance cannot impersonate a browser "
              "(a common cause of empty frames / crumb failures). Try: pip install curl_cffi")

    results: dict[str, dict[str, bool]] = {}
    for sym in symbols:
        print(f"\n=== {sym} ===")
        results[sym] = {
            # Explicit start/end mirrors what the framework actually sends
            # (period="1mo" is flaky on restricted networks and unused by the code).
            "history 1mo": probe("Ticker.history 1mo", lambda s=sym: yf.Ticker(s).history(start=ONE_MONTH_AGO)),
            "history 1y": probe("Ticker.history 1y", lambda s=sym: yf.Ticker(s).history(start=ONE_YEAR_AGO)),
            "history 5y": probe("Ticker.history 5y", lambda s=sym: yf.Ticker(s).history(start=FIVE_YEARS_AGO, end=TOMORROW, auto_adjust=True)),
            "download 1y": probe("yf.download 1y", lambda s=sym: yf.download(s, start=ONE_YEAR_AGO, progress=False, auto_adjust=True)),
            "download 5y": probe("yf.download 5y", lambda s=sym: yf.download(s, start=FIVE_YEARS_AGO, end=TOMORROW, progress=False, auto_adjust=True)),
        }

    print("\n--- summary ---")
    for sym, checks in results.items():
        failed = [k for k, ok in checks.items() if not ok]
        print(f"  {sym}: {'ALL OK' if not failed else 'failed: ' + ', '.join(failed)}")
    print("\nIf 5y fails but 1y/1mo works, keep chunking enabled "
          "(TRADINGAGENTS_OHLCV_CHUNK_DAYS=365 is the default).")
    print("If everything fails, configure a proxy (HTTPS_PROXY/HTTP_PROXY) or install curl_cffi.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
