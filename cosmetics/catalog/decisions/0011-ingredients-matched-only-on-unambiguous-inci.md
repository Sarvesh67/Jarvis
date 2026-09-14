# 0011 — Ingredient mentions are matched only on unambiguous INCI names

**Status** accepted · **Affects** s14b_supplies, `SUPPLIES`, `MENTIONS`

## Context
Finding ingredient names in free-text company web pages.

## What the data actually says
**"MILK", "SAND", "HONEY" and "TALC" are real INCI names and also ordinary English
words.** Matching them naively turns any page that mentions milk into an ingredient
supplier.

## Decision
Two rules:

1. Ambiguous INCI names are **excluded**, not guessed at.
2. A page must yield **≥ 3 distinct ingredients** before any of them is believed.

## Consequence
Recall is deliberately sacrificed for precision. A `SUPPLIES` edge is tier 3 — it means
the words co-occurred on a page the company controls. It is a lead, not an inventory.
