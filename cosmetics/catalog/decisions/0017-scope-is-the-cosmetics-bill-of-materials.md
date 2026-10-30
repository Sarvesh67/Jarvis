# 0017 — The graph widens to the cosmetics bill of materials, not to "chemicals"

**Status** accepted 2026-10-29 · **Affects** s17b_udyam, every adjacent-industry source, `load_neo4j.py`

## Context
On 2026-10-08 a buyer asked for glycerin manufacturers in Ahmedabad and got nothing. The
Udyam slice keeps no chemical NIC code, CHEMEXCIL has no products, and S14b excludes
GLYCERIN. That session ruled that bringing raw-material suppliers in is a scope change,
to be recorded as a decision before any source is widened. This is that record.
`SOURCES_PLAN.md` has the full reasoning.

## What the data actually says
A cosmetics buyer never asks for "chemical companies". They ask for a supplier for one
line of a bill of materials: an ingredient, a pack component, a filler, a lab.

Udyam's chemical classes are mostly generic. 20119 ("organic and inorganic chemical
compounds n.e.c.") and 20299 ("various other chemical products n.e.c.: antiknock
preparations, anti-freeze…") cover paint additives as readily as surfactants. Rows already
kept for a cosmetics code carry them anyway: 20299 on 540 rows, 20119 on 462, 46691 on 219.

## Decision
1. Adjacent-industry firms are landed and staged in Postgres in full.
2. A firm enters the answerable graph only when one of these holds:
   - **(a)** a source links it to a BOM material: a certifier register, a clearance or
     consent product table, its own catalogue, a distributor page or an RFQ response; or
   - **(b)** it holds a code the graph already keeps (the 2023x class, or packaging per
     [0027](0027-packaging-codes-follow-the-registrys-own-text.md)), or one of two curated
     supplier codes:
     - 20118, synthetic aromatic products;
     - 20293, essential oils and the chemical modification of oils and fats.
3. Generic codes (20119, 20299, 46691, 46693) stay staged until (a) applies.

Every promoted firm carries the reason it was promoted.

**Why 20233 is not on the list.** The plan proposed it as "where surfactant makers sit".
It is already kept, as part of the 2023x class, and `derive.py` already refuses to read it
as cosmetics: it is detergent manufacture, and 4,429 kept rows carry it. A detergent
plant is no more a surfactant supplier than it is a cosmetics plant. A surfactant maker
gets in through (a).

## Consequence
This mirrors [0001](0001-s1-is-not-the-facility-spine.md) for pharma and msme/'s rule that
bulk stays outside the graph. It is reversible: staging keeps every row, so widening the
rule later costs a reload, not a re-sweep. A tier-4 NIC → material inference may rank a
promoted firm but never answers "who makes X" alone
([0019](0019-evidence-that-a-firm-sells-a-product-has-a-hierarchy.md)).
