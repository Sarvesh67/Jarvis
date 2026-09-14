# 0004 — Pair scoring renormalises over observable features

**Status** accepted · **Affects** pipeline/resolve.py

## Context
The spec weights entity-resolution features 0.45 name / 0.25 address / 0.20 contact /
0.10 category. That assumes all four are present on both sides.

## What the data actually says
A licence listing has no email and no self-declared role. Charging a pair for a feature
neither side could supply is evidence of a **thin source**, not of difference. Without
renormalisation the `OPERATES` join tops out at 0.70 — below the 0.92 threshold — and
silently never fires.

## Decision
Renormalise the weights over the features both sides can actually supply.

## Consequence
97 `OPERATES` edges before the fix, 822 after.
