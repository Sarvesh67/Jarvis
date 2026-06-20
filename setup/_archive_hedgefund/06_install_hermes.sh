#!/usr/bin/env bash
# Installs Hermes Agent as hedgefund, drops config, and installs the gateway launchd agent.

source "$(dirname "$0")/_lib.sh"
require_sarvesh
ensure_sudo

# --- 1. Install Hermes Agent ---
# Hermes installer drops the binary at ~/.local/bin/hermes, which is NOT on the
# default PATH for non-login bash shells. Every subshell here explicitly prepends
# ~/.local/bin so `hermes` is reachable.
HERMES_BIN="/Users/hedgefund/.local/bin/hermes"
if sudo test -x "$HERMES_BIN"; then
    ok "Hermes already installed at $HERMES_BIN."
else
    log "Installing Hermes Agent under hedgefund..."
    sudo -u hedgefund -H bash -lc '
        cd "$HOME"
        export PATH="$HOME/.local/bin:$PATH"
        curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
    ' || fail "Hermes install failed."
    sudo test -x "$HERMES_BIN" || fail "Installer ran but $HERMES_BIN missing."
fi

# --- 2. Config-schema verification checkpoint ---
log "Capturing hermes --help output for schema sanity-check..."
HELP_FILE="$HEDGEFUND_HERMES/_install-help.txt"
sudo mkdir -p "$HEDGEFUND_HERMES"
sudo chown hedgefund:staff "$HEDGEFUND_HERMES"
sudo -u hedgefund -H bash -lc "cd \"\$HOME\" && export PATH=\"\$HOME/.local/bin:\$PATH\" && hermes --help > '$HELP_FILE' 2>&1 || true"

schema_ok=1
for kw in gateway cron config; do
    if ! sudo grep -q "$kw" "$HELP_FILE"; then
        warn "'$kw' not found in hermes --help. Config draft may need adjustment."
        schema_ok=0
    fi
done
[[ $schema_ok -eq 1 ]] && ok "Hermes subcommand sanity-check passed."

# --- 3. Drop config.yaml ---
log "Installing config.yaml..."
sudo cp "$JARVIS_ROOT/hermes-config/config.yaml" "$HEDGEFUND_HERMES/config.yaml"
sudo chown hedgefund:staff "$HEDGEFUND_HERMES/config.yaml"
sudo chmod 600 "$HEDGEFUND_HERMES/config.yaml"
ok "Config installed."

# --- 4. Stage the first skill ---
log "Installing phase0-verify skill..."
SKILL_DST="$HEDGEFUND_HERMES/skills/data-collection/phase0-verify"
sudo mkdir -p "$SKILL_DST/scripts"
sudo cp "$JARVIS_ROOT/skills/phase0-verify/SKILL.md" "$SKILL_DST/SKILL.md"
sudo cp "$JARVIS_ROOT/skills/phase0-verify/scripts/phase0_verify.py" "$SKILL_DST/scripts/phase0_verify.py"
sudo chown -R hedgefund:staff "$SKILL_DST"
sudo chmod +x "$SKILL_DST/scripts/phase0_verify.py"
ok "Skill staged."

# --- 5. Gateway install (24/7 launchd agent) ---
log "Running 'hermes gateway install' under hedgefund..."
if sudo -u hedgefund -H bash -lc 'cd "$HOME" && export PATH="$HOME/.local/bin:$PATH" && hermes gateway install' 2>&1; then
    ok "Gateway installed."
else
    warn "gateway install failed. If the subcommand isn't called 'gateway', look at: $HELP_FILE"
    note "Fallback: write your own plist targeting 'hermes server' or similar."
fi

# --- 6. Smoke-test the full chain (hermes -> router -> bridge) ---
log "Sanity-checking Hermes -> router -> bridge..."
# The router smoke test in step 5 already proved router -> bridge.
# We just verify Hermes can reach the router. Try non-interactive first;
# fall back to a manual instruction if that flag isn't supported.
SMOKE=$(sudo -u hedgefund -H bash -lc 'cd "$HOME" && export PATH="$HOME/.local/bin:$PATH" && echo "reply with the single word: ready" | hermes 2>&1' 2>&1 | head -30 || true)
if echo "$SMOKE" | grep -qi 'ready\|assistant'; then
    ok "Hermes responded through the router."
    note "$(echo "$SMOKE" | head -3)"
else
    warn "Hermes did not respond as expected. Test manually:"
    note "  sudo -u hedgefund -H hermes"
    note "  > reply with the single word: ready"
fi

ok "Step 6 complete."
