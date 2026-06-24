#!/usr/bin/env bash
# Cognee env: uv venv (py3.12) + pinned deps. Brain/embeddings via the gateway, graph on FalkorDB.
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
ensure_path
have uv || fail "uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"

cd "$COGNEE_DIR"
[ -d .venv ] || { log "Creating cognee venv (py3.12)..."; uv venv --python 3.12; }

if [ -f requirements.lock ]; then
  log "Installing cognee deps from requirements.lock..."
  uv pip install --python .venv -r requirements.lock
else
  log "Installing cognee + falkor adapter (no lock found)..."
  uv pip install --python .venv cognee cognee-community-hybrid-adapter-falkor
  uv pip freeze --python .venv > requirements.lock
fi

log "Syncing per-project model maps to the gateway (DB-pool models + key allowlists)..."
for proj in hedgefund msme; do
  ./.venv/bin/python sync_models.py --project "$proj" || warn "model sync for $proj had issues — check gateway."
done

log "Smoke test (hedgefund: add -> cognify -> search)..."
./.venv/bin/python smoke_test.py hedgefund 2>/dev/null | grep -A2 "=== RESULT ===" || warn "Smoke test produced no RESULT — check FalkorDB + gateway."
ok "Cognee ready. Query a graph: ./.venv/bin/python query.py <project> \"<question>\""
