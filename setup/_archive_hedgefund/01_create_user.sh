#!/usr/bin/env bash
# Creates the hedgefund user and moves /Users/sarvesh/financial-data/ → /Users/hedgefund/financial-data/
# DESTRUCTIVE: moves a folder and creates a system user. Re-entrant: safe to run twice.

source "$(dirname "$0")/_lib.sh"
require_sarvesh
ensure_sudo

# --- 1. Pre-flight path-reference check ---
log "Scanning for hardcoded references to /Users/sarvesh/financial-data/ before move..."
refs=$(grep -rIl --exclude-dir=.venv --exclude-dir=node_modules \
    '/Users/sarvesh/financial-data' \
    "$SARVESH_HOME/Documents" "$SARVESH_HOME/.zshrc" "$SARVESH_HOME/.bashrc" \
    "$SARVESH_HOME/.bash_profile" 2>/dev/null || true)
if [[ -n "$refs" ]]; then
    warn "These files reference /Users/sarvesh/financial-data/ and will break after move:"
    printf "%s\n" "$refs" | sed 's/^/    /'
    confirm "Proceed anyway? (will create the /Users/sarvesh/financial-data-ro symlink as a compatibility shim)" \
        || fail "Aborted by user. Update references first."
else
    ok "No hardcoded references found."
fi

# --- 2. Create hedgefund user (idempotent) ---
if user_exists hedgefund; then
    ok "User 'hedgefund' already exists — skipping creation."
else
    log "Creating user 'hedgefund' (non-admin, role account)..."
    # Prompt for a password (won't be echoed). Using -password - reads from stdin.
    printf "\n    A password is required. You'll type it twice.\n"
    sudo sysadminctl -addUser hedgefund -fullName "AI Hedge Fund Agent" -password -
    sudo dscl . create /Users/hedgefund IsHidden 1
    ok "User created."
fi

# --- 3. Move financial-data (idempotent) ---
if [[ -d "$HEDGEFUND_DATA" && ! -L "$HEDGEFUND_DATA" ]]; then
    ok "Data already at $HEDGEFUND_DATA — skipping move."
elif [[ -d "$SARVESH_OLD_DATA" && ! -L "$SARVESH_OLD_DATA" ]]; then
    log "Moving $SARVESH_OLD_DATA → $HEDGEFUND_DATA ..."
    sudo mv "$SARVESH_OLD_DATA" "$HEDGEFUND_DATA"
    ok "Data moved."
else
    warn "Neither source nor destination is a real directory. Creating empty $HEDGEFUND_DATA."
    sudo mkdir -p "$HEDGEFUND_DATA"
fi

# --- 4. Ownership and mode ---
log "Setting ownership and mode on $HEDGEFUND_DATA ..."
sudo chown -R hedgefund:staff "$HEDGEFUND_DATA"
sudo chmod 700 "$HEDGEFUND_DATA"

# Grant sarvesh read+list via ACL so you can still inspect the data.
log "Granting sarvesh read-only ACL on financial-data ..."
sudo chmod +a "sarvesh allow read,list,file_inherit,directory_inherit" "$HEDGEFUND_DATA" 2>/dev/null || true
ok "ACL set."

# --- 5. Compatibility symlink for anything still referencing the old path ---
if [[ ! -e "$SARVESH_OLD_DATA" ]]; then
    ln -s "$HEDGEFUND_DATA" "$SARVESH_OLD_DATA" 2>/dev/null || true
    ok "Created symlink $SARVESH_OLD_DATA -> $HEDGEFUND_DATA (old paths still work, read-only)."
fi

# --- 6. Logs subdir (skills write here) ---
sudo -u hedgefund mkdir -p "$HEDGEFUND_DATA/logs" "$HEDGEFUND_DATA/for-vault"

ok "Step 1 complete. hedgefund user ready, data at $HEDGEFUND_DATA."
