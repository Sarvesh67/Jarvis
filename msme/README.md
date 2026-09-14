# MSME Second Brain — Udyam preprocessing

Turns the raw Udyam registry (~500k MSMEs) into an enriched, queryable supply-chain knowledge
base for the `msme` project. A self-contained subsystem (sibling to `marketdata/`) that produces
Cognee-ready documents + deterministic graph edges, and lands the bulk in a columnar store.

```
msme/
  seeds/materials.json   controlled material vocabulary (the JOIN KEY) — raw→intermediate→finished
  seeds/nic_map.json     curated NIC-5 -> {outputs, inputs} in material slugs (the supply brain)
  nic.py                 NIC helpers + per-code material enrichment (curated → cache → LLM) + enrich_all
  nic_master.py          parse the official NIC-2008 master (xlsx/csv/pdf) → data/nic2008.csv, merge labels
  gst.py                 cached, swappable GST verification (joined at preprocess; raw CSV immutable)
  preprocess.py          CSV → firm records → material-join inference → docs + edges + parquet
  graph.py               write the deterministic backbone into FalkorDB msme_graph (no cognify, $0 LLM)
  search.py              filtered top-K search — DuckDB pre-filter + FalkorDB graph traversals
  promote.py             cognify a SHORTLIST of firm profiles (semantic layer on top of the backbone)
  out/                   firms.parquet, documents.jsonl, edges.jsonl   (gitignored)
  cache/                 nic_enrich.json, gst.json, materials_pending.json   (gitignored)
  data/                  nic2008.csv (parsed master)                  (gitignored)
```

## Two graphs: `msme_graph` (backbone) + `msme_semantic_graph` (Cognee)

Cognee's semantic layer lives in its **own** FalkorDB graph (`jarvis_cognee.graph_name()` /
`SEMANTIC_GRAPH_SUFFIX`), separate from the deterministic backbone.

**Why:** cognify does graph-wide entity matching. Sharing a graph with the 326k-node `:Enterprise`
backbone made every extraction match against all of it — measured: the same 3 documents finish in
**40s in a 52-node graph** but spin at **99.5% CPU indefinitely** in the 326k-node one (both a
491-doc and a 239-doc batch stalled at ~20 documents). Splitting them makes the semantic layer
usable; the two node sets were never linked anyway (see "semantic slice only" below).

Consequence: the dashboard Knowledge tab shows the **backbone** (the 326k supply graph); Cognee
retrieval (`query.py`, the `jarvis-knowledge` skill) reads the semantic graph. Both are correct.

## GOTCHA — Cognee co-residency requires `id` properties on backbone nodes

Cognee's FalkorDB adapter **assumes it owns the whole graph**: `get_graph_data()` runs a bare
`MATCH (n)` across every node and reads `record["id"]`, and edges need
`source_node_id`/`target_node_id`. Our deterministic backbone shares `msme_graph` with Cognee's
semantic layer, so backbone nodes without those properties made that call raise `KeyError: 'id'`.
That silently broke `index_graph_edges` → the `EdgeType` vector index stayed empty → **every
graphRAG query returned "No relevant knowledge"**, even though entities, chunks and embeddings were
all fine.

`graph.py` therefore stamps `id` on every node (`ent:`/`nic:`/`mat:`/`dist:` prefixes) and
`source_node_id`/`target_node_id`/`relationship_name` on every edge. Don't remove them — retrieval
breaks in a way that looks like "the graph is empty" rather than a crash.

(Known remaining Cognee-side quirk: `index_data_points` reports success but writes 0 `:EdgeType`
nodes on this adapter version, so the edge-type vector collection stays empty. Retrieval still
works via the Entity/Chunk/Summary collections — only edge-name-driven ranking is degraded.)

## Pipeline (run order)

```bash
setup/07_msme.sh                                             # build msme/.venv
python -m msme.nic_master --file nic2008.xlsx               # 1. parse master → labels+hierarchy (no LLM)
python -m msme.preprocess --csv udyam.csv --enrich-only    # 2. per-code material inference (LLM, cached)
python -m msme.preprocess --csv udyam.csv --connected-only --emit-docs  # 3. firms → parquet + docs + edges
python -m msme.graph --wipe                                # 4. deterministic backbone → FalkorDB
python -m msme.search --graph --need "cotton yarn" --role supplier --district surat --gst-verified
python -m msme.promote --from-search "embroidered dress material" --role buyer --top 10  # optional semantic layer
```

## Graph model (what is stored vs derived)

**Stored (deterministic, compact — `graph.py`):**
`(:Enterprise)-[:CLASSIFIED_AS]->(:NICClass)-[:PRODUCES|CONSUMES]->(:Material)`,
`(:Enterprise)-[:LOCATED_IN]->(:District)`. Isolated labels in the same `msme_graph` Cognee uses —
they never contaminate doc retrieval (the `:Signal` pattern).

**Derived at query time — NOT stored** (`search.py`): `SELLS_TO`/`BUYS_FROM`/`COMPETES_WITH` are
traversals over `PRODUCES/CONSUMES` (`A produces m ← B consumes m`), because in one dense cluster
materialised supply edges explode into cliques. Finished products are first-class `:Material` nodes,
so the web is navigable *through* products.

---

## 1. The core idea — infer trade relationships through **materials**, not NIC pairs

A firm's Udyam record tells you *what it does* (an NIC-2008 activity). The insight is that every
activity **produces** some goods and **consumes** others. One firm's output is another firm's raw
material. So instead of hand-curating N² NIC→NIC edges, we map each NIC code to canonical
**material slugs** and let relationships fall out of a join:

```
Firm A SELLS_TO   Firm B   ⟺   A.outputs ∩ B.inputs  ≠ ∅      (B consumes what A makes)
Firm A BUYS_FROM  Firm B   ⟺   A.inputs  ∩ B.outputs ≠ ∅      (A consumes what B makes)
Firm A COMPETES   Firm B   ⟺   A.outputs ∩ B.outputs ≠ ∅  AND  same NIC class  AND  overlapping geo
```

Why materials are the right join key:
- **Compositional.** An N×(materials) mapping yields the full N:M supply web for free.
- **Explainable.** Every edge carries the shared material as evidence: *"A → B via `lace`"* — a human (or the agent) can audit it.
- **Matches reality.** Supply chains *are* material flows. The demo chain proves it:
  `raw_cotton → spinner(13111) → cotton_yarn → weaver(13121) → greige_fabric → dyer(13131) →
  dyed_fabric → embroiderer(13991) → embroidered_fabric/lace → garment maker(14101) → garment →
  trader(46411)`. Every arrow was reconstructed from material joins, nothing else.

The **material vocabulary is the join key** and must be controlled: `"cotton thread"` and
`"cotton sewing thread"` must resolve to the *same* slug or the join silently misses. `nic.Vocabulary`
snaps free text to slugs via `synonyms`; anything unrecognised is logged to
`cache/materials_pending.json` for review — **never silently dropped** (matches the platform's
"no silent caps" ethos).

## 2. Enrichment is done per **NIC code**, not per **firm**

There are ~1,300 distinct NIC-5 codes across all 500k firms. So enrichment (`nic.enrich_nic`) is
**curated-first, cache-second, LLM-last**:

1. **Curated** — `seeds/nic_map.json` is the authoritative, human-verified core (mirrors
   `cognee/linker.py`'s never-guess `symbols.json`). Start here for your Surat textile cluster.
2. **Cache** — a prior LLM enrichment for that code is reused (`cache/nic_enrich.json`).
3. **LLM** — one gateway call (cheap `deepseek-v4-flash`, the role the `msme` project already maps)
   fills an unseen code, then caches it. `reasoning:{enabled:false}` per the DeepSeek gotcha in
   `cognee/preprocess.py`.

So the whole 500k corpus costs **≤ ~1,300 LLM calls, once**, and every firm inherits its NIC
profile(s). GST verification, which you already collect, rides through as a `gst_status` trust flag.

## 3. Scale discipline — bulk stays OUT of the graph

This follows `marketdata/`'s founding principle (*"numbers stay out of the graph; the graph holds
entities + pointers, the store holds the mass"*). **We do NOT cognify 500k firms.**

- The full structured registry → **`out/firms.parquet`** (DuckDB reads it). This is where all
  filtering happens — cheap, no LLM, no graph.
- The **material supply web** is small (materials × NIC), held in two inverted indexes.
- A firm is **promoted into the Cognee graph only when it becomes of interest** — a search hit or a
  shortlist. Then its `documents.jsonl` entry feeds `cognee/ingest.py` and its `edges.jsonl` rows are
  written directly to FalkorDB (isolated, no cognify — the `:Signal` node pattern), because we don't
  want the cheap extractor to rediscover cross-document supply joins we already computed exactly.

## 4. The final document structure

Per firm, `preprocess.firm_document()` emits one object with three layers:

```jsonc
{
  "udyam": "395010-vaghasiya_embroidery",
  "doc_type": "msme_profile",

  // (a) FILTER / INDEX layer — namespaced node_set tags, the Cognee-native mechanism the
  //     dashboard + linker.py already use. Every dimension a search constrains on is a tag.
  "tags": [
    "state:gujarat", "district:surat", "pincode:395010", "gst:verified",
    "section:C", "nic:13991", "nicclass:1399",
    "makes:embroidered_fabric", "makes:lace", "makes:fringe",       // producer index
    "needs:cotton_yarn", "needs:dyed_fabric", "needs:greige_fabric" // consumer index
  ],

  // (b) SEMANTIC layer — prose Cognee's extractor reads to build the graph. Material evidence is
  //     spelled out so SELLS_TO / BUYS_FROM / COMPETES_WITH edges are extractable.
  "text": "Vaghasiya Embroidery is a micro/small enterprise in SURAT, GUJARAT (PIN 395010).\n
           Registered activities: Embroidery work and making of laces and fringes (NIC 13991).\n
           It produces: Embroidered fabric, Lace, Fringe. It consumes: Cotton yarn, Dyed fabric...\n
           Potential SUPPLIERS (it buys from): firms producing Cotton yarn, Dyed fabric...\n
           Potential BUYERS (it sells to): firms that consume Embroidered fabric, Lace...",

  "meta": { "name": "...", "state": "...", "nic_codes": ["13991"], "outputs": [...], "inputs": [...] },

  // (c) DETERMINISTIC EDGES — written straight to FalkorDB, no cognify. Each carries its evidence.
  "edges": [
    {"src": "...vaghasiya...", "rel": "SELLS_TO",      "dst": "...maa_garments...",  "via": ["embroidered_fabric","lace"]},
    {"src": "...vaghasiya...", "rel": "BUYS_FROM",     "dst": "...radhe_spinning...","via": ["cotton_yarn"]},
    {"src": "...vaghasiya...", "rel": "COMPETES_WITH", "dst": "...laxmi_embroidery...","via": ["embroidered_fabric","lace","fringe"]}
  ]
}
```

**Suggested ontology additions** for `cognee/data/msme/ontology.json` (currently Client/Invoice/Product):
`Enterprise`, `NICClass`, `Material`, `District` entity types; `PRODUCES`, `CONSUMES`, `SELLS_TO`,
`BUYS_FROM`, `COMPETES_WITH`, `LOCATED_IN`, `CLASSIFIED_AS` relations — so the fuzzy resolver biases
the extractor toward this schema.

## 5. Bounding the search to a reasonable number of vendors

`search.py` guarantees a small, ranked shortlist via three levers, all in DuckDB (so it can run on
every query, before the graph is ever touched):

1. **Hard filters** remove the vast majority up front — material **role** (a supplier PRODUCES the
   material; a buyer CONSUMES it), **geography**, and **trust** (`--gst-verified`).
2. **Tiered geo fallback** widens `pincode → district → state → national` **only until `top` is
   met**, so a dense cluster never floods and a sparse need still returns something.
3. **Rank + top-K** caps the output (default 20) and prints *matched vs returned vs dropped* — no
   silent truncation. Ranking prefers GST-verified, then registration recency; per-firm edge lists
   are also capped (`MAX_COUNTERPARTIES`) so a mega-material like `cotton_yarn` can't explode.

```bash
msme/.venv/bin/python -m msme.search --need "cotton yarn" --role supplier \
    --state gujarat --district surat --gst-verified --top 20
```

Only these ~20 survivors are promoted to Cognee for the agent (Hermes/Opus) to reason over — the
retrieval-is-cheap / reason-once split the platform already commits to.

---

## Run it

```bash
# build (curated-only, no gateway):
msme/.venv/bin/python -m msme.preprocess --csv udyam_500k.csv --no-llm
# full enrichment (fills unseen NIC codes via the gateway, cached):
msme/.venv/bin/python -m msme.preprocess --csv udyam_500k.csv --emit-docs
# search:
msme/.venv/bin/python -m msme.search --need "dyed fabric" --role supplier --district surat --top 20
```

`out/documents.jsonl` → `cognee/ingest.py` (as `node_set=tags`); `out/edges.jsonl` → a small direct
FalkorDB writer (to build). Needs a venv with `pandas`, `duckdb`, `pyarrow`, `httpx` (the
`marketdata/.venv` already has the first three).

> **Note:** the NIC numbers in `seeds/nic_map.json` are illustrative textile-cluster seeds for the
> Surat demo. Replace/extend with the official NIC-2008 master list; unseen codes are LLM-filled.
