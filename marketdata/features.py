"""Jarvis market-data feature registry — the single source of truth for every deterministic
price calculation in the platform.

Both the ingest-time price-relevance preprocess step (`engine:"feature"`) and the backtester
import these *same* functions, so the math can never drift between "how a doc was scored" and
"how a signal was tested". Tweaking a formula is a config edit (params) or a new registry entry
— never a second copy.

Design rules (see plan "Tweak-safety"):
  - **Pure.** Functions take pandas series + params in, return a float (or None) out. No I/O, no
    hidden state. Trivially unit-testable; golden fixtures in tests/test_features.py pin outputs.
  - **Point-in-time.** Anything used as a *predictor* may only read data up to and including
    `as_of`. Forward windows (realized move *after* as_of) are clearly named `forward`/`car_*`
    and are HINDSIGHT — fine for relevance tagging, must never feed a backtested signal feature.
  - **Versioned.** Each registry entry carries a `version`; results stamp it so re-tuning a
    formula doesn't silently re-interpret old outputs.
  - **Swap, don't rewrite.** A new method (e.g. market-model beta) is a NEW registry entry
    selected by name in config; the old one stays callable.

Conventions:
  - A "close" / "bench" argument is a pandas Series indexed by a sorted, unique, ascending
    DatetimeIndex (one row per trading day/bar). Values are prices.
  - `as_of` is a timestamp; we locate the last bar at or before it (the info available then).
  - `horizon` / `window` are counts of *bars* (trading days for daily data), not calendar days.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Optional

import pandas as pd


# ─── Point-in-time index helpers ──────────────────────────────────────────────

def _check_index(s: pd.Series) -> pd.Series:
    """Series with a sorted, unique, ascending DatetimeIndex. Raises on a malformed series."""
    if not isinstance(s, pd.Series):
        raise TypeError("expected a pandas Series of prices")
    idx = s.index
    if not isinstance(idx, pd.DatetimeIndex):
        raise TypeError("series must have a DatetimeIndex")
    if not idx.is_monotonic_increasing:
        s = s.sort_index()
    if s.index.has_duplicates:
        s = s[~s.index.duplicated(keep="last")]
    return s


def pos_asof(index: pd.DatetimeIndex, as_of) -> Optional[int]:
    """Integer position of the last bar at or before `as_of`, or None if `as_of` precedes all."""
    ts = pd.Timestamp(as_of)
    # searchsorted 'right' gives count of index <= ts; minus 1 is the last such position.
    i = int(index.searchsorted(ts, side="right")) - 1
    return i if i >= 0 else None


def _simple_returns(close: pd.Series) -> pd.Series:
    return close.pct_change()


# ─── Core pure feature functions ──────────────────────────────────────────────
# Each returns None when there is not enough data to compute it honestly (fail closed —
# never fabricate a number from a short window).

def forward_return(close: pd.Series, as_of, horizon: int = 5) -> Optional[float]:
    """Simple return from the as_of close to `horizon` bars later. HINDSIGHT (forward-looking)."""
    close = _check_index(close)
    i = pos_asof(close.index, as_of)
    if i is None:
        return None
    j = i + int(horizon)
    if j >= len(close):
        return None
    c0, c1 = close.iloc[i], close.iloc[j]
    if not c0:
        return None
    return float(c1 / c0 - 1.0)


def abnormal_returns(close: pd.Series, bench: pd.Series, beta: float = 1.0) -> pd.Series:
    """Daily abnormal returns AR_t = r_t - beta*m_t, aligned on the stock's index."""
    close = _check_index(close)
    r = _simple_returns(close)
    if bench is None:
        return r  # no benchmark => raw returns (abnormal == total)
    bench = _check_index(bench)
    m = _simple_returns(bench).reindex(close.index)
    return r - beta * m


def cum_abnormal_return(
    close: pd.Series, bench: pd.Series, as_of, horizon: int = 5, beta: float = 1.0
) -> Optional[float]:
    """CAR over the FORWARD window (as_of, as_of+horizon]. HINDSIGHT — relevance/eval only."""
    close = _check_index(close)
    i = pos_asof(close.index, as_of)
    if i is None:
        return None
    ar = abnormal_returns(close, bench, beta=beta)
    window = ar.iloc[i + 1 : i + 1 + int(horizon)]
    if len(window) < int(horizon) or window.isna().any():
        return None
    return float(window.sum())


def trailing_ar_vol(
    close: pd.Series, bench: pd.Series, as_of, window: int = 60, beta: float = 1.0,
    min_obs: int = 20,
) -> Optional[float]:
    """Std of abnormal returns over the `window` bars up to and INCLUDING as_of (point-in-time)."""
    close = _check_index(close)
    i = pos_asof(close.index, as_of)
    if i is None:
        return None
    ar = abnormal_returns(close, bench, beta=beta)
    hist = ar.iloc[max(0, i - int(window) + 1) : i + 1].dropna()
    if len(hist) < int(min_obs):
        return None
    sd = float(hist.std(ddof=1))
    return sd if sd > 0 else None


def realized_vol(close: pd.Series, as_of, window: int = 20, min_obs: int = 10) -> Optional[float]:
    """Std of simple returns over the trailing `window` bars up to and including as_of."""
    close = _check_index(close)
    i = pos_asof(close.index, as_of)
    if i is None:
        return None
    r = _simple_returns(close).iloc[max(0, i - int(window) + 1) : i + 1].dropna()
    if len(r) < int(min_obs):
        return None
    sd = float(r.std(ddof=1))
    return sd if sd > 0 else None


def car_zscore(
    close: pd.Series, bench: pd.Series, as_of, horizon: int = 5, vol_window: int = 60,
    beta: float = 1.0,
) -> Optional[float]:
    """The price-relevance statistic: forward CAR normalised by trailing AR volatility.

    z = CAR(as_of, as_of+h) / (sigma_AR * sqrt(h)).  |z| measures how unusual the realised
    move around the doc was, in standard deviations. HINDSIGHT (uses the forward window).
    """
    car = cum_abnormal_return(close, bench, as_of, horizon=horizon, beta=beta)
    sd = trailing_ar_vol(close, bench, as_of, window=vol_window, beta=beta)
    if car is None or sd is None:
        return None
    return float(car / (sd * math.sqrt(int(horizon))))


# ─── Registry ─────────────────────────────────────────────────────────────────
# A FeatureContext bundles the series a feature needs; the registry wraps the pure functions
# above with a uniform (ctx, as_of, **params) signature so preprocess + backtester call them by
# name with config-supplied params. Pure functions stay independently testable.

@dataclass
class FeatureContext:
    close: pd.Series                 # the subject ticker's close series
    bench: Optional[pd.Series] = None  # benchmark/sector index close series (None => raw)


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    version: str
    fn: Callable[..., Optional[float]]
    needs_bench: bool
    forward_looking: bool  # True => HINDSIGHT; must not be used as a backtested predictor


FEATURES: dict[str, FeatureSpec] = {}


def _register(spec: FeatureSpec) -> None:
    FEATURES[spec.name] = spec


_register(FeatureSpec(
    "forward_return", "1.0",
    lambda ctx, as_of, horizon=5: forward_return(ctx.close, as_of, horizon=horizon),
    needs_bench=False, forward_looking=True,
))
_register(FeatureSpec(
    "cum_abnormal_return", "1.0",
    lambda ctx, as_of, horizon=5, beta=1.0:
        cum_abnormal_return(ctx.close, ctx.bench, as_of, horizon=horizon, beta=beta),
    needs_bench=True, forward_looking=True,
))
_register(FeatureSpec(
    "car_zscore", "1.0",
    lambda ctx, as_of, horizon=5, vol_window=60, beta=1.0:
        car_zscore(ctx.close, ctx.bench, as_of, horizon=horizon, vol_window=vol_window, beta=beta),
    needs_bench=True, forward_looking=True,
))
_register(FeatureSpec(
    "realized_vol", "1.0",
    lambda ctx, as_of, window=20: realized_vol(ctx.close, as_of, window=window),
    needs_bench=False, forward_looking=False,
))


def compute(name: str, ctx: FeatureContext, as_of, params: Optional[dict] = None) -> dict:
    """Compute a registered feature by name. Returns {value, version, name} (value may be None).

    Raises KeyError for an unknown feature name (fail loud — a typo in config should not silently
    produce a null signal).
    """
    spec = FEATURES[name]
    value = spec.fn(ctx, as_of, **(params or {}))
    return {"name": name, "version": spec.version, "value": value}


def label_from_value(value: Optional[float], thresholds: dict) -> Optional[str]:
    """Map a numeric feature value to a label via config thresholds.

    thresholds example: {"high": 1.5, "by": "abs"}  -> |value| >= 1.5 => "high" else "low".
    Returns None when value is None (no data => no label, fail closed).
    """
    if value is None:
        return None
    by = thresholds.get("by", "abs")
    v = abs(value) if by == "abs" else value
    hi = thresholds.get("high")
    if hi is None:
        return None
    high_label = thresholds.get("highLabel", "high")
    low_label = thresholds.get("lowLabel", "low")
    return high_label if v >= hi else low_label
