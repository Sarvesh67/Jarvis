# 0016 — An entity seed may never be empty

**Status** accepted · **Affects** every source without a CIN or GSTIN — which is most of them

## Context
`resolve.py` derives an entity id by hashing a natural key:
`cin or gstin or pan or f"{norm_name}|{pincode}"`. `norm_name` is written by Stage 2
(`normalize.py`); adapters insert it as NULL.

## What the data actually says
Run Stage 3 before Stage 2 and every unnamed row seeds on `"|<pincode>"` — one hash per
pincode. Each becomes a **single company holding every firm in that pincode**. The
crosswalk is append-only, so a later, correct run cannot repair it: the rows are already
assigned and re-resolution skips them.

That had happened, and had been invisible for the life of the build:

| | |
|---|---|
| company entities before | 45,913 |
| ...of which were pincode buckets | **4,446** |
| raw rows frozen inside them | **91,392** (67% of the corpus) |
| widest single entity | 908 distinct firm names in pincode 110039 |
| company entities after repair | **132,029** |

The false-merge detector could not see it. It tested the one thing these rows do not
have — a CIN — so 4,446 buckets passed it every run.

It surfaced sideways. A NABL query attributed one cosmetics accreditation to an entity
holding 124 unrelated laboratories (Marico's product lab, a Star Cement testing unit, an
incubation council), which is only strange if you already expect a lab to be one lab.

## Decision
1. `canonical_seed` **raises** on an empty seed. It is not a fallback value.
2. Stage 3 preflights: a source whose rows are mostly unnamed has not been through
   Stage 2, and that is an error, not something to work around. A handful of unnamed rows
   is junk in the registry — those rows are skipped, because a row with no name and no
   identifier is not an entity.
3. The false-merge detector also fails on **name fan-out**. A firm may appear under a few
   spellings across sources; an entity carrying more than 12 distinct normalised names is
   not a firm. After the repair the widest entity carries 6.
4. `resolve.py --reseed-empty` retires bucketed crosswalk rows and re-resolves them,
   superseding rather than deleting (hard rule 6).

## Consequence
The headline company count roughly triples, because it was never one company per firm.
Nothing about coverage improved — the same evidence is simply attached to the right
number of entities. Per-firm coverage percentages therefore **fall**: pincode coverage
reads 97.5% of 131,662 where it read 93.2% of 45,546, and website coverage falls from
4.9% to 1.7% for the same 2,215 websites. The earlier percentages were computed against
a denominator two thirds too small.

11,730 pairs went to the review queue, which is the designed destination for a genuine
ambiguity rather than a number to drive to zero.
