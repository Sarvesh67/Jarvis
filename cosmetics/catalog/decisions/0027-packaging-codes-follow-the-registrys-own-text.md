# 0027 — A packaging NIC code is typed by the registry's own text, not by its class name

**Status** accepted 2026-10-29 · **Affects** `adapters/s17_udyam.py` PACKAGING, `load_neo4j.load_packaging`, `SUPPLIES_PACKAGING`, router packaging words

## Context
`SUPPLIES_PACKAGING` held 60,087 tier-1 edges over 10 component types. The NIC → type map
was written from the class names. Udyam returns its own description of every code it
assigns, so the map can be checked against the registry's own wording. It had not been.

## What the data actually says
Descriptions as Udyam returns them, with occurrences in the 119,120 kept rows:

| Code | Udyam's text | Rows | Was typed as | Packaging? |
|---|---|---|---|---|
| 23101 | glass in primary or semi-manufactured forms (sheets, plate glass), mirrors | 2,167 | `glass_primary` | **No** |
| 23109 | other glassware: articles used in construction such as glass blocks; clock or watch glasses, optical glass… glass beads | 2,951 | `glass_container` | **No** |
| 23103 | hollow glassware (bottles, jars etc.) for the conveyance or packing of goods | 159 | *(missing)* | **Yes** |
| 25910 | forging, pressing, stamping and roll-forming of metal; powder metallurgy | 6,398 | `metal_formed` | Generic metalwork |
| 25920 | machining; treatment and coating of metals | 8,200 | `metal_coated` | **No** |
| 25992 | containers such as tins and cans for food products and boxes | 124 | *(missing)* | **Yes** |
| 18115 | printing directly onto … flexographic plastic, glass, metal… | 254 | *(missing)* | **Yes** (decoration and labels) |
| 18119 | other printing … screen printing other than textile n.e.c. | 415 | — | Mostly not; signage and stationery |
| 17021 | corrugated paper and paperboard | 1,339 | — | No; board sold to box makers, not to brands |

Every `glass_container` edge (3,110) came from 23109, which is construction and optical
glass. The real bottle-and-jar code was not in the map. The metal codes 25910 and 25920
were 14,502 edges, 24.2% of the total.

## Decision
- **Untyped:** 23101, 23109, 25910 and 25920 no longer produce a `SUPPLIES_PACKAGING` edge.
  The firms stay in the graph: registry membership is still tier 1.
- **Added:** 23103 → `glass_container`, 25992 → `metal_container`, 18115 → `printed_packaging`.
- **Not added:** 18119 and 17021, for the reasons in the table.
- The edge is computed at load from the stored `nic_codes`, so it is corrected without
  re-ingesting. Each run retracts the previous run's edges first, as `derive.py` does.

## Consequence
- `glass_container` falls from 3,110 firms to the 23103 holders (≈159). That is a smaller
  number and a true one.
- "Tube" and "can" questions move off `metal_formed`: plastic and lami tubes sit under
  22203, cans under 25992.
- Adding the new codes to WANTED widens only the next sweep. Rows already kept are typed
  from codes they already carry.
