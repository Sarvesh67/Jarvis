# 0011 — Ingredient mentions are matched only on unambiguous INCI names

**Status** accepted · **Affects** s14b_supplies, `SUPPLIES`, `MENTIONS`

## Context
Finding ingredient names in free-text company web pages.

## What the data actually says
**"MILK", "SAND", "HONEY" and "TALC" are real INCI names and also ordinary English
words.** Matching them naively turns any page that mentions milk into an ingredient
supplier.

## Decision
Two rules:

1. Ambiguous INCI names are **excluded**, not guessed at.
2. A page must yield **≥ 3 distinct ingredients** before any of them is believed.

## Consequence
Recall is deliberately sacrificed for precision. A `SUPPLIES` edge is tier 3 — it means
the words co-occurred on a page the company controls. It is a lead, not an inventory.

## Amended 2026-10-29 — what "unambiguous" means, and where the ≥3 rule applies
The principle stands: ambiguous names are excluded, not guessed at. Its implementation
was excluding the wrong things, and it was the direct cause of the 2026-10-08 glycerin
miss.

- **GLYCERIN was on the ambiguous list.** So were MENTHOL, CAMPHOR, LANOLIN, BEESWAX,
  PARAFFIN, KAOLIN, GELATIN and UREA. These are substances, not ordinary English. They
  were listed because they also appear on brand pages, and a brand page is now rejected by
  S14b's catalogue-vs-storefront test before any of its matches is kept.
- **A 9-character minimum on one-word names** dropped 571 INCI names, among them SQUALANE,
  CARBOMER, LECITHIN, CAFFEINE, EDTA and every PEG-n. The minimum is now 4. The English
  words those names collide with are listed explicitly instead: bare elements, minerals,
  gems, textiles, foods, and packing materials such as POLYETHYLENE ("supplied in
  polyethylene bags").
- **No synonyms.** "Glycerine IP" on a supplier's page matched nothing. The needles are
  now `pipeline/materials.matching_keys()` ([0018](0018-materials-are-joined-through-one-vocabulary.md)):
  INCI names, CosIng's own synonyms and CAS numbers (check-digit validated). Each key
  resolves to exactly one INCI name, or it is dropped.
- **One-word names that run on are not counted.** Found by reading the glycerin evidence
  once it was matched. "GLYCEROL MONO STEARATE" is glyceryl stearate, "GLYCERIN SOAP BASE"
  is a soap base, and "GLYCERINE CREAM" is a brand's cream. A one-word name counts only
  where it stands alone at least once on the page.
- **≥ 3 distinct ingredients per site, not per page.** The code always applied the rule
  per host; this record said per page. Per site is the right rule: a catalogue split one
  product per page is still a catalogue, and S14 now follows those pages.

Measured on the same 4,308 landed pages: supply claims went from 804 claims over 53
suppliers to 2,471 over 238. Of all 2,471, 320 matched through a synonym and 149
through a CAS number. GLYCERIN went from 0 suppliers to 11. 798 of the 804 earlier claims
survive. The 6 that did not were shorter names consumed by a longer, correct one
(DIETHYLENE GLYCOL inside DIETHYLENE GLYCOL MONOETHYL ETHER).

## Amended 2026-10-30 — a name counts as an entry, not a mention

The catalogue crawl of 2026-10-29 took S14 from 4,308 to 7,339 landed pages, and the rules
above turned that into 10,123 claims over 545 suppliers. Reading samples of them showed
the rules were not enough once the pages were product pages and blogs rather than home
pages: on sites with 3-25 claims, about 40% of one-word matches and 35% of multi-word
matches were wrong. The name sat in a sentence ("palmarosa is high in geraniol", "a
mordant like tannic acid", "used in the production of polyvinyl chloride"), or a word
beside it made it something else ("4-nitro anisole", "formaldehyde free", "rose crystal
ex benzaldehyde"). Four rules were added, each from a class of error read in the
evidence, never from a single bad row:

- **Entry, not mention.** A name counts where it is an element of its own on the page
  (list item, cell, heading, link: a text fragment of at most 12 words), or, inside a
  longer block, where another substance or a CAS number is named within 12 words. A
  catalogue does one or the other; prose does neither.
- **Negated or named as a source.** NO, NON, WITHOUT, EX or FROM immediately before the
  name.
- **A substituent before, or an ester or salt word after.** AMINO, NITRO, CHLORO, METHYL,
  HYDRO, ISO and the like in front ("octyl methyl cinnamate"), or a following word ending
  in -ATE ("glycerol hydroxystearate", "castor oil sulphate"). A hydrate is the same
  substance ("dextrose monohydrate" is glucose), and a word that begins the next listed
  substance ends this one rather than renaming it.
- **Sites that sell finished goods.** Four or more distinct consumer or private-label
  phrases across a site ("paraben-free", "for all skin types", "private label", "launch
  your brand") mean it sells formulations. Its "Shea Butter Glycerin Body Cleanser" is
  not an offer of glycerin. One phrase proves nothing: "contract manufacturing" is
  ordinary custom-synthesis language for a chemical maker.

One rule was tried and rejected: requiring a recognised neighbour for every name. It
removed as many real claims as wrong ones (15 of 30 drops correct): a dye or aroma
catalogue's other entries are often not in CosIng, and a page given over to one product
has no neighbour at all.

Result: 8,868 claims over 486 suppliers, 6,542 of them for an ingredient with a cosmetic
function. GLYCERIN has 55 suppliers. On two uniform samples of 50 cosmetics-relevant
claims, 83 of 100 are a real offer of that substance (roughly ±8). What remains wrong is
mostly composition and specification tables, "raw material for ..." phrasing, and double
salts. Telling those apart needs the sentence read, not matched, and is not attempted
here. The rules are pinned by `test_supply_claims_come_from_entries_not_mentions`.
