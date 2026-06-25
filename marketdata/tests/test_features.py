"""Golden tests for the pure feature functions.

These pin the deterministic math so any future tweak to a formula fails LOUDLY here instead of
silently moving live relevance labels / backtest stats. Hand-computed expectations, no store I/O.
"""
import math

import pandas as pd
import pytest

from marketdata import features as F


def _series(values, start="2024-01-01"):
    idx = pd.bdate_range(start, periods=len(values))
    return pd.Series(values, index=idx, dtype="float64")


def test_forward_return_simple():
    close = _series([100, 101, 102, 103, 104, 110])
    # as_of at index 0 (100), horizon 5 -> 110/100 - 1
    assert F.forward_return(close, "2024-01-01", horizon=5) == pytest.approx(0.10)


def test_forward_return_none_when_window_exceeds_data():
    close = _series([100, 101, 102])
    assert F.forward_return(close, "2024-01-01", horizon=5) is None


def test_pos_asof_picks_last_bar_on_or_before():
    # 2024-01-01 is a Monday; 6 business days span a weekend (Fri=idx[4], next Mon=idx[5]).
    close = _series([10, 11, 12, 13, 14, 15])
    idx = close.index
    # Saturday after Friday's bar resolves to Friday (the last bar on or before).
    assert F.pos_asof(idx, idx[4] + pd.Timedelta(days=1)) == 4
    # a date before all bars -> None
    assert F.pos_asof(idx, idx[0] - pd.Timedelta(days=10)) is None


def test_abnormal_returns_subtract_benchmark():
    close = _series([100, 110, 121])     # +10%, +10%
    bench = _series([100, 105, 105])     # +5%, 0%
    ar = F.abnormal_returns(close, bench, beta=1.0)
    # day1: 0.10 - 0.05 = 0.05 ; day2: 0.10 - 0.0 = 0.10
    assert ar.iloc[1] == pytest.approx(0.05)
    assert ar.iloc[2] == pytest.approx(0.10)


def test_cum_abnormal_return_forward_window():
    close = _series([100, 110, 121])
    bench = _series([100, 105, 105])
    # as_of index 0, horizon 2 -> sum of AR over days 1,2 = 0.05 + 0.10
    car = F.cum_abnormal_return(close, bench, "2024-01-01", horizon=2, beta=1.0)
    assert car == pytest.approx(0.15)


def test_car_zscore_matches_manual():
    # Flat benchmark so AR == stock returns. Trailing window has NON-degenerate variance
    # (returns cycle through {-0.002,-0.001,0,0.001,0.002}) then a +5% forward jump after as_of.
    vals = [100.0]
    for t in range(1, 31):
        r = 0.001 * ((t % 5) - 2)
        vals.append(vals[-1] * (1 + r))
    base = vals[-1]
    close = _series(vals + [base * 1.05, base * 1.05])   # index 0..30 history, 31 = forward jump
    bench = _series([100.0] * len(close.index))           # flat -> m_t = 0, AR == returns
    as_of = close.index[len(vals) - 1]                    # index 30
    z = F.car_zscore(close, bench, as_of, horizon=1, vol_window=30, beta=1.0)
    # Manual: CAR(h=1) = 0.05 ; sd = std of AR over the 30-bar trailing window (positions 1..30)
    sd = F.abnormal_returns(close, bench).iloc[1:31].std(ddof=1)
    assert sd > 0
    assert z == pytest.approx(0.05 / (sd * math.sqrt(1)), rel=1e-6)


def test_label_from_value_abs_threshold():
    th = {"high": 1.5, "by": "abs"}
    assert F.label_from_value(2.0, th) == "high"
    assert F.label_from_value(-2.0, th) == "high"
    assert F.label_from_value(0.5, th) == "low"
    assert F.label_from_value(None, th) is None


def test_registry_compute_stamps_version():
    close = _series([100, 101, 102, 103, 104, 110])
    ctx = F.FeatureContext(close=close, bench=None)
    out = F.compute("forward_return", ctx, "2024-01-01", {"horizon": 5})
    assert out["value"] == pytest.approx(0.10)
    assert out["name"] == "forward_return"
    assert out["version"] == "1.0"


def test_unknown_feature_raises():
    ctx = F.FeatureContext(close=_series([1, 2, 3]), bench=None)
    with pytest.raises(KeyError):
        F.compute("does_not_exist", ctx, "2024-01-01")
