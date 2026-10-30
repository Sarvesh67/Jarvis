# 0018 — Materials are joined through one vocabulary: INCI, synonym, CAS, HS, NIC

**Status** accepted 2026-10-29 · **Affects** s06_cosing, s14b_supplies, `pipeline/materials.py`, router, `PackagingComponent`

## Context
Every miss in the glycerin session was a vocabulary miss. The same material carries a
different name in each source:

| Source | Calls it |
|---|---|
| A supplier's page | "Glycerine IP" |
| CosIng (INCI) | GLYCERIN |
| INN | glycerol |
| CAS | 56-81-5 |
| Customs | HS 2905.45 |
| Udyam | 20293 or 20119, on the maker |

Nothing translated between them.

## What the data actually says
CosIng already carries most of the bridge, unused:

- an INN name on 839 entries;
- a semicolon list of chemical names and synonyms ("Glycerine; Glycerol (INN); Glycyl
  Alcohol; 1,2,3-Propanetriol");
- a CAS number on 16,507 of 36,023 rows (12,217 distinct).

## Decision
1. `Ingredient` stays keyed on `inci_name`. Synonyms (CosIng INN and chemical names, later
   filtered PubChem) and CAS numbers are **matching keys**. They find the INCI name. They
   are never displayed as a fact about it.
2. A synonym or CAS is used only when it maps to exactly one INCI name and is not itself
   another INCI name. A synonym of an excluded ambiguous INCI name is excluded with it. A
   CAS is used only if its check digit is valid.
3. `TradeName` -[:MAPS_TO_INCI]-> `Ingredient` is filled from sources that publish trade
   names, COSMOS first.
4. Packaging types come from a controlled list, each type carrying its NIC and HS codes.
   Until that list exists, the NIC map in `adapters/s17_udyam.py` is the list
   ([0027](0027-packaging-codes-follow-the-registrys-own-text.md)).
5. A curated table maps the top BOM materials to HS-6/8 codes for the benchmarks in
   [0024](0024-price-benchmarks-stay-out-of-the-graph.md).

## Consequence
`pipeline/materials.py` is the one place that decides what may be used as a needle.
S14b, the Stage 7 document linker and the router all read it. Phase 0 applies (1)–(2) to
S14b and to the router's ingredient resolution.
