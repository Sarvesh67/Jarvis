# Cosmetics supply-chain graph.
# Postgres is the shared jarvis-postgres container (database `cosmetics`);
# Neo4j is this stack's own container. See setup/08_cosmetics.sh.

PY := .venv/bin/python
PG := docker exec -i jarvis-postgres psql -U $(shell grep -m1 '^POSTGRES_USER=' ../platform/.env | cut -d= -f2-) -d cosmetics

.PHONY: bootstrap ingest normalize resolve reconcile constraints load derive trust \
        validate coverage rebuild pipeline clean-graph help

help:
	@grep -E '^[a-z-]+:.*?##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/' | column -t -s "$$(printf '\t')"

bootstrap:              ## docker up, create db, apply SQL + Neo4j constraints
	docker compose up -d
	$(PG) -v ON_ERROR_STOP=1 < sql/001_schema.sql
	$(PY) pipeline/gen_constraints.py --apply

ingest:                 ## Stage 0+1 for one source: make ingest SOURCE=s06
	$(PY) adapters/$(shell ls adapters | grep '^$(SOURCE)').py $(ARGS)

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

clean-graph:
	$(PY) -c "import lib; d=lib.neo4j_driver(); s=d.session(); s.run('MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS'); d.close(); print('wiped')"

test:                   ## pytest: golden queries + hard-rule invariants
	$(PY) -m pytest tests/ -q

review:                 ## list the pending resolution decisions
	$(PY) -m pipeline.review --list

index:                  ## Stage 7: chunk, embed (local, free) and link the doc corpus
	$(PY) -m pipeline.index_docs --sites 250 --wipe

ask:                    ## natural-language question -> whitelisted template. make ask Q="..."
	$(PY) -m pipeline.router "$(Q)"

templates:              ## list the whitelisted query templates
	$(PY) -m pipeline.router --list
