# 0014 — `SUBSTITUTE_FOR` is emitted only from restricted ingredients

**Status** accepted · **Affects** pipeline/derive.py, s06_cosing

## Context
Ingredients sharing a function are candidate substitutes for each other. The full
cross-product is available from CosIng's `functions` field.

## What the data actually says
The full cross-product is a **function-sized clique** — every preservative linked to every
other preservative — and answers a question nobody asks.

The question people actually ask is: *"this is restricted, what else does the job?"*

## Decision
Emit `SUBSTITUTE_FOR` only **from** restricted ingredients, top-10 by shared function.

## Consequence
1,720 edges instead of millions. Tier 4 — it suggests a direction for formulation work
and never asserts that a substitute is permitted. Check the target's own
`RESTRICTED_BY` / `SUBJECT_TO_GENERAL_RULE` edges, pinned to the right `jurisdiction`
(hard rule 2), before acting on one.
