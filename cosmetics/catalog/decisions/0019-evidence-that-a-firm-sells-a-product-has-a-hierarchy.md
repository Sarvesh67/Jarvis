# 0019 — Evidence that a firm sells a product has a hierarchy, and a card shows its rung

**Status** accepted 2026-10-29 · **Affects** `SUPPLIES`, `SUPPLIES_PACKAGING`, `PERMITTED_TO_MAKE`, router

## Context
Several sources will say "firm F supplies material M", and they are not equal. Regulators
hold the strongest evidence, and the open web holds most of the volume.

## Decision
Evidence that a firm sells a product ranks in four rungs, by tier:

| Tier | Evidence |
|---|---|
| 1 | Environmental-clearance and pollution-board product tables; certifier registers (COSMOS) for the certified material; state licence lists |
| 2 | The firm's own catalogue or label; association product lists; a distributor's principals page; an RFQ response |
| 3 | Exhibitor categories; scraped listings |
| 4 | NIC → material inference |

A card shows, for each material, the strongest rung held. Tier 4 is labelled "likely".
It ranks but never answers "who makes X" alone. A tier-1 product table says what a unit
was *permitted* to make, not that it makes it now
([0020](0020-a-clearance-or-consent-is-a-ceiling-not-production.md)).

## Consequence
S14b stays tier 3, because it finds words on a page. It is a lead, as
[0011](0011-ingredients-matched-only-on-unambiguous-inci.md) says. Today, before any
tier-1 product source is loaded, a supplier answer rests on tier 3, and the answer says so.
