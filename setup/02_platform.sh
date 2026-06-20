#!/usr/bin/env bash
# Bring up the data + gateway stack: FalkorDB + Postgres + LiteLLM.
# Generates platform/.env secrets on first run (you supply only the OpenRouter key).
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
ensure_path

cd "$PLATFORM_DIR"

# --- platform/.env (generate secrets once; prompt for OpenRouter key) ---
if [ ! -f .env ]; then
  log "Generating platform/.env secrets..."
  umask 077
  cat > .env <<EOF
POSTGRES_USER=jarvis
POSTGRES_PASSWORD=$(openssl rand -hex 24)
POSTGRES_DB=jarvis
LITELLM_MASTER_KEY=sk-$(openssl rand -hex 24)
LITELLM_SALT_KEY=$(openssl rand -hex 24)
OPENROUTER_API_KEY=
EOF
  chmod 600 .env
fi
if [ -z "$(env_get OPENROUTER_API_KEY)" ]; then
  warn "OPENROUTER_API_KEY is empty in platform/.env."
  note "Add it: open -e $PLATFORM_DIR/.env  (paste your sk-or-... key after OPENROUTER_API_KEY=)"
  fail "Set the OpenRouter key, then re-run."
fi

# --- bring up the stack ---
log "Starting FalkorDB + Postgres + LiteLLM..."
docker compose up -d

log "Waiting for LiteLLM gateway..."
for _ in $(seq 1 60); do litellm_up && break; sleep 3; done
litellm_up && ok "Gateway up at $GATEWAY (UI: $GATEWAY/ui)." || fail "Gateway did not come up — check: docker compose logs litellm"
docker compose ps --format '{{.Name}}\t{{.Status}}'
