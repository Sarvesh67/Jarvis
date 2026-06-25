"""Golden tests for the backtester's deterministic core.

`compute_stats` is the trustworthy heart — these pin its math against hand computation so a tweak
fails loudly. Also asserts the look-ahead guard refuses a forward-looking predictor.
"""
import math

import pytest

from backtest import engine


def _rows(fwds, scores=None, start_day=1):
    rows = []
    for i, f in enumerate(fwds):
        r = {"ticker": "X.NS", "as_of": f"2024-01-{start_day+i:02d}", "fwd": f, "ar": None}
        if scores is not None:
            r["score"] = scores[i]
        rows.append(r)
    return rows


def test_hitrate_and_avg_long():
    rows = _rows([0.02, -0.01, 0.03, 0.00, -0.02])
    s = engine.compute_stats(rows, "long", 5)
    assert s["n"] == 5
    # directional (long) returns == fwd; >0 count = 2 (0.02, 0.03), 0.0 is not >0
    assert s["hitRate"] == pytest.approx(2 / 5)
    assert s["avgFwdRet"] == pytest.approx((0.02 - 0.01 + 0.03 + 0.0 - 0.02) / 5)


def test_short_flips_sign():
    rows = _rows([0.02, -0.01, 0.03])
    s = engine.compute_stats(rows, "short", 5)
    # short directional returns = -fwd -> [-0.02, 0.01, -0.03]; one positive
    assert s["hitRate"] == pytest.approx(1 / 3, abs=1e-4)
    assert s["avgFwdRet"] == pytest.approx(-(0.02 - 0.01 + 0.03) / 3, abs=1e-6)


def test_equity_curve_is_ordered_compound():
    rows = _rows([0.10, -0.05])  # long
    s = engine.compute_stats(rows, "long", 5)
    assert s["equityCurve"][0] == pytest.approx(1.10)
    assert s["equityCurve"][1] == pytest.approx(1.10 * 0.95)


def test_tstat_matches_manual():
    fwds = [0.02, 0.01, 0.03, 0.015, 0.025]
    s = engine.compute_stats(_rows(fwds), "long", 5)
    import numpy as np
    dr = np.array(fwds)
    expect = dr.mean() / (dr.std(ddof=1) / math.sqrt(len(dr)))
    assert s["tStat"] == pytest.approx(round(float(expect), 3))


def test_ic_perfect_rank_correlation():
    # score ascending, fwd ascending -> spearman = 1.0
    rows = _rows([0.01, 0.02, 0.03, 0.04], scores=[1, 2, 3, 4])
    s = engine.compute_stats(rows, "long", 5)
    assert s["ic"] == pytest.approx(1.0)


def test_ic_none_without_scores():
    s = engine.compute_stats(_rows([0.01, 0.02, 0.03]), "long", 5)
    assert s["ic"] is None


def test_empty_events():
    s = engine.compute_stats([], "long", 5)
    assert s["n"] == 0 and s["hitRate"] is None and s["equityCurve"] == []


def test_skips_none_fwd():
    rows = _rows([0.02, None, 0.03])
    s = engine.compute_stats(rows, "long", 5)
    assert s["n"] == 2  # the None-fwd event is excluded


def test_lookahead_guard_refuses_forward_feature():
    # car_zscore is forward_looking -> must be refused as a predictor.
    with pytest.raises(ValueError, match="LOOK-AHEAD GUARD"):
        engine.backtest(project="hedgefund", events=[], score_feature="car_zscore")


def test_unknown_score_feature_raises():
    with pytest.raises(ValueError, match="unknown score feature"):
        engine.backtest(project="hedgefund", events=[], score_feature="nope")
