# 0001 — S1 (CDSCO) is not the cosmetics facility spine

**Status** accepted · **Affects** s01_cdsco · **Found by** building it

## Context
The build spec named the CDSCO approved-manufacturing-sites register as the facility
spine for the cosmetics graph.

## What the data actually says
`num_total_records=911` — the crawl is complete, not truncated. Of those 911 rows,
exactly **3** are cosmetics-regime after a junk row is dropped. The remaining 907 are
drug licences. A fourth apparently-cosmetics row was a portal test entry (`Testing123`,
Haryana). The overlap argument for keeping the drug rows is weaker than it looks: of the
491 firms on the register, **7** appear in any cosmetics source.

## Decision
The cosmetics licence layer must come from state FDAs (S2), which publish no bulk list.
Pharma rows are kept in Postgres but held out of the answerable graph unless they stand
in a loan relationship. `COSMETICS_KEEP_PHARMA=1` loads them all, for analysis only.

## Consequence
S2 becomes the highest-value missing source in the project, and `PERMITS` is empty —
see [0012](0012-permits-is-legitimately-empty.md).
