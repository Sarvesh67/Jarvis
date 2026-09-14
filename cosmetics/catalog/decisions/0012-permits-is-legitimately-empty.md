# 0012 — `PERMITS` is legitimately empty

**Status** accepted · **Affects** s01_cdsco, s02_state_fda, coverage reporting

## Context
`PERMITS` would connect a Licence to the ProductCategory it authorises — the edge that
answers "is this firm allowed to make this".

## What the data actually says
CDSCO's listing **has no category field**. No licence→category edge can be asserted at
tier 1 from any source currently held. The state FDA sources that would carry it (S2)
publish no bulk list.

## Decision
Leave the edge empty and let the coverage table report **0%**.

## Consequence
This is the project's central honesty commitment, and the reason to resist the obvious
shortcuts:

- An empty edge with a 0% coverage row is **better** than a populated edge that implies
  permission the data cannot evidence.
- `SERVES_CATEGORY` (tier 4, derived from NIC activity codes) provides a **ranking** path
  in the meantime. It suggests; it never permits.
- Hard rule 3 and the `tier4_never_answers_compliance` golden query enforce the
  distinction mechanically, so it cannot erode by accident.

See also [0001](0001-s1-is-not-the-facility-spine.md).
