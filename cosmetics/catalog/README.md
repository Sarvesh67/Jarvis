# catalog/ — what this data is, and what it may be used to claim

The graph answers *questions*. This directory answers *"should I believe that answer?"*

It exists because the load-bearing knowledge about this dataset is not in the data. It is
things like: the CDSCO register is 99.7% drug licences, `PERMITS` is empty on purpose,
the evidence grade never ranks a supplier, and MILK is an INCI name.
Someone who queries the graph without knowing those will get a confident wrong answer and
have no way to tell.

Read this directory before querying. That goes for people and for agents.

## Layout

| Path | What it is | Written by |
|---|---|---|
| `sources/*.yaml` | One card per acquired source — what it is, what it answers, its defects, and the guardrails on using it | hand |
| `decisions/*.md` | Why the build works the way it does, each anchored to the evidence that forced it | hand |
| `glossary.yaml` | Domain and project vocabulary, with the traps | hand |
| `datasets/*.yaml` | Per-table and per-label notes, where a card is not enough | hand |
| `snapshots/` | Dated measurements of the live stores | `pipeline/snapshot.py` |
| `_schema/source-card.yaml` | The shape of a source card | hand |

**Judgement is hand-written; measurement is generated.** Nothing here rewrites itself.
A card that silently updated its own numbers could not be reviewed, and rewriting the YAML
would destroy the comments that carry the judgement.

## Keeping it honest

```bash
make catalog          # write today's snapshot
make catalog-check    # fail if a card disagrees with the live stores
```

`catalog-check` compares every card's measured fields — `volume.raw_rows`,
`acquisition.payloads`, `bytes`, `first_seen`, `last_seen` — against Postgres, Neo4j and
`raw/` on disk, with a 2% tolerance. When it fails, **edit the card. Do not edit the data
to match the card.**

## If you are an agent

Read in this order, and do not skip step 1.

1. **`glossary.yaml`** — or you will misread `trust_grade`, `tier`, and at least one
   ingredient name.
2. **The `do_not` block of every source card you are about to touch.** These are not
   style notes. They encode the hard rules, and several of them are enforced mechanically
   by `validate/checks.py` and the Cypher guard — a query that violates one gets rejected
   rather than answered.
3. **`pipeline/schema_card.py`** (`make schema`) — the live labels and properties,
   generated from the graph, never hand-written. Read it before writing any Cypher.
4. **`snapshots/latest.json`** — current counts, if you need to say how much of something
   there is.

The four rules that cause the most wrong answers here:

- **Tier decides what a fact may claim.** Tier 1 is the regulator and is the only tier
  that may answer a licence or compliance question. Tier 2 is a company's own claim,
  tier 3 is something observed on a page, tier 4 is inferred — those three rank and
  suggest, never permit.
- **The evidence grade is not a quality score, and it never ranks.** Read `trust_grade` (flagged, G0–G4) and the facets `trust_licence` / `trust_activity` / `trust_track`; `trust_level` is deprecated. Filter licences with `trust_licence = 'verified'`.
- **Every `RegulatoryRule` needs its `jurisdiction` pinned.** EU restriction is not Indian
  restriction, and the graph holds both.
- **An empty edge is a real answer.** `PERMITS` is empty and coverage says 0% because no
  source can evidence it. Say the data does not support the claim; do not reach for a
  tier-4 edge that looks close enough.

## Where the rules are actually enforced

The catalog documents them. It does not enforce them — these do:

| Rule | Enforced in |
|---|---|
| `roles[]` is additive, never a label | `pipeline/reconcile.py` |
| Every `RegulatoryRule` carries `jurisdiction` | `load_neo4j.py`, golden query |
| Tier 4 never answers compliance | `tier4_never_answers_compliance` golden query |
| `Person` never enters Neo4j | `no_person_nodes` golden query |
| `raw/` is append-only | `lib.land()`, content-addressed |
| `crosswalk` is append-only | `resolve.py` — supersede, never delete |
| GST filing history never displayed | `trust.py` |
| Every property and edge carries provenance | `lib.prov()`, `load_neo4j.pv()` |
| Category spine comes from the regulator | `s05_ninth_schedule.py` |
| `OPERATES` needs ≥ 0.92 or shared GSTIN/pincode | `resolve.py` |

## Adding a source

1. Copy `_schema/source-card.yaml` into `sources/sNN_slug.yaml` and fill the
   hand-written fields. Leave the measured ones at 0.
2. Write the `do_not` block **first**. If you cannot say what the source may not be used
   to claim, you do not understand it well enough to ingest it.
3. Build the adapter, ingest, then `make catalog` to record the measurements and
   `make catalog-check` to reconcile the card.
4. If building it changed your mind about anything, that is a `decisions/` entry. Those
   are the most valuable files here — they are the mistakes that have already been paid
   for once.
