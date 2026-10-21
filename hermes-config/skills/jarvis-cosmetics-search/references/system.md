# Cosmetics graph — operational reference

Read this when a query behaves unexpectedly. It explains WHY the tool answers as it does.
It is deliberately free of frozen result lists: the graph is the source of truth and any
firm list copied into a document starts rotting immediately (an earlier version of this
file pinned "50 firms in Baddi" when the registry held 976).

## Where things live

| Store | Holds |
|---|---|
| Postgres `cosmetics` db (in `jarvis-postgres`) | every raw row, crosswalk, review queue, GST records, and **all person data** |
| Neo4j `cosmetics-neo4j` (:7474) | the reconciled graph — entities, typed edges, provenance |

Code: `/Users/sarvesh/Documents/Jarvis/cosmetics/` with its own `.venv` (Python 3.12).

## Why you must not write your own Cypher

The router's templates are the only reviewed queries. They encode guarantees that raw
Cypher silently loses:

- **Tier discipline** — tier 4 is inferred and may never answer a licence, compliance or
  restriction question.
- **Jurisdiction separation** — an EU annex restriction has no force in India, and a
  query that mixes them produces a confidently wrong compliance answer.
- **Blanket-note exclusion** — a handful of EU notes ("ban substances that deplete the
  ozone layer") attach to thousands of ingredients. They are held on a separate
  `SUBJECT_TO_GENERAL_RULE` edge precisely so `RESTRICTED_BY` always means a
  substance-specific restriction.

If the tool cannot answer the question, say so. That is a better outcome than an
unreviewed query that looks authoritative.

## Bounds, not truncation

Every template ends in `LIMIT $limit` (default 50). Pass `--limit N` for more and
`--rows 0` to print them all; `--json` always returns every row the limit allowed. If a
result feels short, raise the limit — do not conclude the tool is lossy.

## Known coverage gaps — state these, never paper over them

| Gap | Consequence |
|---|---|
| **State COS-8/COS-9 licence registers are not published in bulk** | Licence queries return almost nothing. Registry presence is NOT permission to manufacture. This is the single biggest gap. |
| No GSTIN verification provider | Trust level L3 is unreachable; most firms are registry-only and sit at trust 1 (Udyam membership). Only 54 companies carry a GSTIN at all. |
| Contract-manufacturing claims come from company websites | Most cluster MSMEs have no website, so absence is missing evidence, not absence of the capability. |
| Udyam self-declaration is noisy | A firm registers several activities; its name may reflect a different one. `evidence_basis` exposes this. |
| MCA NIC codes are incorporation-time | Never updated afterwards; roles derived from them are suffixed `_at_incorporation` and rank last. |

## Source status

| ID | Source | Gives |
|----|--------|-------|
| S1 | CDSCO approved manufacturing sites | facilities (mostly drug forms) |
| S3 | MCA company master | verified CIN, registered office, company status |
| S5 | Cosmetics Rules 2020 | category spine + BIS/IS standards + India restrictions |
| S6 | EU CosIng | INCI dictionary + EU annex restrictions |
| S7 | Open Beauty Facts | observed Indian products and their ingredients |
| S8/S9 | Trade-fair exhibitors, CHEMEXCIL | active companies, export activity, websites |
| S11 | NABL | accredited testing labs and their accreditation scope |
| S14/S14b | Company websites | contact, GSTIN, capabilities, ingredient supply claims |
| S17/S17b | Udyam MSME registry (national) | the long tail and packaging converters |
