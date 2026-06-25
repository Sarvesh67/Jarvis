"""Canonical timeseries store — Parquet at rest, DuckDB as the query layer.

Keep numbers OUT of the graph: FalkorDB holds entities + (ticker, as_of) pointers; this store
holds the OHLCV. They join on (ticker, as_of). Cognify/embeddings never touch this — so it costs
zero LLM budget.

Layout: store/bars/<freq>/<safe_ticker>.parquet, one file per (freq, ticker). The real ticker is
a column (not just the path), so reads never depend on path encoding. Writes are upserts: merge
new rows with existing, dedupe on (ticker, ts) keeping the latest.
"""
from __future__ import annotations

from typing import Iterable, Optional

import duckdb
import pandas as pd

from . import config


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce an incoming frame to the canonical schema/types. Raises on missing core columns."""
    missing = {"ticker", "ts", "close"} - set(df.columns)
    if missing:
        raise ValueError(f"frame missing required columns: {sorted(missing)}")
    out = df.copy()
    out["ts"] = pd.to_datetime(out["ts"], utc=False)
    for c in ("open", "high", "low", "close", "volume"):
        if c not in out.columns:
            out[c] = pd.NA
        out[c] = pd.to_numeric(out[c], errors="coerce")
    if "adjusted" not in out.columns:
        out["adjusted"] = False
    out["adjusted"] = out["adjusted"].astype(bool)
    if "freq" not in out.columns:
        raise ValueError("frame missing 'freq' (one of: %s)" % config.VALID_FREQS)
    out["ticker"] = out["ticker"].astype(str)
    return out[config.CANON_COLS].sort_values(["ticker", "ts"]).reset_index(drop=True)


def write_bars(df: pd.DataFrame) -> dict:
    """Upsert a frame of bars (possibly many tickers/freqs) into the store. Returns a summary."""
    df = _normalize(df)
    written = {}
    for (ticker, freq), part in df.groupby(["ticker", "freq"], sort=False):
        if freq not in config.VALID_FREQS:
            raise ValueError(f"invalid freq {freq!r} for {ticker}")
        path = config.parquet_path(ticker, freq)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            existing = pd.read_parquet(path)
            part = pd.concat([existing, part], ignore_index=True)
        part = (
            part.drop_duplicates(subset=["ticker", "ts"], keep="last")
            .sort_values("ts")
            .reset_index(drop=True)
        )
        part.to_parquet(path, index=False)
        written[f"{ticker}|{freq}"] = len(part)
    return {"tickers": len(written), "rows": int(written and sum(written.values()) or 0),
            "detail": written}


def _glob(freq: str) -> str:
    return str(config.BARS / freq / "*.parquet")


def read_bars(
    tickers: Optional[Iterable[str]] = None, *, freq: str = "1d",
    start=None, end=None,
) -> pd.DataFrame:
    """Read bars as a DataFrame. Empty frame (canonical columns) if nothing matches."""
    import glob as _g
    if not _g.glob(_glob(freq)):
        return pd.DataFrame(columns=config.CANON_COLS)
    con = duckdb.connect()
    try:
        q = f"SELECT * FROM read_parquet('{_glob(freq)}')"
        clauses, params = [], []
        if tickers:
            tl = list(tickers)
            q += " WHERE ticker IN (" + ",".join(["?"] * len(tl)) + ")"
            params += tl
            joiner = " AND"
        else:
            joiner = " WHERE"
        if start is not None:
            clauses.append(f"{joiner} ts >= ?"); params.append(pd.Timestamp(start)); joiner = " AND"
        if end is not None:
            clauses.append(f"{joiner} ts <= ?"); params.append(pd.Timestamp(end)); joiner = " AND"
        q += "".join(clauses) + " ORDER BY ticker, ts"
        return con.execute(q, params).df()
    finally:
        con.close()


def close_series(ticker: str, *, freq: str = "1d", start=None, end=None) -> pd.Series:
    """A point-in-time close Series (DatetimeIndex) for one ticker — what features.py consumes."""
    df = read_bars([ticker], freq=freq, start=start, end=end)
    if df.empty:
        return pd.Series(dtype="float64", index=pd.DatetimeIndex([]))
    s = df.set_index("ts")["close"].astype("float64")
    s.index = pd.DatetimeIndex(s.index)
    return s.sort_index()


def coverage(freq: str = "1d") -> pd.DataFrame:
    """Per-ticker row count + date range — quick sanity on what's loaded."""
    import glob as _g
    if not _g.glob(_glob(freq)):
        return pd.DataFrame(columns=["ticker", "rows", "first", "last", "adjusted"])
    con = duckdb.connect()
    try:
        return con.execute(
            f"""SELECT ticker, count(*) AS "rows", min(ts) AS "first", max(ts) AS "last",
                       bool_and(adjusted) AS adjusted
                FROM read_parquet('{_glob(freq)}') GROUP BY ticker ORDER BY ticker"""
        ).df()
    finally:
        con.close()
