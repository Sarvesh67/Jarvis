# 0024 — Price benchmarks stay out of the graph and are never shown as a quote

**Status** accepted 2026-10-29 · **Affects** s28 (tradestat), s29 (WPI), Postgres `price_benchmark`

## Context
A buyer wants to know whether a quote is fair. Two free official series help:

- tradestat's HS-8 import value and quantity, which give a unit value;
- the 2022-23 WPI item indices, which give a trend.

## What the data actually says
An import unit value averages every grade, pack size and origin under one HS-8 code for a
month. It is a ceiling and a trend, not a price anyone will sell at. WPI is an index with
no unit. It has items for fatty acid, surface-active agents, castor oil, plastic bottles,
closures and labels, but **none for glycerin, petrolatum or white oil**.

## Decision
1. `price_benchmark(hs8, month, value_inr, qty, unit, unit_value, source)` and
   `wpi_item(item_code, month, index)` live in Postgres, never in Neo4j. This is the same
   rule msme/marketdata keeps: numbers stay out of the graph.
2. They are shown as "import unit value, HS 2905.45, <month>" or "price index". They are
   never shown as a price or a quote, and never beside a supplier's name as if that
   supplier charges it.

## Consequence
Real prices exist only as quotes on our own rail
([0025](0025-the-rfq-rail-is-a-source.md)), visible to the buyer who received them.
