# 0005 — `roles_claimed` is never scored as a resolution discriminator

**Status** accepted · **Affects** pipeline/resolve.py, s08_exhibitors, s09_chemexcil

## Context
Role overlap looks like a natural resolution feature: two rows claiming the same role are
more likely the same firm.

## What the data actually says
Every source stamps its **own** role vocabulary. Two rows for the same company from two
sources have disjoint role sets *by construction*. Galaxy Surfactants existed twice —
once as an exhibitor, once as a CHEMEXCIL member — because the disjoint role sets pushed
the pair below the merge threshold.

## Decision
Role overlap counts as positive evidence. Its **absence counts as nothing**, never as
negative evidence.

## Consequence
Follows from hard rule 1: `roles[]` is additive and unioned across sources, never a label.
