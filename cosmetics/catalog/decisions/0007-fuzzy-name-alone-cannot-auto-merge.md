# 0007 — A fuzzy name match alone cannot auto-merge

**Status** accepted · **Affects** pipeline/resolve.py, s09_chemexcil

## Context
CHEMEXCIL publishes a name and a website and nothing else. Name is the only feature
available for 4,005 rows.

## What the data actually says
Name similarity on raw strings is dominated by generic vocabulary. "SUDAMA ENTERPRISE"
and "RAMU ENTERPRISE" share a pincode and score 0.81 on `token_set_ratio` because of one
generic word.

## Decision
Two guards, both required:

1. **Score on distinctive tokens.** Strip generic business and sector vocabulary before
   scoring. A pair whose only common ground is generic can auto-merge only when something
   else corroborates at ≥ 0.9.
2. **Name-only merges need an exact normalised match**, ≥ 2 tokens, and a name not
   already seen at two different pincodes.

## Consequence
"S.P. Pharmaceuticals" and "S P Pharmaceuticals" at one address still merge correctly.
Without guard 2, every generic name in a 4,000-row directory collapses into one entity.
