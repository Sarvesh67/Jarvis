"""Swappable historical-data source adapters.

The user supplies NSE history in a format that isn't decided yet — so the *engine* depends only
on this `SourceAdapter` interface, and the concrete format is a config-selected adapter. Swap the
adapter, not the spine. Every adapter returns a frame in the canonical schema (see config.CANON_COLS)
and declares whether its prices are split/bonus `adjusted` (unadjusted prices corrupt returns).

Built-in adapters:
  - csvdir     : a directory of CSVs (one per ticker or a combined file), with a column map.
  - parquetdir : a directory of Parquet files (already columnar).
  - synthetic  : deterministic generated bars — for tests/demos and end-to-end verification
                 without any external data (seeded by ticker, so runs are reproducible).
  - yfinance   : optional, lazy import — for the 1-minute rolling capture Dispatch task only.

make_adapter({"kind": "...", ...}) builds one from a config dict (what the Dispatch tab stores).
"""
from __future__ import annotations

import abc
import glob
import hashlib
import pathlib
from typing import Iterable, Optional

import pandas as pd

from . import config


class SourceAdapter(abc.ABC):
    kind: str = "abstract"
    adjusted: bool = False  # does this source already split/bonus-adjust prices?

    @abc.abstractmethod
    def load_history(
        self, tickers: Iterable[str], *, start=None, end=None, freq: str = "1d"
    ) -> pd.DataFrame:
        """Return canonical-schema bars for `tickers` over [start, end] at `freq`."""

    def _finish(self, df: pd.DataFrame, freq: str) -> pd.DataFrame:
        """Stamp adjusted/freq and keep canonical columns (subclasses build the OHLCV core)."""
        if df.empty:
            return pd.DataFrame(columns=config.CANON_COLS)
        df = df.copy()
        df["freq"] = freq
        if "adjusted" not in df.columns:
            df["adjusted"] = self.adjusted
        for c in config.CANON_COLS:
            if c not in df.columns:
                df[c] = pd.NA
        return df[config.CANON_COLS]


# ─── File adapters ────────────────────────────────────────────────────────────

_DEFAULT_MAP = {
    "ticker": "ticker", "ts": "date", "open": "open", "high": "high",
    "low": "low", "close": "close", "volume": "volume",
}


class _FrameDirAdapter(SourceAdapter):
    """Common logic for csvdir/parquetdir: read files, apply a column map, filter."""

    def __init__(self, path: str, *, column_map: Optional[dict] = None,
                 adjusted: bool = False, ticker_from_filename: bool = False):
        self.path = pathlib.Path(path).expanduser()
        self.column_map = {**_DEFAULT_MAP, **(column_map or {})}
        self.adjusted = adjusted
        self.ticker_from_filename = ticker_from_filename

    def _read_file(self, p: pathlib.Path) -> pd.DataFrame:  # pragma: no cover - overridden
        raise NotImplementedError

    def _files(self) -> list[pathlib.Path]:
        raise NotImplementedError

    def load_history(self, tickers, *, start=None, end=None, freq="1d") -> pd.DataFrame:
        want = set(tickers) if tickers else None
        frames = []
        for p in self._files():
            raw = self._read_file(p)
            if raw.empty:
                continue
            m = self.column_map
            df = pd.DataFrame({
                "ts": pd.to_datetime(raw[m["ts"]], errors="coerce"),
                "open": pd.to_numeric(raw.get(m["open"]), errors="coerce"),
                "high": pd.to_numeric(raw.get(m["high"]), errors="coerce"),
                "low": pd.to_numeric(raw.get(m["low"]), errors="coerce"),
                "close": pd.to_numeric(raw.get(m["close"]), errors="coerce"),
                "volume": pd.to_numeric(raw.get(m["volume"]), errors="coerce"),
            })
            if self.ticker_from_filename:
                df["ticker"] = p.stem
            else:
                df["ticker"] = raw[m["ticker"]].astype(str).values
            frames.append(df)
        if not frames:
            return pd.DataFrame(columns=config.CANON_COLS)
        out = pd.concat(frames, ignore_index=True).dropna(subset=["ts", "close"])
        if want:
            out = out[out["ticker"].isin(want)]
        if start is not None:
            out = out[out["ts"] >= pd.Timestamp(start)]
        if end is not None:
            out = out[out["ts"] <= pd.Timestamp(end)]
        return self._finish(out, freq)


class CsvDirAdapter(_FrameDirAdapter):
    kind = "csvdir"

    def _files(self):
        return [pathlib.Path(p) for p in sorted(glob.glob(str(self.path / "*.csv")))]

    def _read_file(self, p):
        return pd.read_csv(p)


class ParquetDirAdapter(_FrameDirAdapter):
    kind = "parquetdir"

    def _files(self):
        return [pathlib.Path(p) for p in sorted(glob.glob(str(self.path / "*.parquet")))]

    def _read_file(self, p):
        return pd.read_parquet(p)


# ─── Synthetic adapter (deterministic; for tests + verification) ──────────────

class SyntheticAdapter(SourceAdapter):
    """Deterministic geometric-walk bars seeded by ticker — reproducible, no external data.

    Used by tests and the `demo` CLI so the whole spine (and later the join) can be verified end
    to end before the user's real NSE history is wired in.
    """
    kind = "synthetic"
    adjusted = True

    def __init__(self, *, start: str = "2024-01-01", periods: int = 400, seed: int = 0):
        self.start = start
        self.periods = periods
        self.seed = seed

    def _series_for(self, ticker: str, n: int) -> pd.DataFrame:
        import numpy as np
        h = int(hashlib.sha256(f"{ticker}:{self.seed}".encode()).hexdigest(), 16) % (2**32)
        rng = np.random.default_rng(h)
        dates = pd.bdate_range(self.start, periods=n)  # business days ≈ trading days
        rets = rng.normal(0.0003, 0.015, size=n)
        close = 100.0 * np.exp(np.cumsum(rets))
        return pd.DataFrame({
            "ticker": ticker, "ts": dates,
            "open": close * (1 - rng.normal(0, 0.002, n)),
            "high": close * (1 + abs(rng.normal(0, 0.004, n))),
            "low": close * (1 - abs(rng.normal(0, 0.004, n))),
            "close": close, "volume": rng.integers(1e5, 1e6, n),
        })

    def load_history(self, tickers, *, start=None, end=None, freq="1d") -> pd.DataFrame:
        frames = [self._series_for(t, self.periods) for t in tickers]
        out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if start is not None and not out.empty:
            out = out[out["ts"] >= pd.Timestamp(start)]
        if end is not None and not out.empty:
            out = out[out["ts"] <= pd.Timestamp(end)]
        return self._finish(out, freq)


# ─── yfinance adapter (optional; 1-minute rolling capture) ────────────────────

class YFinanceAdapter(SourceAdapter):
    """Pulls bars from yfinance. Optional dependency — imported lazily.

    Note: yfinance serves 1-minute data only for ~the last 7 days, so this is a *rolling capture*
    that the Dispatch tab runs on a schedule to ACCUMULATE intraday history that cannot be
    backfilled later. Daily bars carry the real backtests meanwhile.
    """
    kind = "yfinance"
    adjusted = True  # auto_adjust=True

    def load_history(self, tickers, *, start=None, end=None, freq="1d") -> pd.DataFrame:
        import yfinance as yf  # lazy; only needed when this adapter is actually used
        interval = {"1d": "1d", "1m": "1m"}[freq]
        frames = []
        for t in tickers:
            df = yf.download(t, start=start, end=end, interval=interval,
                             auto_adjust=True, progress=False)
            if df is None or df.empty:
                continue
            df = df.reset_index()
            tcol = "Datetime" if "Datetime" in df.columns else "Date"
            frames.append(pd.DataFrame({
                "ticker": t, "ts": pd.to_datetime(df[tcol]),
                "open": df["Open"], "high": df["High"], "low": df["Low"],
                "close": df["Close"], "volume": df["Volume"],
            }))
        out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        return self._finish(out, freq)


ADAPTERS = {
    a.kind: a for a in (CsvDirAdapter, ParquetDirAdapter, SyntheticAdapter, YFinanceAdapter)
}


def make_adapter(spec: dict) -> SourceAdapter:
    """Build an adapter from a config dict, e.g. {"kind":"csvdir","path":"~/nse","adjusted":true}."""
    spec = dict(spec or {})
    kind = spec.pop("kind", None)
    if kind not in ADAPTERS:
        raise ValueError(f"unknown source kind {kind!r}; expected {sorted(ADAPTERS)}")
    return ADAPTERS[kind](**spec)
