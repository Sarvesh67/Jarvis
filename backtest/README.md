# backtest — deterministic signal evidence engine

Turns a signal **proposal** (a hypothesis in machine-executable form) into **evidence** with zero
LLM calls. Runs in the `marketdata` venv (it needs duckdb/pandas/numpy + the light falkordb client).

## Flow

```
proposal.trigger ──► resolve_events_from_graph() ──► [(ticker, as_of), ...]
                                                          │
                                                          ▼
                              marketdata.features: forward + abnormal returns
                                                          │
                                                          ▼
                       compute_stats(): n, hitRate, avgFwdRet, tStat, IC, equityCurve
```

Events come from the graph's stamped `ticker:`/`asof:` NodeSets (Phase 2), or are passed
explicitly (used by tests for fully-deterministic checks).

## Discipline

- **Look-ahead guard.** The forward return is the *outcome* (legitimately post-`as_of`). Any
  feature used as a *predictor* (the IC `scoreFeature`) must be point-in-time; a `forward_looking`
  feature passed as a predictor is **refused**. This is the leakage firewall.
- **Versioned.** Output stamps `featureVersions` so a later formula tweak can't silently
  re-interpret an old backtest.

## Usage

```bash
PY=../marketdata/.venv/bin/python   # from repo root: marketdata/.venv/bin/python

# From a proposal's trigger:
$PY -m backtest.engine --project hedgefund --proposal proposal.json

# Explicit events (deterministic):
$PY -m backtest.engine --project hedgefund \
    --events '[{"ticker":"TATAMOTORS.NS","as_of":"2024-07-03"}]' --direction long --horizon 5

$PY -m pytest backtest/tests/ -q
```

`backtest_proposal(proposal, project)` is the entry point the Phase-5 orchestrator calls.
