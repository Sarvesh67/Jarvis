#!/usr/bin/env bash
# MSME preprocessing env: uv venv (py3.12) + pinned deps. Turns the Udyam registry into the
# deterministic supply-chain backbone in the msme FalkorDB graph + a DuckDB registry for search.
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
ensure_path
have uv || fail "uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"

MSME_DIR="$JARVIS_ROOT/msme"
cd "$MSME_DIR"
[ -d .venv ] || { log "Creating msme venv (py3.12)..."; uv venv --python 3.12; }

if [ -f requirements.lock ]; then
  log "Installing msme deps from requirements.lock..."
  uv pip install --python .venv -r requirements.lock
else
  log "Installing msme deps (no lock found)..."
  uv pip install --python .venv -r requirements.txt
  uv pip freeze --python .venv > requirements.lock
fi

ok "MSME env ready. Pipeline:"
echo "  1. NIC master  : ./.venv/bin/python -m msme.nic_master --file nic2008.xlsx"
echo "  2. enrich      : ./.venv/bin/python -m msme.preprocess --csv udyam.csv --enrich-only"
echo "  3. preprocess  : ./.venv/bin/python -m msme.preprocess --csv udyam.csv --connected-only --emit-docs"
echo "  4. graph       : ./.venv/bin/python -m msme.graph --wipe"
echo "  5. search      : ./.venv/bin/python -m msme.search --graph --need 'cotton yarn' --role supplier --district surat"
