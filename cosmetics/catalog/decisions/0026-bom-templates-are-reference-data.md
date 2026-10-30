# 0026 — BOM templates are tier-4 reference data that structure a request

**Status** accepted 2026-10-29 · **Affects** RFQ pack, `ProductCategory`, `Ingredient.functions`

## Context
A brand's request ("serum, 10k units, 30 days") breaks into lines:

- ingredients by function;
- primary pack, closure and secondary pack;
- a filler;
- tests.

The graph knows categories (Fourth Schedule) and ingredient functions (CosIng). Nothing
connects a category to the lines a product in it needs.

## Decision
A curated seed maps a product category to its function classes and BOM lines. For
example, a serum is:

- humectant, emollient, active, preservative, thickener, fragrance;
- dropper bottle, closure, carton, label;
- filler;
- stability and challenge test.

Each line points to the material vocabulary
([0018](0018-materials-are-joined-through-one-vocabulary.md)) and, where one exists, to a
relevant standard.

Templates are tier-4 reference data. They structure an RFQ pack and a supplier search.
They never claim anything about a supplier. A product needing a line is not evidence that
any firm supplies it.
