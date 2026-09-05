#!/usr/bin/env bash
# Cosmetics supply-chain graph: uv venv (py3.12) + pinned deps, Neo4j container,
# a `cosmetics` database inside the shared jarvis-postgres, and the Neo4j constraints.
# Idempotent — safe to re-run.
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
ensure_path
have uv || fail "uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
have docker || fail "docker not found (Colima)."
require_platform_env

COS_DIR="$JARVIS_ROOT/cosmetics"
cd "$COS_DIR"

[ -d .venv ] || { log "Creating cosmetics venv (py3.12)..."; uv venv --python 3.12; }
if [ -f requirements.lock ]; then
  log "Installing cosmetics deps from requirements.lock..."
  uv pip install --python .venv -r requirements.lock
else
  uv pip install --python .venv -r requirements.txt
  uv pip freeze --python .venv > requirements.lock
fi

log "Starting Neo4j (cosmetics-neo4j)..."
docker compose up -d
for i in $(seq 1 30); do
  docker inspect -f '{{.State.Health.Status}}' cosmetics-neo4j 2>/dev/null | grep -q healthy && break
  sleep 2
done
docker inspect -f '{{.State.Health.Status}}' cosmetics-neo4j | grep -q healthy \
  || fail "cosmetics-neo4j did not become healthy; check: docker logs cosmetics-neo4j"

PGUSER_VAL="$(env_get POSTGRES_USER)"
log "Ensuring the cosmetics database exists in jarvis-postgres..."
docker exec jarvis-postgres psql -U "$PGUSER_VAL" -d postgres -tAc \
  "SELECT 1 FROM pg_database WHERE datname='cosmetics'" | grep -q 1 \
  || docker exec jarvis-postgres psql -U "$PGUSER_VAL" -d postgres -c "CREATE DATABASE cosmetics;"

log "Applying SQL schema..."
docker exec -i jarvis-postgres psql -U "$PGUSER_VAL" -d cosmetics -v ON_ERROR_STOP=1 \
  < sql/001_schema.sql >/dev/null

log "Generating + applying Neo4j constraints from ontology.yaml..."
./.venv/bin/python pipeline/gen_constraints.py --apply

ok "Cosmetics graph env ready."
note "Neo4j browser : http://127.0.0.1:7474  (neo4j / \${NEO4J_PASSWORD:-cosmetics})"
note "Postgres      : jarvis-postgres, database 'cosmetics'"
echo
note "Credentials that lift coverage (optional, put in cosmetics/.env):"
note "  DATA_GOV_IN_KEY=...   free at https://data.gov.in — lifts the MCA crawl off the"
note "                        shared key's 10-rows-per-request / ~150-request ceiling"
note "  GST_PROVIDER_KEY=...  a GSTIN verification API — required for trust level L3"
echo
note "Pipeline:"
note "  1. ingest    : make ingest-all          # every credential-free source"
note "  2. stages 2-8: make pipeline            # normalize -> resolve -> load -> derive -> trust -> validate"
note "  3. coverage  : make coverage            # the honest number for customer conversations"
