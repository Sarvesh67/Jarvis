#!/usr/bin/env bash
# Dashboard: uv venv + deps + LaunchAgent (autostart at login, restart on crash).
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
ensure_path
have uv || fail "uv not found."

cd "$DASHBOARD_DIR"
[ -d .venv ] || { log "Creating dashboard venv (py3.12)..."; uv venv --python 3.12; }
if [ -f requirements.lock ]; then uv pip install --python .venv -r requirements.lock
else uv pip install --python .venv fastapi "uvicorn[standard]" httpx falkordb; uv pip freeze --python .venv > requirements.lock; fi

# generate the dashboard bearer token once (gates /api + /ws now that we bind 0.0.0.0)
ENV_FILE="$PLATFORM_DIR/.env"
if ! grep -q "^DASHBOARD_TOKEN=" "$ENV_FILE" 2>/dev/null; then
  TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
  echo "DASHBOARD_TOKEN=$TOKEN" >> "$ENV_FILE"
  log "Generated DASHBOARD_TOKEN in platform/.env"
fi

# install + (re)load the LaunchAgent
PLIST=~/Library/LaunchAgents/com.jarvis.dashboard.plist
cp "$JARVIS_ROOT/launchagents/com.jarvis.dashboard.plist" "$PLIST"
launchctl bootout "gui/$(id -u)/com.jarvis.dashboard" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

log "Waiting for dashboard..."
# Probe the app shell (/) — it's unauthenticated; /api now requires DASHBOARD_TOKEN.
for _ in $(seq 1 15); do curl -fsS -o /dev/null http://127.0.0.1:8080/ 2>/dev/null && break; sleep 1; done
curl -fsS -o /dev/null http://127.0.0.1:8080/ && ok "Dashboard up at http://127.0.0.1:8080 (also on 0.0.0.0 for LAN/Tailscale)" || fail "Dashboard not responding — check $DASHBOARD_DIR/dashboard.log"
