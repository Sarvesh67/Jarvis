---
name: jarvis-signals
description: "Propose ONE testable, machine-executable trading signal for a Jarvis project from its knowledge graph, to be deterministically backtested and human-reviewed."
version: 1.0.0
platforms: [macos]
metadata:
  hermes:
    tags: [cognee, knowledge-graph, hedgefund, signals, alpha, backtest]
    related_skills: [jarvis-knowledge]
---

# Jarvis Signal Proposals

Turn a seed (an event or piece of news) into ONE concrete, **testable** trading-signal
hypothesis grounded in a project's knowledge graph. You propose; a deterministic engine
backtests it against price history; a human approves or rejects. You never invent numbers — you
produce a precise hypothesis a machine can re-run across history.

## When to use

- You're asked to propose / mine a trading signal from a seed event or news, or as part of the
  weekly high-value sweep. (For plain Q&A over the graph, use **jarvis-knowledge** instead.)

## The loop you're part of

```
seed → YOU query the graph + reason → write a proposal JSON → engine backtests it → human reviews
```

## Steps

1. **Query the graph.** Use the jarvis-knowledge query path to pull the entities, events and
   relationships around the seed:

   ```bash
   /Users/sarvesh/Documents/Jarvis/cognee/.venv/bin/python \
     /Users/sarvesh/Documents/Jarvis/cognee/query.py hedgefund "<entities/events around the seed>" 2>/dev/null
   ```

   Run it more than once with different phrasings if the first pull is thin.

2. **Reason** over the context and form ONE signal hypothesis: a direction (long/short), a
   horizon, and a precise, machine-matchable *trigger* (what pattern, on which ticker(s)).

3. **Write the proposal** as a single valid JSON object to the exact inbox path you are given
   in the prompt. Use your file/shell tools. The schema:

   ```json
   {
     "id": "<the id from the prompt>",
     "seed": "<the seed>",
     "thesis": "<one sentence>",
     "direction": "long",
     "horizon": "5d",
     "universe": ["TATAMOTORS.NS"],
     "trigger": {
       "match": {
         "tickers": ["TATAMOTORS.NS"],
         "entityAny": ["Morgan Stanley", "Nomura"],
         "keywordAny": ["upgrade", "overweight", "buy"]
       }
     },
     "rationale": "<why, grounded in the graph context>",
     "sourceNodes": []
   }
   ```

## Rules

- Use ONLY tickers/entities that actually appear in the graph context — **never invent a
  ticker**. NSE tickers end in `.NS` (e.g. `TATAMOTORS.NS`).
- `trigger.match` is what the backtester re-runs across history, so make it precise. `tickers`
  restricts the universe; `entityAny`/`keywordAny` filter which historical docs count as events.
- The graph stamps every doc with its ticker + date (`as_of`), so a good trigger resolves to a
  clean set of dated events.
- Propose exactly ONE signal per run. Write ONLY the JSON file; just confirm in your reply that
  you wrote it (don't paste the JSON back).
- If the graph has nothing usable for the seed, say so plainly and do not fabricate a signal.

## What happens next (not your job)

The orchestrator backtests your trigger deterministically (hit-rate, IC, sample size, equity
curve) and moves the proposal to the review queue. You do not compute any of those numbers.
