# 0008 — A district name match must agree on state

**Status** accepted · **Affects** s17b_udyam, s15_ref, ontology/cluster_map.yaml

## Context
District names were treated as spelling variants of one another to absorb transliteration
noise.

## What the data actually says
**Raigarh (Chhattisgarh) is not Raigad (Maharashtra).** Listing the former as a variant of
the latter put nine Chhattisgarh firms inside the Palghar-Tarapur wedge cluster.

The same trap, all real Indian district pairs:

| District | States |
|---|---|
| Aurangabad | Maharashtra / Bihar |
| Bilaspur | Himachal Pradesh / Chhattisgarh |
| Hamirpur | Himachal Pradesh / Uttar Pradesh |
| Raigarh / Raigad | Chhattisgarh / Maharashtra |

## Decision
A district name match requires state agreement. An **unknown** state still matches — a
missing state is not evidence of a mismatch, only absence of evidence.

## Consequence
Cluster assignment is trustworthy enough to rank on. It is still tier 4 and still never
answers a compliance question (hard rule 3).
