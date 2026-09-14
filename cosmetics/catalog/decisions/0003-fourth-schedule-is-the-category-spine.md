# 0003 — The Fourth Schedule is the category spine, not the Ninth

**Status** accepted · **Affects** s05_rules, `ProductCategory`

## Context
Both schedules were candidates for the canonical category vocabulary.

## Decision
The Fourth Schedule is CDSCO's own 80-entry category vocabulary — the regulator's answer
to "what counts as a lipstick". The Ninth Schedule's 37 rows are *standards that attach
to* those categories, not categories themselves. Both are loaded; the Fourth is the spine
and the Ninth hangs off it via `GOVERNED_BY`.

## Consequence
Retail and marketplace category strings are **tags**, mapped in
`ontology/category_map.yaml`. They never substitute for the spine (hard rule 9).
Both PDF OCR defects found in the schedules are documented in
`adapters/s05_ninth_schedule.py`, corrected in the adapter and never in `raw/`.
