# Cosmetics supply-chain graph.
# Postgres is the shared jarvis-postgres container (database `cosmetics`);
# Neo4j is this stack's own container. See setup/08_cosmetics.sh.

PY := .venv/bin/python
PG := docker exec -i jarvis-postgres psql -U $(shell grep -m1 '^POSTGRES_USER=' ../platform/.env | cut -d= -f2-) -d cosmetics

.PHONY: gst-stub gst-stub-purge bootstrap ingest normalize resolve reconcile constraints load derive trust \
        validate coverage rebuild pipeline clean-graph help demo demo-check \
        catalog catalog-check site site-dev viz-up viz-down \
        backup backup-verify backup-verify-offsite backup-passphrase

help:
	@grep -E '^[a-z-]+:.*?##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/' | column -t -s "$$(printf '\t')"

bootstrap:              ## docker up, create db, apply SQL + Neo4j constraints
	docker compose up -d
	$(PG) -v ON_ERROR_STOP=1 < sql/001_schema.sql
	$(PG) -v ON_ERROR_STOP=1 < sql/003_trust_evidence.sql
	$(PG) -v ON_ERROR_STOP=1 < sql/004_gst_verification.sql
	$(PY) pipeline/gen_constraints.py --apply

ingest:                 ## Stage 0+1 for one source: make ingest SOURCE=s06 (s17 and s17b differ)
	$(PY) adapters/$(shell ls adapters | grep '^$(SOURCE)_.*\.py$$') $(ARGS)

ingest-all:             ## every source that needs no credentials
	$(PY) adapters/s15_reference.py
	$(PY) adapters/s06_cosing.py --item-type both
	$(PY) adapters/s05_ninth_schedule.py
	$(PY) adapters/s01_cdsco_sites.py
	$(PY) adapters/s08_exhibitors.py
	$(PY) adapters/s17_udyam.py
	$(PY) adapters/s07_obf.py

normalize:              ## Stage 2
	$(PY) pipeline/normalize.py

resolve:                ## Stage 3 — entity resolution + OPERATES inference
	$(PY) pipeline/resolve.py

reconcile:              ## Stage 4 — property precedence, losers retained
	$(PY) pipeline/reconcile.py

constraints:            ## regenerate + apply ontology constraints
	$(PY) pipeline/gen_constraints.py --apply

load:                   ## Stage 5 — load Neo4j (idempotency asserted)
	$(PY) -m pipeline.load_neo4j --assert-idempotent

derive:                 ## Stage 6 — derived edges (all tier 4 unless stated)
	$(PY) -m pipeline.derive

trust:                  ## Stage 8 — recompute trust levels
	$(PY) -m pipeline.trust

validate:               ## referential + false-merge + jurisdiction + golden queries
	$(PY) validate/checks.py

coverage:               ## just the honest coverage table
	$(PY) validate/checks.py --coverage-only

pipeline: normalize resolve reconcile load derive trust validate  ## Stages 2-8

rebuild:                ## drop the graph and replay Stages 2-8 from Postgres
	$(PY) -m pipeline.load_neo4j --wipe
	$(MAKE) derive trust validate

backup:                 ## snapshot Postgres + mirror raw/ + msme inputs to ~/Backups/lattix-cosmetics, then an encrypted copy to iCloud Drive (DATA.md)
	$(PY) -m pipeline.backup

backup-verify:          ## restore the newest snapshot into a scratch db and check it against its manifest
	$(PY) -m pipeline.backup verify

backup-verify-offsite:  ## decrypt the iCloud copy into a scratch root and run the same check on it
	$(PY) -m pipeline.backup verify-offsite
	$(PY) -m pipeline.backup offsite-status

backup-passphrase:      ## once per Mac: store the backup passphrase in the login keychain (prompts twice)
	security add-generic-password -s lattix-cosmetics-backup -a "$$USER" -l "Lattix cosmetics backup" -w

clean-graph:
	$(PY) -c "import lib; d=lib.neo4j_driver(); s=d.session(); s.run('MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS'); d.close(); print('wiped')"

test:                   ## pytest: golden queries + hard-rule invariants
	$(PY) -m pytest tests/ -q

demo-check:             ## every demo question and paraphrase, through the live router
	$(PY) -m pytest tests/test_demo.py -m demo -q

demo:                   ## print the demo script: questions, in order, with the caveats
	$(PY) -m pipeline.demo

mca-bridge:             ## T3: ask MCA about every company/LLP name from Udyam, CHEMEXCIL, fairs and websites (~13.9k, hours, resumable), then rebuild
	$(PY) adapters/s03_mca_master.py --mode names --sources s17b_udyam_api s17_udyam s09_chemexcil s08_exhibitors s14_sites --limit 20000
	$(MAKE) pipeline

gst-stub:               ## T2: run GST verification + name search end to end on the STUB provider (verifies nothing)
	COSMETICS_GST_PROVIDERS=stub $(PY) -m trust.gst run-held

gst-stub-purge:         ## delete every stub GST row (they were never facts)
	$(PY) -m trust.gst purge-stub

review:                 ## list the pending resolution decisions
	$(PY) -m pipeline.review --list

index:                  ## Stage 7: chunk, embed (local, free) and link the doc corpus
	$(PY) -m pipeline.index_docs --sites 250 --wipe

ask:                    ## natural-language question -> whitelisted template. make ask Q="..."
	$(PY) -m pipeline.router "$(Q)"

templates:              ## list the whitelisted query templates
	$(PY) -m pipeline.router --list

schema:                 ## the live schema the agent reads before writing Cypher
	$(PY) -m pipeline.router --schema

context:                ## graph-context retrieval. make context Q="..."
	$(PY) -m pipeline.router --mode context "$(Q)"

catalog:                ## write today's catalog snapshot of the live stores
	$(PY) -m pipeline.snapshot

catalog-check:          ## fail if a source card disagrees with the live stores
	$(PY) -m pipeline.snapshot --drift

site-dev:               ## Evidence site with live reload on :3002
	cd site && npx evidence dev --port 3002

site:                   ## refresh catalog data, then build the static site to site/build
	$(MAKE) catalog
	cd site && npx evidence sources && npx evidence build

bi-views:               ## (re)build the Postgres serving layer Metabase reads
	docker exec -i jarvis-postgres psql -U jarvis -d cosmetics -v ON_ERROR_STOP=1 < sql/002_bi_views.sql

viz-up:                 ## start Metabase (:3001), NeoDash (:5005), Neo4j Browser (:7474)
	docker compose up -d
	$(MAKE) bi-views

viz-down:               ## stop them
	docker compose stop metabase neodash neo4j
