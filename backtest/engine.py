"""Deterministic signal backtester — the evidence engine behind every agent proposal.

A proposal is a *hypothesis in machine-executable form*; this module turns it into evidence with
ZERO LLM calls. Input: a `trigger` (or an explicit event list). Steps:

  1. Resolve the trigger to events `(ticker, as_of)` from the graph's stamped `ticker:`/`asof:`
     NodeSets (Phase 2), or take an explicit event list.
  2. For each event, compute the FORWARD return + cumulative abnormal return from the timeseries
     store (marketdata.features).
  3. Aggregate to stats: n, hitRate, avgFwdRet, tStat, IC, equity curve.

Discipline (non-negotiable — see plan Risks):
  - **Point-in-time / look-ahead guard.** The forward return is the *outcome* (legitimately uses
    data after as_of). Any feature used as a *predictor* (the IC `scoreFeature`) MUST be
    point-in-time; the engine REFUSES a forward-looking feature as a predictor (that's the
    "leaky run is caught" check). Outcome features are flagged `forward_looking` in the registry.
  - **Versioned.** Output stamps the featureVersion of every formula used, so re-tuning a
    formula doesn't silently re-interpret an old backtest.

Runs in the marketdata venv (duckdb/pandas/numpy + the light falkordb client).
"""
from __future__ import annotations

import json
import math
import pathlib

import numpy as np

from marketdata import config as mdconfig
from marketdata import store
from marketdata.features import (FEATURES, FeatureContext, compute, cum_abnormal_return,
                                 forward_return)

REPO = pathlib.Path(__file__).resolve().parent.parent
COGNEE_DATA = REPO / "cognee" / "data"
COGNEE_SEEDS = REPO / "cognee" / "seeds"

DIRSIGN = {"long": 1.0, "short": -1.0}


# ─── Symbols / benchmark (read the linker's per-project map) ──────────────────

def load_symbols(project: str) -> dict:
    for p in (COGNEE_DATA / project / "symbols.json", COGNEE_SEEDS / project / "symbols.json"):
        if p.exists():
            try:
                return json.loads(p.read_text())
            except Exception:  # noqa: BLE001
                pass
    return {"defaultBenchmark": mdconfig.DEFAULT_BENCHMARK, "tickers": {}}


def benchmark_for(ticker: str, symbols: dict) -> str:
    meta = (symbols.get("tickers") or {}).get(ticker) or {}
    return meta.get("benchmark") or symbols.get("defaultBenchmark") or mdconfig.DEFAULT_BENCHMARK


def _ctx(ticker: str, symbols: dict) -> FeatureContext:
    return FeatureContext(close=store.close_series(ticker),
                          bench=store.close_series(benchmark_for(ticker, symbols)))


# ─── Trigger → events (from the stamped graph) ────────────────────────────────

def _graph(project: str):
    from falkordb import FalkorDB
    return FalkorDB(host="127.0.0.1", port=6379).select_graph(f"{project}_graph")


def _ticker_blobs(g) -> dict:
    """Per-ticker lowercased blob of connected entity names + chunk text, for trigger filtering."""
    rows = g.query(
        "MATCH (tk:NodeSet)-[:belongs_to_set]-(m) WHERE tk.name STARTS WITH 'ticker:' "
        "RETURN tk.name AS t, collect(toLower(coalesce(m.name, m.text, ''))) AS blob"
    ).result_set
    return {t.split("ticker:", 1)[1]: " ".join(blob_list) for t, blob_list in rows}


def resolve_events_from_graph(project: str, trigger: dict) -> list[dict]:
    """Resolve a trigger to events `(ticker, as_of)` using the stamped NodeSets.

    Base universe = (ticker, as_of) pairs that co-occur on a shared graph node. The trigger's
    `match` block then filters by ticker / entity-name / keyword. NOTE (v1): entity/keyword
    filtering is at ticker granularity — exact when one doc per ticker; refine to per-event later.
    """
    g = _graph(project)
    rows = g.query(
        "MATCH (tk:NodeSet)-[:belongs_to_set]-(c)-[:belongs_to_set]-(af:NodeSet) "
        "WHERE tk.name STARTS WITH 'ticker:' AND af.name STARTS WITH 'asof:' "
        "RETURN DISTINCT tk.name AS t, af.name AS a"
    ).result_set
    events = [{"ticker": t.split("ticker:", 1)[1], "as_of": a.split("asof:", 1)[1],
               "source": "graph"} for t, a in rows]

    m = (trigger or {}).get("match") or {}
    want = set(m.get("tickers") or [])
    if want:
        events = [e for e in events if e["ticker"] in want]
    entity_any = [s.lower() for s in (m.get("entityAny") or [])]
    keyword_any = [s.lower() for s in (m.get("keywordAny") or [])]
    if entity_any or keyword_any:
        blobs = _ticker_blobs(g)

        def ok(e):
            blob = blobs.get(e["ticker"], "")
            if entity_any and not any(s in blob for s in entity_any):
                return False
            if keyword_any and not any(s in blob for s in keyword_any):
                return False
            return True

        events = [e for e in events if ok(e)]
    return events


# ─── Stats (the deterministic core — independently golden-tested) ─────────────

def _rank(a: np.ndarray) -> np.ndarray:
    order = a.argsort()
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(len(a), dtype=float)
    return ranks


def _information_coefficient(rows: list) -> float | None:
    """Spearman rank corr between a point-in-time predictor `score` and the realised fwd return."""
    pairs = [(r["score"], r["fwd"]) for r in rows
             if r.get("score") is not None and r.get("fwd") is not None]
    if len(pairs) < 3:
        return None
    s = _rank(np.array([p[0] for p in pairs], dtype=float))
    f = _rank(np.array([p[1] for p in pairs], dtype=float))
    if s.std() == 0 or f.std() == 0:
        return None
    return round(float(np.corrcoef(s, f)[0, 1]), 4)


def compute_stats(rows: list, direction: str, horizon: int) -> dict:
    """Aggregate per-event rows into signal stats. `rows` carry `fwd` (forward return) and an
    optional point-in-time `score` (for IC). Directional return = sign(direction) * fwd."""
    sign = DIRSIGN.get(direction, 1.0)
    ev = [r for r in rows if r.get("fwd") is not None]
    dr = np.array([sign * r["fwd"] for r in ev], dtype=float)
    n = len(dr)
    if n == 0:
        return {"n": 0, "hitRate": None, "avgFwdRet": None, "medianFwdRet": None,
                "tStat": None, "ic": None, "equityCurve": []}
    sd = float(dr.std(ddof=1)) if n > 1 else 0.0
    tstat = float(dr.mean() / (sd / math.sqrt(n))) if sd > 0 else None
    cum, eq = 1.0, []
    for r in sorted(ev, key=lambda r: r["as_of"]):
        cum *= (1.0 + sign * r["fwd"])
        eq.append(round(cum, 6))
    return {
        "n": n,
        "hitRate": round(float((dr > 0).mean()), 4),
        "avgFwdRet": round(float(dr.mean()), 6),
        "medianFwdRet": round(float(np.median(dr)), 6),
        "tStat": round(tstat, 3) if tstat is not None else None,
        "ic": _information_coefficient(ev),
        "equityCurve": eq,
    }


# ─── Backtest (events or trigger) ─────────────────────────────────────────────

def backtest(*, project: str, direction: str = "long", horizon: int = 5,
             events: list | None = None, trigger: dict | None = None,
             score_feature: str | None = None, score_params: dict | None = None,
             beta: float = 1.0) -> dict:
    """Run a backtest from explicit `events` or a `trigger`. Returns stats + per-event rows +
    featureVersions. Refuses a forward-looking `score_feature` (look-ahead guard)."""
    symbols = load_symbols(project)
    if events is None:
        if trigger is None:
            raise ValueError("backtest needs either events or a trigger")
        events = resolve_events_from_graph(project, trigger)

    # Look-ahead guard: a predictor used for IC must be point-in-time.
    if score_feature:
        spec = FEATURES.get(score_feature)
        if spec is None:
            raise ValueError(f"unknown score feature {score_feature!r}")
        if spec.forward_looking:
            raise ValueError(
                f"LOOK-AHEAD GUARD: scoreFeature {score_feature!r} uses a forward window and "
                f"cannot be a backtest predictor (it would leak the outcome). Refusing.")

    rows = []
    for e in events:
        tk, as_of = e["ticker"], e["as_of"]
        close = store.close_series(tk)
        bench = store.close_series(benchmark_for(tk, symbols))
        rows.append({
            "ticker": tk, "as_of": as_of,
            "fwd": forward_return(close, as_of, horizon=horizon),
            "ar": cum_abnormal_return(close, bench, as_of, horizon=horizon, beta=beta),
        })

    feat_versions = {"forward_return": FEATURES["forward_return"].version,
                     "cum_abnormal_return": FEATURES["cum_abnormal_return"].version}
    if score_feature:
        for r in rows:
            r["score"] = compute(score_feature, _ctx(r["ticker"], symbols),
                                 r["as_of"], score_params or {}).get("value")
        feat_versions[score_feature] = FEATURES[score_feature].version

    stats = compute_stats(rows, direction, horizon)
    skipped = [{"ticker": r["ticker"], "as_of": r["as_of"]} for r in rows if r["fwd"] is None]
    return {
        "project": project, "direction": direction, "horizon": horizon,
        "events": len(events), "evaluated": stats["n"], "skippedNoData": skipped,
        "stats": stats, "featureVersions": feat_versions, "pointInTime": True,
        "rows": [{"ticker": r["ticker"], "as_of": r["as_of"], "fwd": r["fwd"],
                  "ar": r["ar"], "score": r.get("score")} for r in rows],
    }


def backtest_proposal(proposal: dict, project: str) -> dict:
    """Backtest a signal proposal dict (the contract from the jarvis-signals skill)."""
    horizon = proposal.get("horizon", "5d")
    h = int(str(horizon).rstrip("dD")) if str(horizon).rstrip("dD").isdigit() else 5
    return backtest(
        project=project,
        direction=proposal.get("direction", "long"),
        horizon=h,
        trigger=proposal.get("trigger") or {},
        score_feature=(proposal.get("backtestConfig") or {}).get("scoreFeature"),
        score_params=(proposal.get("backtestConfig") or {}).get("scoreParams"),
    )


def _main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="backtest")
    ap.add_argument("--project", required=True)
    ap.add_argument("--proposal", help="path to a proposal JSON (uses its trigger)")
    ap.add_argument("--events", help="explicit events JSON: [{\"ticker\":..,\"as_of\":..}]")
    ap.add_argument("--direction", default="long")
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--score-feature", default=None)
    ap.add_argument("--out", help="write the result JSON here (default: stdout)")
    args = ap.parse_args(argv)

    if args.proposal:
        proposal = json.loads(pathlib.Path(args.proposal).read_text())
        res = backtest_proposal(proposal, args.project)
    else:
        events = json.loads(args.events) if args.events else None
        res = backtest(project=args.project, direction=args.direction, horizon=args.horizon,
                       events=events, score_feature=args.score_feature)
    out = json.dumps(res, indent=2, default=str)
    if args.out:
        pathlib.Path(args.out).write_text(out)
        print(f"wrote {args.out}: n={res['stats']['n']} hitRate={res['stats']['hitRate']}")
    else:
        print(out)


if __name__ == "__main__":
    _main()
