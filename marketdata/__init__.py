"""Jarvis market-data spine.

DuckDB + Parquet timeseries store, swappable source adapters, and a pure feature registry shared
by the ingest-time price-relevance step and the backtester. Numbers live here; the graph holds
only (ticker, as_of) pointers that join back to this store.
"""
from . import config, features, source, store

__all__ = ["config", "features", "source", "store", "ingest_from_adapter"]


def ingest_from_adapter(spec: dict, tickers, *, start=None, end=None, freq: str = "1d") -> dict:
    """Pull history via a configured source adapter and upsert it into the canonical store."""
    adapter = source.make_adapter(spec)
    df = adapter.load_history(tickers, start=start, end=end, freq=freq)
    if df.empty:
        return {"tickers": 0, "rows": 0, "detail": {}, "note": "adapter returned no rows"}
    return store.write_bars(df)
