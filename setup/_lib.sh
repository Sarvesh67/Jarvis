#!/usr/bin/env bash
# Shared helpers for Jarvis setup scripts. Everything runs as the logged-in user (sarvesh) —
# there is no separate service account. Source this at the top of each numbered script.
set -euo pipefail

JARVIS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLATFORM_DIR="$JARVIS_ROOT/platform"
COGNEE_DIR="$JARVIS_ROOT/cognee"
DASHBOARD_DIR="$JARVIS_ROOT/dashboard"
GATEWAY="http://127.0.0.1:4000"

c_blue=$'\033[0;36m'; c_green=$'\033[0;32m'; c_yellow=$'\033[0;33m'; c_red=$'\033[0;31m'; c_off=$'\033[0m'
log()  { echo "${c_blue}▶${c_off} $*"; }
ok()   { echo "${c_green}✓${c_off} $*"; }
warn() { echo "${c_yellow}⚠${c_off} $*"; }
fail() { echo "${c_red}✗ $*${c_off}" >&2; exit 1; }
note() { echo "  $*"; }

# Make brew, uv, and hermes reachable in non-login shells.
ensure_path() {
  [ -x /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)"
  export PATH="$HOME/.local/bin:$PATH"
}

have() { command -v "$1" >/dev/null 2>&1; }

# Read a value from platform/.env (KEY -> value), empty if missing.
env_get() { grep -m1 "^$1=" "$PLATFORM_DIR/.env" 2>/dev/null | cut -d= -f2- || true; }

require_platform_env() {
  [ -f "$PLATFORM_DIR/.env" ] || fail "platform/.env missing — copy platform/.env.example and fill it in."
}

litellm_up() { curl -fsS "$GATEWAY/health/liveliness" >/dev/null 2>&1; }
