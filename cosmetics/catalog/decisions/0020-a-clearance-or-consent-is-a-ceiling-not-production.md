# 0020 — An environmental clearance or a consent is a ceiling, not production

**Status** accepted 2026-10-29 · **Affects** s21 (EC minutes), s22 (pollution boards), `Licence`, `PERMITTED_TO_MAKE`

## Context
Three kinds of regulator document carry product tables with a capacity for each product,
the only tier-1 statement of what a chemical unit makes:

- environmental-clearance minutes (EAC Industry-3, state SEAC);
- Maharashtra pollution-board committee minutes;
- Gujarat consent orders.

Example: *PCl3, 10,000 / 20,000 / 30,000 TPA* (existing / proposed / total).

## What the data actually says
A clearance grants permission to produce *up to* a capacity. It does not report output.
Units are cleared years before they build. They also apply for products they never make,
and they keep clearances for lines they have shut.

## Decision
1. A clearance or consent is a `Licence` node with
   `kind: env_clearance | pcb_consent`, its document URL and its date.
2. A new edge, `Licence -[:PERMITTED_TO_MAKE]-> Ingredient`, carries the capacity and
   unit. It exists only for an unambiguous INCI match, per
   [0011](0011-ingredients-matched-only-on-unambiguous-inci.md). Other products stay in
   Postgres `licensed_product`.
3. `PERMITS` (Licence → ProductCategory, cosmetics manufacture) is untouched.
   [0012](0012-permits-is-legitimately-empty.md) still holds: an environmental permission
   is not a cosmetics licence.
4. Displayed as "cleared to make P, up to C, as of D". Never "makes P" or "produces C".

## Consequence
A golden query asserts that `PERMITTED_TO_MAKE` never answers a `PERMITS` question.
Coverage skews to larger synthetic-organics units above the clearance thresholds. That is
the coverage, and it is reported as such.
