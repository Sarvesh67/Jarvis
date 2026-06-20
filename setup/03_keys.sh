#!/usr/bin/env bash
# Create per-project LiteLLM virtual keys with monthly budgets (idempotent).
# Budgets total $100/mo: hedgefund $40, msme $40, jarvis $20.
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
ensure_path
require_platform_env
litellm_up || fail "Gateway down — run 02_platform.sh first."

MASTER="$(env_get LITELLM_MASTER_KEY)"
MODELS='["extractor","reasoner","fast","local-finance","embed"]'

# alias  budget  env-var-name
ensure_key() {
  local alias="$1" budget="$2" var="$3"
  local existing; existing="$(env_get "$var")"
  if [ -n "$existing" ] && curl -fsS "$GATEWAY/key/info?key=$existing" -H "Authorization: Bearer $MASTER" >/dev/null 2>&1; then
    curl -fsS -X POST "$GATEWAY/key/update" -H "Authorization: Bearer $MASTER" -H "Content-Type: application/json" \
      -d "{\"key\":\"$existing\",\"max_budget\":$budget}" >/dev/null
    ok "$alias key exists — budget set to \$$budget."
    return
  fi
  log "Creating $alias key (\$$budget/30d)..."
  local key; key="$(curl -fsS "$GATEWAY/key/generate" -H "Authorization: Bearer $MASTER" -H "Content-Type: application/json" \
    -d "{\"key_alias\":\"$alias\",\"max_budget\":$budget,\"budget_duration\":\"30d\",\"models\":$MODELS}" \
    | python3 -c "import sys,json;print(json.load(sys.stdin).get('key',''))")"
  [ -n "$key" ] || fail "Failed to create $alias key."
  grep -vE "^$var=" "$PLATFORM_DIR/.env" > "$PLATFORM_DIR/.env.new"
  printf '%s=%s\n' "$var" "$key" >> "$PLATFORM_DIR/.env.new"
  mv "$PLATFORM_DIR/.env.new" "$PLATFORM_DIR/.env"; chmod 600 "$PLATFORM_DIR/.env"
  ok "$alias key created + saved as $var."
}

ensure_key hedgefund 40 HEDGEFUND_LLM_KEY
ensure_key msme      40 MSME_LLM_KEY
ensure_key jarvis    20 JARVIS_LLM_KEY
ok "Keys ready (view spend at $GATEWAY/ui)."
