#!/usr/bin/env bash
# Installs mac-messages-mcp + mcp-proxy as sarvesh, and deploys the HTTP bridge LaunchAgent
# bound to 127.0.0.1:5000. The bridge lets Hermes (running as hedgefund) reach iMessage
# without needing its own Apple ID or Full Disk Access.

source "$(dirname "$0")/_lib.sh"
require_sarvesh
# No sudo needed for this step — user-scoped install.

# --- 1. uv (package manager) ---
if ! command -v uv >/dev/null 2>&1; then
    log "Installing uv..."
    if command -v brew >/dev/null 2>&1; then
        brew install uv
    else
        # Standalone curl installer — drops uv into ~/.local/bin
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$PATH"
    fi
    command -v uv >/dev/null 2>&1 || fail "uv install failed."
    ok "uv installed ($(uv --version))."
else
    ok "uv already installed ($(uv --version))."
fi

# --- 2. mac-messages-mcp ---
if uv tool list 2>/dev/null | grep -q '^mac-messages-mcp'; then
    ok "mac-messages-mcp already installed."
else
    log "Installing mac-messages-mcp..."
    uv tool install mac-messages-mcp || warn "mac-messages-mcp install failed; see uv output above."
fi

# --- 3. mcp-proxy (wraps stdio MCP servers as HTTP) ---
if uv tool list 2>/dev/null | grep -q '^mcp-proxy'; then
    ok "mcp-proxy already installed."
else
    log "Installing mcp-proxy..."
    uv tool install mcp-proxy || warn "mcp-proxy install failed; see uv output above."
fi

# --- 4. Resolve absolute paths for the LaunchAgent ---
MAC_MSG_BIN="$(uv tool list --show-paths 2>/dev/null | awk '/mac-messages-mcp/{print $NF}' | head -1)"
PROXY_BIN="$(command -v mcp-proxy || uv tool list --show-paths 2>/dev/null | awk '/mcp-proxy/{print $NF}' | head -1)"
# Fallback to canonical uv tool path.
[[ -x "$PROXY_BIN" ]] || PROXY_BIN="$HOME/.local/bin/mcp-proxy"
[[ -x "$MAC_MSG_BIN" ]] || MAC_MSG_BIN="$HOME/.local/bin/mac-messages-mcp"

log "Using:"
note "  mcp-proxy        = $PROXY_BIN"
note "  mac-messages-mcp = $MAC_MSG_BIN"

# --- 5. Materialize the LaunchAgent with resolved paths ---
AGENT_SRC="$JARVIS_ROOT/launchagents/com.sarvesh.messages-bridge.plist"
AGENT_DST="$SARVESH_HOME/Library/LaunchAgents/com.sarvesh.messages-bridge.plist"
mkdir -p "$(dirname "$AGENT_DST")"

sed \
    -e "s|__PROXY_BIN__|$PROXY_BIN|g" \
    -e "s|__MAC_MSG_BIN__|$MAC_MSG_BIN|g" \
    -e "s|__HOME__|$HOME|g" \
    "$AGENT_SRC" > "$AGENT_DST"

chmod 644 "$AGENT_DST"
ok "LaunchAgent written: $AGENT_DST"

# --- 6. Load it ---
launchctl unload "$AGENT_DST" 2>/dev/null || true
launchctl load "$AGENT_DST"
sleep 2

# --- 7. Smoke test ---
if curl -sS -m 3 http://127.0.0.1:5000/ >/dev/null 2>&1 || \
   curl -sS -m 3 http://127.0.0.1:5000/health >/dev/null 2>&1 || \
   curl -sS -m 3 http://127.0.0.1:5000/sse >/dev/null 2>&1; then
    ok "iMessage bridge is responding on 127.0.0.1:5000."
else
    warn "No response from 127.0.0.1:5000. Likely causes:"
    note "  - Full Disk Access not granted. Grant to: $PROXY_BIN"
    note "    (System Settings → Privacy & Security → Full Disk Access)"
    note "    Then:  launchctl unload $AGENT_DST && launchctl load $AGENT_DST"
    note "  - Tail $SARVESH_HOME/Library/Logs/messages-bridge.err.log for details."
fi

printf "\n"
warn "MANUAL STEP (one-time): Grant Full Disk Access to mcp-proxy so it can read ~/Library/Messages/chat.db"
note "  System Settings → Privacy & Security → Full Disk Access → +"
note "  Navigate to: $PROXY_BIN"
note "  Toggle ON."

ok "Step 4 complete."
