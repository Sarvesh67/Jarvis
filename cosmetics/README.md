# Cosmetics supply-chain graph — India

An ontology-guided knowledge graph of the **Indian B2B cosmetics supply chain**: who can
legally make what, where, from which ingredients, under which standard, and how much of
that we can actually prove.

A sibling subsystem to `msme/` and `marketdata/` — own venv, own store, own lifecycle.
It does **not** touch the textile/Udyam graph or the Cognee semantic layer.

```
cosmetics/
  docker-compose.yml      Neo4j 5.26 (cosmetics-neo4j)  — Postgres is REUSED from jarvis-postgres
  ontology/
    ontology.yaml         single source of truth for the schema
    constraints.cypher    GENERATED from ontology.yaml — never hand-edit
    category_map.yaml     canonical category spine + source-string -> code mappings
    cluster_map.yaml      district / pincode-prefix -> cluster_id (17 clusters, 4 wedge)
  adapters/               Stage 0+1, one file per source
  pipeline/               Stages 2-8
  sql/001_schema.sql      staging + provenance + crosswalk + trust
  validate/               checks.py + golden_queries.yaml
  raw/                    immutable landed payloads (gitignored)
```

## Storage split, and why

| | |
|---|---|
| **Postgres** (`cosmetics` db in `jarvis-postgres`) | every raw row, every competing property value, the append-only crosswalk, the review queue, people, GST records |
| **Neo4j** (`cosmetics-neo4j`, :7474) | the reconciled graph — entities, typed edges, provenance |

Postgres is reused rather than run twice: one less container on the Mac mini, and the
platform's setup scripts already own that lifecycle. Neo4j is its own container because
the spec's Cypher (composite uniqueness constraints, APOC, vector index) targets it, and
its browser at :7474 makes the graph inspectable while it is being built.

**People live in Postgres only.** `person` is a table; there is no `Person` label, and
`validate/checks.py` fails the build if one appears.

## Quick start

```bash
setup/08_cosmetics.sh          # venv, Neo4j, cosmetics db, constraints
cd cosmetics
make ingest-all                # every source that needs no credentials
make pipeline                  # Stages 2-8, ending in validation
make coverage                  # the honest number
make test                      # 25 pytest checks: golden queries + invariants
```

### Review queue

`norm_name + pincode` is the primary resolution path for the MSME long tail, so the
review queue is a staffed workflow, not an exception hopper:

```bash
python -m pipeline.review --list                 # most ambiguous first
python -m pipeline.review --show 42              # full feature vector for one pair
python -m pipeline.review --merge 42             # supersede + relink (append-only)
python -m pipeline.review --distinct 42
python -m pipeline.review --auto-distinct-below 0.80
```

## Reading surfaces

The graph answers questions. `catalog/` answers *"should I believe that answer?"* — it
carries what each source is, what it may be used to claim, and the defects found by
building it. **Read `catalog/README.md` before querying.** `CLAUDE.md` is the same brief
for an agent.

Judgement in the catalog is hand-written; measurement is generated. Nothing rewrites
itself, and `make catalog-check` fails when a card has drifted from the live stores.

| Surface | Where | For |
|---|---|---|
| **Evidence site** | `make site` → `site/build`, or `make site-dev` on :3002 | The narrated view. Source is markdown + SQL in `site/pages/`, so it reads well as prose and as a file. |
| **Metabase** | :3001 (`make viz-up`) | Point-and-click over Postgres. Saved questions, dashboards, per-column descriptions. |
| **Neo4j Browser** | :7474 | Cypher against the graph. Run `make schema` first. |
| **Catalog snapshots** | `catalog/snapshots/` | Dated measurements, committed, so growth is reviewable over time. |
| **Public demo** | `serve/` → https://demo.lattix.co.in | One question box, for someone who has not read any of this. `setup/09_demo.sh`. |

### The agent's skill lives here

`skills/jarvis-cosmetics-search/` is the contract both answering surfaces run under — the
Jarvis console chat and the public demo. `~/.hermes/profiles/cosmetics/skills/` holds a
**symlink** to it, maintained by `setup/09_demo.sh`, so there is one file and one history.

It was not always so, and the failure mode is worth recording because it is silent: the
profile held a real directory containing a v1 skill, with a **newer v2 one level below it**
where nothing loaded it. The console answered under v1 — templates only — for five days
while the repo documented three answer paths. `tests/test_demo.py` now asserts the symlink
resolves to this directory, so the two cannot drift again.

### The public demo

`serve/agent.py` runs the same loop the console's Hermes agent runs, on the model the
profile names, under the skill above: it chooses a path, runs it, reads the honesty
metadata and reasons over the JSON. All three paths are reachable — reviewed templates,
graph context, and Cypher the agent writes — because a demo of a narrower product is a
demo of a different product.

What is NOT carried over is the rest of Hermes: terminal, browser, filesystem and MCP
toolsets. The console has them because someone trusted is driving it. `serve/app.py` also
holds no Postgres credentials at all — the query path never touches it, and people live
there.

Its suggested questions are served from `validate/demo_questions.yaml`, the same file
`tests/test_demo.py` asserts still works, so a question cannot appear on the page unless
every tested phrasing of it still reaches the same template and still returns rows.

```bash
make demo-check      # every demo question and paraphrase, through the live router
make demo            # print the demo script, with the caveat to say for each question
setup/09_demo.sh     # tunnel + launch agent (see the header: two steps need your accounts)
```

Abuse control is a daily question budget and a per-IP burst, not a rate limit — at the
measured $0.0018 a routed question, the failure mode should be "come back tomorrow",
not an invoice. Cloudflare Access sits in front and is what actually decides who gets in.

Metabase's first run asks you to create an admin account and add a database. Connect it to
`jarvis-postgres` / `cosmetics` — host `jarvis-postgres`, port `5432`, user and password
from `platform/.env`. Use the container name, not `127.0.0.1`: Metabase is on the
`jarvis_default` network and localhost inside the container is the container.

Its own app database is a file on the `metabase_data` volume, deliberately not the
`cosmetics` db — otherwise Metabase creates its ~40 internal tables inside the data it is
meant to describe.

Everything binds to `127.0.0.1`. Nothing is exposed off-box.

## Sources

| ID | Source | Tier | What it gives | State |
|---|---|---|---|---|
| S6 | EU CosIng | 1 | 35.9k INCI ingredients, EU Annex II-VI restrictions | **live** |
| S5 | Cosmetics Rules 2020 | 1 | Fourth Schedule (80 categories), Ninth Schedule (37 BIS standards), India ingredient limits | **live** |
| S1 | CDSCO approved manufacturing sites | 1 | 911 facilities + licences | **live** |
| S17 | Udyam MSME registry (cosmetics slice) | 1 | 1,906 firms incl. proprietorships | **live, Gujarat only** |
| S8 | Trade-fair exhibitor lists | 2 | companies actively selling into cosmetics | **live** |
| S7 | Open Beauty Facts | 3 | ingredient alias table | **live** |
| S15 | GeoNames pincodes | 1 | 19,238 pincode -> district -> state | **live** |
| S3 | MCA company master | 1 | the company spine | **blocked — needs a data.gov.in key** |
| S2 | State FDA / ONDLS | 1 | COS-8 / COS-9 licences | **no state publishes a bulk list** |
| S16 | GSTIN verification | 1 | constitution, status, filing history -> trust L3 | **needs a provider key** |
| S9 | CHEMEXCIL member directory | 2 | 4,005 members, 2,379 websites, export activity | **live** |
| S14 | Company websites | 2 | contact, checksum-valid GSTIN, certifications, capabilities | **live** |
| S14b | Ingredient supply claims from those pages | 3 | `Company -[:SUPPLIES]-> Ingredient` | **live** |
| S17b | **Udyam MSME registry, NATIONAL** (data.gov.in) | 1 | the long tail + packaging converters, every cluster | **live** |
| S11 | **NABL accredited testing laboratories** | 1 | who can legally TEST the product | **live** |
| S10/S12/S13 | IndiaMART, retail, trade data | 3-4 | enrichment | not built |
| S19 | SEBI public-issue filings (DRHP/RHP) | 2 | assessed for `THIRD_PARTY_MANUFACTURED_BY` | **not built — brands never name their manufacturers, see decisions/0015** |

### Corrections to the build spec, found by building it

1. **S1 is not the cosmetics facility spine.** Of the 911 CDSCO approved-site rows
   (`num_total_records=911` — the crawl is complete, not truncated) exactly **3** are
   cosmetics-regime after a junk row is dropped; the remaining 907 are drug licences,
   and a fourth cosmetics row was a portal test entry (`Testing123`, Haryana). The
   overlap argument for keeping the drug rows is weaker than it looks: of the 491 firms
   on the register, **7** appear in any cosmetics source. So the cosmetics licence layer
   must come from state FDAs, and the pharma rows are kept in Postgres but held out of
   the answerable graph unless they stand in a loan relationship (`COSMETICS_KEEP_PHARMA=1`
   loads them all).
2. **A loan licence names two firms but not which one owns the plant.** The register has
   `Premise Name` and `Loan Premises Name`, and state authorities fill them in opposite
   conventions: `Pure&Cure → Dr. Reddy's` puts the factory first, `GSK → Encube` puts it
   second, and P&G Health appears on *both* sides in different rows. The pairing is
   therefore tier 1 and the direction is unknown — `LOAN_LICENCE_WITH` is written both
   ways with `direction_known:false`, and anything wanting to say who manufactures for
   whom must use `THIRD_PARTY_MANUFACTURED_BY` instead.
3. **CosIng is fully accessible.** The SPA publishes its own endpoint and API key in
   `assets/env-json-config.json`; the query DSL accepts only `term`/`terms`/`text`, and a
   hard 10,000-result paging window is worked around with recursive prefix buckets on
   `inciName`, deduped on `substanceId`. Coverage: 33,638 / 33,654 (99.95%).
4. **CDSCO's listing is not an opaque form-POST.** `Approved_Manuf_Site.jsp?page_no=N`
   works with a plain GET once a JSESSIONID exists. No DevTools capture needed.
5. **The Fourth Schedule is a better category spine than the Ninth.** It is CDSCO's own
   80-entry category vocabulary; the Ninth Schedule's 37 rows are standards that attach
   to it. Both are loaded, and both PDF OCR defects found in them are documented in
   `adapters/s05_ninth_schedule.py`.
6. **Udyam is the answer to the spec's own stated blind spot.** MCA cannot see
   proprietorships and partnerships; Udyam registers by enterprise, not legal form.
7. **CHEMEXCIL is the website source, not just the export source.** Its directory
   enumerates at `/members-alpha/{A-Z,0-9}` and publishes a website per member — 2,379
   of them — which is what makes S14 possible at all. Member *profiles* need a council
   login, so membership type (manufacturer-exporter vs merchant-exporter) is not
   available and that role split is not made. Do not parse `/members.html` search
   results: the page wraps the matched term in markup and splits names mid-word.

## The hard rules, and where each is enforced

| Rule | Enforced in |
|---|---|
| 1. `roles[]` is additive, never a label | `pipeline/reconcile.py` unions roles across sources |
| 2. Every `RegulatoryRule` carries `jurisdiction` | asserted in `load_neo4j.py`, tested in golden queries |
| 3. Tier 4 never answers a licence/compliance question | `tier4_never_answers_compliance` golden query |
| 4. `Person` never enters Neo4j | `no_person_nodes` golden query |
| 5. `raw/` is append-only | `lib.land()` is content-addressed; identical bytes never re-land |
| 6. `crosswalk` is append-only | `resolve.py` only INSERTs; supersede, never delete |
| 7. GST filing history never displayed | `trust.py` records the fact, never the period, in public reasons |
| 8. Every property and edge carries provenance | `lib.prov()` / `load_neo4j.pv()`; `every_edge_has_provenance` query |
| 9. Category spine comes from the regulator | `s05_ninth_schedule.py`; retail strings are tags in `category_map.yaml` |
| 10. `OPERATES` needs score ≥ 0.92 or shared GSTIN/pincode | `resolve.py`; everything else → `review_queue` |

## Notes on judgement calls

- **Pair scoring renormalises over observable features.** The spec's weights (0.45 name,
  0.25 address, 0.20 contact, 0.10 category) assume all four are present. A licence
  listing has no email and no self-declared role, so charging a pair for a feature
  neither side could supply is evidence of a thin source, not of difference. Without
  this the OPERATES join tops out at 0.70 and silently never fires — 97 edges before the
  fix, 822 after.
- **`IN_CLUSTER_WITH` is deliberately not materialised.** It is a clique; the Cluster
  node already carries the information and a two-hop traversal answers it. (`msme/`
  learned this on one dense Surat cluster.)
- **`SUBSTITUTE_FOR` is emitted only from restricted ingredients**, top-10 by shared
  function. The useful question is "this is restricted, what else does the job?"; the
  full cross-product would be a function-sized clique.
- **`PERMITS` is legitimately empty.** CDSCO's listing has no category field, so no
  licence→category edge can be asserted at tier 1. The coverage table says 0% rather
  than the graph implying permission. `SERVES_CATEGORY` (tier 4, from NIC activity)
  provides the ranking path in the meantime.
- **Embeddings will go through the platform's Ollama/LiteLLM gateway** rather than
  `sentence-transformers`, avoiding a ~2 GB torch install and matching the rest of Jarvis.
- **Name similarity is computed on distinctive tokens.** "SUDAMA ENTERPRISE" and "RAMU
  ENTERPRISE" share a pincode and score 0.81 on `token_set_ratio` because of one generic
  word. Generic business/sector vocabulary is stripped before scoring, and a pair whose
  only common ground is generic can only auto-merge when something else corroborates at
  ≥ 0.9 — which is what still lets "S.P. Pharmaceuticals" and "S P Pharmaceuticals" at
  one address merge correctly.
- **`roles_claimed` is never scored as a discriminator.** Every source stamps its own
  role vocabulary, so two rows for the same company from two sources have disjoint role
  sets by construction. Overlap counts as positive evidence; its absence counts as
  nothing. Before this fix, Galaxy Surfactants existed twice — once as an exhibitor and
  once as a CHEMEXCIL member — because the disjoint role sets pushed the score below the
  merge threshold.
- **A fuzzy match on the name alone cannot auto-merge** unless the normalised name is an
  exact match, has ≥ 2 tokens, and is not a name already seen at two different pincodes.
  CHEMEXCIL publishes a name and a website and nothing else; without this guard every
  generic name in a 4,000-row directory collapses into one entity.
- **Identifiers scraped from a website are promoted to spine fields only when the
  company's own name appears on that site.** Directories legitimately list a parent or
  group site against a subsidiary (CHEMEXCIL lists Bombay Perfumery against aacipl.com,
  verified against the raw HTML as their data, not a parse error). A GSTIN lifted from
  the wrong company's site would merge two unrelated entities, so unconfirmed ones stay
  in the payload. Every candidate is checksum-validated first.
- **Udyam is partitioned by PINCODE, not district.** The data.gov.in API caps every
  distinct query at 10,000 rows and the registry holds 43.9M records; Surat district alone
  is 560,180 and Thane 684,886, so district buckets can never be read completely. Pincode
  is the only field that splits it into buckets that mostly fit (Baddi is 6,280), and
  pincodes are globally unique so no district filter is needed alongside. There is no
  server-side activity filter — `filters[Activities]` matches the whole JSON blob and
  `q=` is ignored — so the NIC slice is cut locally and the ~5% yield is the price of the
  registry having no activity index.
- **A district name match must agree on state.** Raigarh (Chhattisgarh) is a different
  district from Raigad (Maharashtra), and listing the former as a spelling variant of the
  latter put nine Chhattisgarh firms inside the Palghar-Tarapur wedge cluster. Aurangabad
  (Maharashtra/Bihar), Bilaspur (Himachal/Chhattisgarh) and Hamirpur (Himachal/Uttar
  Pradesh) are the same trap. An unknown state still matches, because a missing state is
  not evidence of a mismatch.
- **NABL accreditation scope comes from the register, not from keywords.** "Accredited for
  cosmetics" means the Cosmetics & Essential Oils / AYUSH / Soap-Detergent-Toiletries
  scope specifically. An earlier keyword list containing the bare word CHEMICAL flagged a
  CENTRAL FORENSIC SCIENCE LABORATORY as a cosmetics testing lab and inflated the count
  from 184 to 442. Pharma accreditation is recorded as adjacent capability, never as
  cosmetics.
- **Ingredient mentions are matched only on unambiguous INCI names.** "MILK", "SAND",
  "HONEY" and "TALC" are real INCI names and also ordinary English; they are excluded
  rather than guessed at, and a page must yield ≥ 3 distinct ingredients before any of
  them is believed.

## Two constraints that are not negotiable

- **DPDP.** A proprietorship GSTIN derives from an individual's PAN and is arguably
  personal data. Where `is_proprietorship`, it gets `person`-table handling — consent
  basis recorded, deletable — not industrial-spine handling.
- **Never publish filing history.** Verify against it, rank with it, warn a buyer
  privately. "Hasn't filed in 8 months" on a public profile is a defamation problem.

---

## Three answer paths (and which one may make a regulatory claim)

The router no longer only dispatches templates. Which path runs depends on **what kind of
claim the answer makes**, not on which mechanism is technically available:

| Question | Path | May assert regulatory fact? |
|---|---|---|
| licence, restriction, standard, "is X allowed" | **template** — 13 reviewed queries | **Yes — only this one** |
| open, multi-entity, "tell me about" | **context** — graph neighbourhood + cited rule text | No, exploratory |
| counting, ranking, arbitrary joins | **cypher** — the agent writes it, we validate it | No, exploratory |

```bash
make ask Q="cosmetics manufacturers in Baddi"          # auto: template, else context
$PY -m pipeline.router --mode context "tell me about Baddi"
$PY -m pipeline.router --expand Company:c_ab12 --edge-types OPERATES
$PY -m pipeline.router --schema                        # read before writing Cypher
$PY -m pipeline.router --cypher "MATCH ... RETURN ..." # validated, read-only
```

**Why the guarantee changed shape.** Templates alone capped the product: a question no
template anticipated could not be answered at all, and the one fallback was dead code (it
queried `chunk_embedding`; the index is `docchunk_embedding`). The guarantee is now
conditional on the claim rather than structural — regulatory assertions still come only
from reviewed queries, while exploration gets real reach.

**What keeps generated Cypher safe:** read-only twice over (a READ-access session *and* a
textual guard), always bounded, rejected if it touches `RegulatoryRule` without pinning
`jurisdiction` (hard rule 2) or leans on tier-4 derived edges for a compliance question
(hard rule 3). Every rejection names the offending fragment so the agent repairs rather
than dead-ends, and the executed query is always returned for audit.

**What keeps graph context honest:** it is a *neighbourhood, not a census*. It never
expands out of a hub node beyond the first hop (`Cluster(Ahmedabad-Vapi)` has ~17k
neighbours), excludes tier-4 edges by default, is budgeted to ~60 nodes / ~120 edges, and
says in its own `trust` field that you must not count or rank from it.

The agent reads `--schema` — generated from the live graph, never hand-written — before
writing any query, so it cannot guess at labels. The schema card also carries the mistakes
that have already produced wrong answers here (`WHERE` after `OPTIONAL MATCH` filters
nothing; `trust_level` is a maximum, filter `2 IN trust_rungs`).
