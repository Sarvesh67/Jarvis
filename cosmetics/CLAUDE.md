# cosmetics/ — read this before querying or changing anything

An ontology-guided knowledge graph of the Indian B2B cosmetics supply chain. `README.md`
is the build narrative. This file is the operating brief.

## Read first, in this order

1. **`catalog/glossary.yaml`** — domain and project vocabulary. Skipping it is how you
   misread `trust_grade` and `tier`, which are the two fields that decide whether an
   answer is allowed to be given at all.
2. **`catalog/sources/*.yaml`** — one card per source. The `do_not:` block of any source
   you are about to touch is mandatory reading; several entries are enforced
   mechanically and will reject your query rather than answer it.
3. **`make schema`** (`pipeline/schema_card.py`) — the live labels and properties,
   generated from the graph. Read it before writing any Cypher. Do not guess at labels.
4. **`catalog/decisions/`** — why the build works the way it does. Read the relevant one
   before "fixing" something that looks wrong; several of these document things that look
   like bugs and are not.
5. **`DATA.md`** — where every byte the graph is built from lives, what exists only in
   Postgres, how it is backed up, and how to fetch each source again.

## The four mistakes that produce confidently wrong answers here

- **Tier decides what a fact may claim.** Tier 1 is the regulator and is the only tier
  that may answer a licence or compliance question. Tier 2 (company-declared), tier 3
  (scraped) and tier 4 (inferred) rank and suggest; they never permit.
- **The evidence grade describes our evidence, never the firm, and never ranks.**
  `trust_grade` is flagged | G0 listed | G1 registered | G2/G3 corroborated by 2 / 3+
  independent publishers | G4 GST verified (TRUST_PLAN §6). Licence, activity and track
  record are separate facets (`trust_licence` …). Count publishers (`publisher.family` on
  each card), never source ids. `trust_level` / `trust_rungs` are deprecated — kept one
  release; never filter `trust_level >= 2`.
- **Pin `jurisdiction` on every `RegulatoryRule`.** The graph holds IN and EU. An EU
  restriction is not an Indian restriction.
- **An empty edge is a real answer.** `PERMITS` is empty and coverage reports 0% because
  no held source can evidence it — see `catalog/decisions/0012`. Say the data does not
  support the claim. Do not substitute a tier-4 edge that looks close enough.

Also: `WHERE` after `OPTIONAL MATCH` filters nothing. This has produced wrong answers here.

## Hard rules you can break by accident

| Rule | Enforced in |
|---|---|
| `Person` never enters Neo4j — people live in Postgres `person` only | `no_person_nodes` golden query |
| Tier 4 never answers a compliance question | `tier4_never_answers_compliance` golden query |
| Every `RegulatoryRule` carries `jurisdiction` | `load_neo4j.py` + golden query |
| Every property and edge carries provenance | `lib.prov()` / `load_neo4j.pv()` |
| `raw/` is append-only | `lib.land()`, content-addressed |
| `crosswalk` is append-only — supersede, never delete | `pipeline/resolve.py` |
| GST filing history is never displayed | `pipeline/trust.py` |
| A person's PAN never reaches the graph — personal-PAN GSTINs (holder type P/H) are masked in `gstins` and `trust_reasons`, `pan` unset | `no_personal_pan_in_graph` golden query; `pipeline/gstin.py` |

Two that no test can catch, so they are on you:

- **DPDP.** A proprietorship GSTIN derives from an individual's PAN. The graph side is
  now tested (above). The Postgres side is not: where `is_proprietorship`, the full value
  needs `person`-table handling — consent basis recorded, deletable — not
  industrial-spine handling. Use `pipeline.gstin.public_gstin()` for anything shown.
- **Never publish filing history.** Verify against it, rank with it, warn privately.
  "Hasn't filed in 8 months" on a public profile is a defamation problem.

## Layout

```
ontology/     ontology.yaml is the single source of truth; constraints.cypher is GENERATED
adapters/     Stage 0+1, one file per source
pipeline/     Stages 2-8, plus router.py (answers) and snapshot.py (catalog measurement)
catalog/      what the data is and what it may be used to claim  ← start here
raw/          immutable landed payloads, gitignored, 841 MB
validate/     checks.py + golden_queries.yaml
out/          built artifacts, gitignored
```

## Commands

```bash
make ingest-all        # every source needing no credentials
make pipeline          # Stages 2-8, ending in validation
make test              # 284 checks: golden queries + hard-rule invariants + trust + GST + backup
make schema            # live schema — read before writing Cypher
make coverage          # the honest number
make catalog           # write today's snapshot of the live stores
make catalog-check     # fail if a source card disagrees with the stores
make backup            # snapshot Postgres, mirror raw/ + msme inputs, encrypt to iCloud (DATA.md)
make backup-verify     # restore the newest snapshot into a scratch db and check it
make backup-verify-offsite  # the same check on the encrypted iCloud copy alone
make gst-stub          # GST verification end to end on the STUB provider (verifies nothing); gst-stub-purge after
make ask Q="..."       # natural language -> whitelisted template
make site              # rebuild the Evidence reading site
make viz-up            # Metabase :3001, NeoDash :5005, Neo4j Browser :7474
make bi-views          # rebuild the Postgres `bi` schema Metabase reads
```

Metabase reads the `bi` schema in Postgres, never `public`, and connects as a role that
cannot see `person`. Metabase has no Neo4j driver and never will — the graph is read
through NeoDash and Neo4j Browser instead. See `VIZ.md`.

`make catalog` also feeds `site/sources/catalog/*.csv`. Neo4j is not an Evidence connector
and the cards are YAML, so both are flattened to CSV there rather than taught to the site.
Those CSVs are generated — edit the cards or the graph, never the CSVs.

## Conventions

- `ontology/constraints.cypher` is generated. Never hand-edit it; edit `ontology.yaml`
  and run `make constraints`.
- Catalog cards are **hand-written for judgement, generated for measurement**, and
  nothing rewrites itself. When `catalog-check` fails, edit the card — not the data.
- Source defects are corrected in the adapter, never in `raw/`.
- Use the project venv: `.venv/bin/python`, not system Python.
