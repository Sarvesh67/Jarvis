#!/usr/bin/env bash
# Shared helpers sourced by every setup/NN_*.sh script.
# Keep dependency-free: only coreutils + macOS builtins.

set -euo pipefail

# --- paths (single source of truth) ---
export JARVIS_ROOT="/Users/sarvesh/Documents/Jarvis"
export HEDGEFUND_HOME="/Users/hedgefund"
export HEDGEFUND_DATA="${HEDGEFUND_HOME}/financial-data"
export HEDGEFUND_HERMES="${HEDGEFUND_HOME}/.hermes"
export HEDGEFUND_BRAIN="${HEDGEFUND_HOME}/brain-stub"
export SARVESH_HOME="/Users/sarvesh"
export SARVESH_OLD_DATA="${SARVESH_HOME}/financial-data"
export FORTRESS="${SARVESH_HOME}/Library/Mobile Documents/iCloud~md~obsidian/Documents/Library of Mnemosyne"

# --- output helpers ---
c_red=$'\e[31m'; c_grn=$'\e[32m'; c_yel=$'\e[33m'; c_blu=$'\e[34m'; c_rst=$'\e[0m'
log()   { printf "%s[+]%s %s\n" "$c_blu" "$c_rst" "$*"; }
ok()    { printf "%s[✓]%s %s\n" "$c_grn" "$c_rst" "$*"; }
warn()  { printf "%s[!]%s %s\n" "$c_yel" "$c_rst" "$*" >&2; }
fail()  { printf "%s[✗]%s %s\n" "$c_red" "$c_rst" "$*" >&2; exit 1; }
note()  { printf "    %s\n" "$*"; }

confirm() {
    # confirm "message" → 0 if yes, 1 otherwise
    local msg="$1"
    read -r -p "${c_yel}?${c_rst} ${msg} [y/N] " ans
    [[ "${ans:-}" =~ ^[Yy]$ ]]
}

require_admin() {
    if ! groups | grep -qw admin; then
        fail "Current user must be an admin (sudo needed)."
    fi
}

require_sarvesh() {
    if [[ "$(whoami)" != "sarvesh" ]]; then
        fail "This script must run as sarvesh (got $(whoami))."
    fi
}

user_exists() {
    dscl . -read "/Users/$1" >/dev/null 2>&1
}

ensure_sudo() {
    # Cache sudo credentials up front so the script doesn't hang mid-way.
    # If a parent script already started a keepalive, inherit it — no re-prompt.
    if [[ -n "${SUDO_KEEPALIVE_PID:-}" ]] && kill -0 "$SUDO_KEEPALIVE_PID" 2>/dev/null; then
        return 0
    fi
    sudo -v || fail "sudo required"
    ( while true; do sudo -n true; sleep 50; done ) 2>/dev/null &
    SUDO_KEEPALIVE_PID=$!
    export SUDO_KEEPALIVE_PID
    trap 'kill "$SUDO_KEEPALIVE_PID" 2>/dev/null || true' EXIT
}
