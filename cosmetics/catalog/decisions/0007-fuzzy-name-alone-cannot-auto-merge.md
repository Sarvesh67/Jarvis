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

## Amended 2026-10-29 — a website is contact evidence; the generic list is not widened
Adjacent-industry sources (COSMOS, brand sites, distributor pages, association lists)
publish a name and often a website, and nothing else. A shared registrable domain now
counts in the `contact` feature, at the weight of a shared phone or email, and is a
blocking key. It cannot merge anything on its own. Name plus contact is 0.65 of the
weight mass, below the 0.70 corroboration floor, so a name-and-website pair still needs
an address or an identifier. Group sites are why it can't count for more: atul.co.in,
ariesgroup.in and neogenchem.com each carry 3–4 distinct legal names in CHEMEXCIL.
Marketplace, directory and social hosts (indiamart.com, zaubacorp.com, facebook.com, …)
are not a key at all.

SOURCES_PLAN proposed widening the generic-token list for the new sectors (PLASTICS,
PACKAGING, DYES, INTERMEDIATES …). **Not done.** A generic token helps when it is the only
thing two names share, and it hurts when it is the only thing that tells them apart.
"MEGHMANI DYES INTERMEDIATES" and "MEGHMANI ORGANICS" are different companies with
distinct CINs. ORGANICS is already generic. Adding DYES and INTERMEDIATES would reduce
both names to "MEGHMANI" and score them 1.0. CHEMICALS needs nothing: `normalize.py`
already maps it to CHEM, which is generic.
