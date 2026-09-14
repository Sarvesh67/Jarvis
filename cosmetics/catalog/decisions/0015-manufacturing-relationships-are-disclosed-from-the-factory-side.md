# 0015 — A manufacturing relationship is disclosed from the factory's side, never the brand's

**Status** accepted · **Affects** s19_filings, THIRD_PARTY_MANUFACTURED_BY, LOAN_LICENCE_WITH

## Context
`THIRD_PARTY_MANUFACTURED_BY` is declared in the ontology and holds **0 edges**.
[0002](0002-loan-licence-direction-is-unknown.md) routes every "who manufactures for whom"
question to it, because the CDSCO register gives a pairing with unknown direction. Nothing
has ever fed it.

SEBI filings looked like the answer: free, full-text, legally attested, and the chunking
and entity-linking machinery already exists for the Cosmetics Rules corpus.

## What the documents actually say
Two filings were read end to end rather than sampled.

**Brand side — Honasa Consumer (Mamaearth) DRHP, 1.78M characters.** The company
outsources *all* manufacturing. It discloses that it sourced from **37 contract
manufacturers**, and that the top three were 75.81% / 81.95% / 70.97% / 56.53% of traded
goods purchases across four periods. It names **none of them**. Zero `M/s` occurrences in
the whole document. The only company-shaped name within 400 characters of any
contract-manufacturing sentence is Honasa itself.

**Factory side — Akums Drugs and Pharmaceuticals RHP, 2.09M characters.** Names
counterparties freely: Ipca Laboratories, Alembic Pharmaceuticals, Alkem Laboratories,
Blue Cross Laboratories, Mylan Pharmaceuticals, Cure Healthcare, Maxcure Nutravedics,
Systopic Laboratories, Nicholas Healthcare.

## Why the asymmetry
It is not editorial habit, it is what SEBI requires. A filer must disclose **customer
concentration** because losing a large customer is a material risk to it. Supplier
identity is not a required disclosure and is commercially sensitive, so a brand discloses
the *shape* of its dependency — how many, what share — and withholds the names.

**So the edge is only ever visible from the manufacturer's filing, pointing outward at its
customers.** Any plan that starts from the brands is looking down the wrong end.

## Decision
Do not build the filings corpus for this project.

The mechanism works, but the addressable universe does not. Akums is pharma-dominant: it
mentions cosmetics 86 times and the Cosmetics Rules 4 times, yet every named counterparty
is a pharmaceutical firm, and the only names near a cosmetics sentence are its own
Uttarakhand subsidiaries. Indian cosmetics contract manufacturing is overwhelmingly
proprietorships and partnerships — the same structural fact
[0006](0006-udyam-is-the-spine-partitioned-by-pincode.md) rests on — so almost none of
these firms will ever file with SEBI. The expected yield is a handful of edges for an
adapter, a PDF corpus and an entity-resolution pass.

## Also checked, also negative
DRHPs do **not** carry licence numbers. The "Government and Other Approvals" section lists
licence *types* in prose ("License for manufacture for sale or distribution of drugs under
the Drugs and Cosmetics Act, 1940"), never numbers, issuing authority or validity. So this
corpus does not help with the COS-8 gap either — see `sources/s02_state_fda.yaml`.

## Consequence
`THIRD_PARTY_MANUFACTURED_BY` stays empty, and that is now a recorded finding rather than
an unexamined gap. It is the same commitment as
[0012](0012-permits-is-legitimately-empty.md): an empty edge is a real answer.

If it is ever revisited, the trigger is a **cosmetics CDMO filing with SEBI**, and the
adapter reads that one filing outward. It is not a corpus-scale exercise.
