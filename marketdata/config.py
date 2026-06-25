"""Paths and constants for the market-data spine."""
from __future__ import annotations

import pathlib
import re

BASE = pathlib.Path(__file__).resolve().parent
# Canonical at-rest store (gitignored): Parquet is durable/portable, DuckDB is the query layer.
STORE = BASE / "store"
BARS = STORE / "bars"          # store/bars/<freq>/<safe_ticker>.parquet

# Canonical bar schema. `adjusted` rides on each row so a frame always declares whether its
# prices are split/bonus-adjusted (unadjusted prices silently corrupt every return).
CANON_COLS = ["ticker", "ts", "open", "high", "low", "close", "volume", "adjusted", "freq"]

# Default benchmark when a ticker has no sector mapping (NIFTY 50). The linker's symbols.json
# can override per ticker.
DEFAULT_BENCHMARK = "^NSEI"

# NSE regular session (IST). Used by the calendar/intraday capture, not by daily features.
NSE_TZ = "Asia/Kolkata"
NSE_OPEN = "09:15"
NSE_CLOSE = "15:30"

VALID_FREQS = {"1d", "1m"}


def safe_ticker(ticker: str) -> str:
    """Filesystem-safe stem for a ticker (e.g. '^NSEI' -> 'NSEI', 'INFY.NS' -> 'INFY_NS').

    The real ticker is always stored as a column inside the Parquet, so this is only a path name.
    """
    return re.sub(r"[^A-Za-z0-9]+", "_", ticker).strip("_") or "ticker"


def parquet_path(ticker: str, freq: str) -> pathlib.Path:
    return BARS / freq / f"{safe_ticker(ticker)}.parquet"
