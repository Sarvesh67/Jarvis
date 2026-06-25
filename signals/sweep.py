"""Weekly high-value sweep — the example scheduled Dispatch task.

Deterministically ranks the graph's stamped doc-events and proposes signals for the top-N. No LLM
in the *selection* (pure ranking math); the per-pick proposal then runs the agent via orchestrate.

Selection (see plan):
    score = w_move·|pricemove_z| + w_recency·decay(age)
  - pricemove_z : |car_zscore| from the timeseries store (the same feature used at ingest).
  - recency     : exponential decay on the doc's as_of age.
  Then DEDUP by ticker (don't mine the same name twice) and take the top-N as seeds.

Runs in the marketdata venv (graph + features). Invoked manually or on a schedule from the
Dispatch tab — never automatically.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys
from datetime import date, datetime, timezone

from falkordb import FalkorDB

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from backtest.engine import benchmark_for, load_symbols  # noqa: E402
from marketdata import store  # noqa: E402
from marketdata.features import car_zscore  # noqa: E402
from signals import orchestrate  # noqa: E402

W_MOVE = 1.0
W_RECENCY = 0.5


def _say(m: str):
    print(m, flush=True)


def _events(project: str) -> list[tuple[str, str]]:
    g = FalkorDB(host="127.0.0.1", port=6379).select_graph(f"{project}_graph")
    rows = g.query(
        "MATCH (tk:NodeSet)-[:belongs_to_set]-(c)-[:belongs_to_set]-(af:NodeSet) "
        "WHERE tk.name STARTS WITH 'ticker:' AND af.name STARTS WITH 'asof:' "
        "RETURN DISTINCT tk.name AS t, af.name AS a"
    ).result_set
    return [(t.split("ticker:", 1)[1], a.split("asof:", 1)[1]) for t, a in rows]


def _chunk_blurb(project: str, ticker: str, as_of: str) -> str:
    g = FalkorDB(host="127.0.0.1", port=6379).select_graph(f"{project}_graph")
    rows = g.query(
        "MATCH (tk:NodeSet {name:$t})-[:belongs_to_set]-(c:DocumentChunk)-[:belongs_to_set]"
        "-(af:NodeSet {name:$a}) RETURN c.text LIMIT 1",
        params={"t": f"ticker:{ticker}", "a": f"asof:{as_of}"},
    ).result_set
    return (rows[0][0] if rows and rows[0] and rows[0][0] else "")[:600]


def _recency(as_of: str, today: date) -> float:
    try:
        d = datetime.strptime(as_of, "%Y-%m-%d").date()
    except ValueError:
        return 0.0
    return math.exp(-max(0, (today - d).days) / 365.0)


def rank(project: str, *, horizon: int = 5, vol_window: int = 60, today: date | None = None) -> list[dict]:
    symbols = load_symbols(project)
    today = today or datetime.now(timezone.utc).date()
    scored = []
    for ticker, as_of in _events(project):
        close = store.close_series(ticker)
        bench = store.close_series(benchmark_for(ticker, symbols))
        z = car_zscore(close, bench, as_of, horizon=horizon, vol_window=vol_window)
        move = abs(z) if z is not None else 0.0
        scored.append({"ticker": ticker, "as_of": as_of, "z": z,
                       "score": W_MOVE * move + W_RECENCY * _recency(as_of, today)})
    # dedup by ticker, keep the highest-scoring event per ticker
    best: dict[str, dict] = {}
    for s in sorted(scored, key=lambda x: x["score"], reverse=True):
        best.setdefault(s["ticker"], s)
    return sorted(best.values(), key=lambda x: x["score"], reverse=True)


def sweep(project: str, *, top_n: int = 3, horizon: str = "5d", dry_run: bool = False) -> dict:
    h = int(str(horizon).rstrip("dD")) if str(horizon).rstrip("dD").isdigit() else 5
    ranked = rank(project, horizon=h)
    picks = ranked[:top_n]
    _say(f"sweep: {len(ranked)} ticker-events ranked, taking top {len(picks)}")
    for p in picks:
        zt = f"{p['z']:.2f}" if p["z"] is not None else "n/a"
        _say(f"  • {p['ticker']} @ {p['as_of']}  score={p['score']:.3f} z={zt}")
    if dry_run:
        return {"ranked": len(ranked), "picks": picks, "proposed": []}

    proposed = []
    for p in picks:
        blurb = _chunk_blurb(project, p["ticker"], p["as_of"])
        seed = (f"High-value sweep pick: {p['ticker']} showed an unusual move around {p['as_of']} "
                f"(abnormal-return z≈{p['z']:.2f}). Driving context: {blurb}"
                if p["z"] is not None else
                f"Sweep pick: {p['ticker']} around {p['as_of']}. Context: {blurb}")
        _say(f"proposing for {p['ticker']}…")
        res = orchestrate.propose(project, seed, horizon=horizon, direction="long")
        proposed.append({"ticker": p["ticker"], "ok": res.get("ok"), "id": res.get("id")})
    _say(f"SWEEP_DONE: proposed {sum(1 for x in proposed if x['ok'])}/{len(picks)}")
    return {"ranked": len(ranked), "picks": picks, "proposed": proposed}


def _main(argv=None):
    ap = argparse.ArgumentParser(prog="signals.sweep")
    ap.add_argument("--project", required=True)
    ap.add_argument("--top-n", type=int, default=3)
    ap.add_argument("--horizon", default="5d")
    ap.add_argument("--dry-run", action="store_true", help="rank + print picks, don't run the agent")
    args = ap.parse_args(argv)
    sweep(args.project, top_n=args.top_n, horizon=args.horizon, dry_run=args.dry_run)


if __name__ == "__main__":
    _main()
