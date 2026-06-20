#!/usr/bin/env bash
# Deploys the brain ROUTER (formerly stub) under /Users/hedgefund/brain-stub/,
# creates its venv, and installs the LaunchAgent that binds 127.0.0.1:8765.
#
# v0.2: brain-stub is now a smart router. It receives Hermes's requests, picks
# a model tier (haiku/sonnet/opus) by heuristic, and forwards to the Claude
# Code CLI bridge at 127.0.0.1:3456. The bridge must be running separately as
# sarvesh.

source "$(dirname "$0")/_lib.sh"
require_sarvesh
ensure_sudo

# --- 0. Pre-flight: bridge reachable? ---
BRIDGE_URL="${BRAIN_BRIDGE_URL:-http://127.0.0.1:3456/v1/models}"
log "Checking Claude Code bridge at $BRIDGE_URL ..."
if curl -sS -m 3 "$BRIDGE_URL" | grep -q '"data"'; then
    ok "Bridge reachable."
else
    warn "Bridge NOT reachable at $BRIDGE_URL."
    note "  The router will install fine, but every brain call will return 503"
    note "  until the bridge is up. Start your CC CLI bridge before running"
    note "  Hermes interactively. (RUNBOOK has the details.)"
fi

# --- 1. Copy the router source into hedgefund's home ---
log "Staging brain router under $HEDGEFUND_BRAIN ..."
sudo mkdir -p "$HEDGEFUND_BRAIN"
sudo cp "$JARVIS_ROOT/brain-stub/server.py" "$HEDGEFUND_BRAIN/server.py"
sudo cp "$JARVIS_ROOT/brain-stub/requirements.txt" "$HEDGEFUND_BRAIN/requirements.txt"

# Deploy .env template if no real .env exists yet — preserves the user's key on re-runs.
if [[ ! -f "$HEDGEFUND_BRAIN/.env" ]]; then
    sudo cp "$JARVIS_ROOT/brain-stub/.env.example" "$HEDGEFUND_BRAIN/.env"
    note "  .env created at $HEDGEFUND_BRAIN/.env (default: Meridian on localhost:3456)"
    note "  To switch to OpenRouter, edit that file (see comments inside)"
fi
sudo chown -R hedgefund:staff "$HEDGEFUND_BRAIN"
sudo chmod 600 "$HEDGEFUND_BRAIN/.env"

# --- 2. Create venv and install deps as hedgefund ---
log "Creating venv and installing FastAPI + uvicorn ..."
sudo -u hedgefund -H bash -lc "
    set -e
    cd '$HEDGEFUND_BRAIN'
    python3 -m venv .venv
    source .venv/bin/activate
    pip install --quiet --upgrade pip
    pip install --quiet -r requirements.txt
" || fail "brain-stub venv setup failed."
ok "brain-stub venv ready."

# --- 3. Deploy as LaunchDaemon (system domain) ---
# We use a LaunchDaemon, not a LaunchAgent, because hedgefund is a non-GUI
# user (never logs in). User-level launchd domains (`gui/<uid>`) don't exist
# until first GUI login. LaunchDaemons live in the system domain, are loaded
# by root at boot, and run as the user named in <UserName>hedgefund</UserName>
# (already set in the plist).
DAEMON_DST="/Library/LaunchDaemons/com.hedgefund.brain-stub.plist"

# Clean up any old LaunchAgent attempt from a previous deployment
OLD_AGENT="$HEDGEFUND_HOME/Library/LaunchAgents/com.hedgefund.brain-stub.plist"
if [[ -f "$OLD_AGENT" ]]; then
    log "Removing old LaunchAgent (replacing with LaunchDaemon) ..."
    sudo launchctl bootout "gui/$(id -u hedgefund)/com.hedgefund.brain-stub" 2>/dev/null || true
    sudo rm -f "$OLD_AGENT"
fi

log "Installing LaunchDaemon at $DAEMON_DST ..."
sudo cp "$JARVIS_ROOT/launchagents/com.hedgefund.brain-stub.plist" "$DAEMON_DST"
sudo chown root:wheel "$DAEMON_DST"
sudo chmod 644 "$DAEMON_DST"

# Make sure hedgefund can write the log files referenced in the plist
sudo -u hedgefund mkdir -p /Users/hedgefund/financial-data/logs

# --- 4. Load into system domain ---
log "Loading LaunchDaemon into system domain ..."
sudo launchctl bootout system/com.hedgefund.brain-stub 2>/dev/null || true
sudo launchctl bootstrap system "$DAEMON_DST" || \
    fail "launchctl bootstrap failed. Run 'sudo launchctl print system/com.hedgefund.brain-stub' for details."

# Force-start (RunAtLoad should fire, but be explicit)
sudo launchctl kickstart -k system/com.hedgefund.brain-stub 2>/dev/null || true

# Wait up to 10s for the server to come up
for i in {1..10}; do
    curl -sS -m 1 http://127.0.0.1:8765/healthz >/dev/null 2>&1 && break
    sleep 1
done

# --- 5. Smoke tests ---
log "Smoke test 1: router /healthz ..."
if curl -sS -m 3 http://127.0.0.1:8765/healthz | grep -q '"ok":true'; then
    ok "Router responding on 127.0.0.1:8765."
else
    warn "Router not responding. Check logs: $HEDGEFUND_DATA/logs/brain-stub.err.log"
fi

log "Smoke test 2: end-to-end through router \u2192 bridge ..."
resp=$(curl -sS -m 30 -X POST http://127.0.0.1:8765/v1/chat/completions \
    -H 'content-type: application/json' \
    -d '{"model":"claude-haiku-4","messages":[{"role":"user","content":"reply pong"}],"max_tokens":10}' 2>&1 || true)
if echo "$resp" | grep -q 'pong\|choices'; then
    ok "Round-trip through router \u2192 bridge works."
    note "$(echo "$resp" | head -c 200)"
else
    warn "Round-trip failed. Check both router log AND bridge availability."
    note "Response was: $resp"
fi

ok "Step 5 complete."
