# 0013 — `IN_CLUSTER_WITH` is deliberately not materialised

**Status** accepted · **Affects** pipeline/derive.py

## Context
"Which firms are in the same cluster as this one" is a common question and an obvious
candidate for a derived edge.

## What the data actually says
It is a **clique**. `Cluster(Ahmedabad-Vapi)` has ~17,000 neighbours; materialising the
edge writes ~17,000² relationships to express something the `Cluster` node already holds.
`msme/` learned this on one dense Surat cluster.

## Decision
Do not materialise it. A two-hop traversal through `LOCATED_IN` answers the question.

## Consequence
Graph context retrieval never expands out of a hub node beyond the first hop, and is
budgeted to ~60 nodes / ~120 edges. It reports in its own `trust` field that it is a
**neighbourhood, not a census** — you must not count or rank from it.
