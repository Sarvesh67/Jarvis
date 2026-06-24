#!/usr/bin/env bash
# Post-Stage-1 verification — run AFTER `./setup/00_run_all.sh` completes.
# Tests the live, deployed system. Pass criteria match RUNBOOK §Verification.

set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/../setup/_lib.sh" 2>/dev/null || true

c_red=$'\e[31m'; c_grn=$'\e[32m'; c_yel=$'\e[33m'; c_rst=$'\e[0m'

PASS=0; FAIL=0; WARN=0
check() {
    local name="$1"; shift
    if "$@" >/dev/null 2>&1; then
        printf "  ${c_grn}PASS${c_rst}  %s\n" "$name"; PASS=$((PASS+1))
    else
        printf "  ${c_red}FAIL${c_rst}  %s\n" "$name"; FAIL=$((FAIL+1))
    fi
}
warn_check() {
    local name="$1"; shift
    if "$@" >/dev/null 2>&1; then
        printf "  ${c_grn}PASS${c_rst}  %s\n" "$name"; PASS=$((PASS+1))
    else
        printf "  ${c_yel}WARN${c_rst}  %s\n" "$name"; WARN=$((WARN+1))
    fi
}

FORTRESS="/Users/sarvesh/Library/Mobile Documents/iCloud~md~obsidian/Documents/Library of Mnemosyne"

echo
echo "=== 1. User isolation ==="
if id hedgefund >/dev/null 2>&1; then
    PASS=$((PASS+1)); printf "  ${c_grn}PASS${c_rst}  hedgefund user exists\n"
    # These checks are only meaningful when the user actually exists.
    check "hedgefund cannot ls sarvesh's Desktop" \
        bash -c "! sudo -u hedgefund ls /Users/sarvesh/Desktop 2>/dev/null"
    check "hedgefund CAN list fortress root"  sudo -u hedgefund ls "$FORTRESS"
    # Also verify a file inside is readable (ACL must have propagated to existing content)
    check "hedgefund CAN read a file in fortress" bash -c "
        f=\$(sudo -u hedgefund find '$FORTRESS' -maxdepth 3 -type f ! -name '*.icloud' 2>/dev/null | head -1)
        [[ -n \"\$f\" ]] && sudo -u hedgefund test -r \"\$f\"
    "
    check "hedgefund CANNOT write fortress" \
        bash -c "! sudo -u hedgefund touch '$FORTRESS/.write-probe' 2>/dev/null"
else
    FAIL=$((FAIL+1)); printf "  ${c_red}FAIL${c_rst}  hedgefund user exists (skipping dependent checks)\n"
fi

echo
echo "=== 2. Filesystem layout ==="
check "/Users/hedgefund/financial-data exists"     test -d /Users/hedgefund/financial-data
check "/Users/hedgefund/.hermes exists"            test -d /Users/hedgefund/.hermes
# These files live under hedgefund's mode-700 .hermes/ dir — sarvesh can't read
# in there, so use sudo to check existence as root.
check "Hermes config installed"                    sudo test -f /Users/hedgefund/.hermes/config.yaml
check "phase0-verify skill staged"                 sudo test -f /Users/hedgefund/.hermes/skills/data-collection/phase0-verify/SKILL.md

echo
echo "=== 3. Bridge (Claude Code CLI) ==="
check "bridge reachable on 3456"        curl -sS -m 3 http://127.0.0.1:3456/v1/models
check "bridge exposes haiku"            bash -c "curl -sS http://127.0.0.1:3456/v1/models | grep -q claude-haiku"

echo
echo "=== 4. Brain router ==="
check "router /healthz responds"        curl -sS -m 3 http://127.0.0.1:8765/healthz
check "router lists models"             curl -sS -m 3 http://127.0.0.1:8765/v1/models
check "router round-trip to bridge"  bash -c "
    resp=\$(curl -sS -m 30 -X POST http://127.0.0.1:8765/v1/chat/completions \
        -H 'content-type: application/json' \
        -d '{\"model\":\"auto\",\"messages\":[{\"role\":\"user\",\"content\":\"reply pong\"}]}')
    echo \"\$resp\" | grep -q choices
"

echo
echo "=== 5. iMessage bridge ==="
# NB: macOS AirPlay Receiver also uses port 5000. Just curling / can give a
# false positive. Check that the LaunchAgent is actually loaded and that the
# response looks like an MCP server (SSE or MCP-flavored JSON), not AirPlay.
warn_check "messages-bridge LaunchAgent loaded" bash -c "
    launchctl list 2>/dev/null | grep -q com.sarvesh.messages-bridge
"
warn_check "port 5000 belongs to mcp-proxy"     bash -c "
    lsof -nP -iTCP:5000 -sTCP:LISTEN 2>/dev/null | grep -qE 'mcp-proxy|python|uv'
"
warn_check "no recent errors in bridge log"     bash -c "
    log=/Users/sarvesh/Library/Logs/messages-bridge.err.log
    [[ ! -s \$log ]] || ! tail -50 \$log | grep -qi 'permission denied'
"

echo
echo "=== 6. Ollama ==="
warn_check "ollama service responding"          curl -sS -m 3 http://127.0.0.1:11434/api/tags
warn_check "nomic-embed-text model registered"  bash -c "
    ollama list 2>/dev/null | grep -qw nomic-embed-text
"

echo
echo "=== 7. Hermes daemon ==="
# Hermes installs at /Users/hedgefund/.local/bin/hermes which isn't on PATH
# for `bash -lc` non-login shells. Check the binary file directly.
warn_check "Hermes installed"                   sudo test -x /Users/hedgefund/.local/bin/hermes
# Hermes gateway may live in either user (gui/<uid>) or system domain.
# Check both via sudo launchctl list, plus the on-disk plist.
warn_check "Hermes gateway in launchctl"        bash -c "
    sudo launchctl list 2>/dev/null | grep -qi 'hermes\\|nous' ||
    sudo find /Library/LaunchDaemons /Users/hedgefund/Library/LaunchAgents -maxdepth 1 -iname '*hermes*' 2>/dev/null | grep -q .
"

echo
echo "=== 8. Logs ==="
check "brain-tokens.jsonl writable by hedgefund"  bash -c "
    sudo -u hedgefund touch /Users/hedgefund/financial-data/logs/.write-probe &&
    sudo -u hedgefund rm /Users/hedgefund/financial-data/logs/.write-probe
"

echo
printf "${c_grn}PASS:${c_rst} %d   ${c_red}FAIL:${c_rst} %d   ${c_yel}WARN:${c_rst} %d\n" "$PASS" "$FAIL" "$WARN"
echo
if [[ $FAIL -gt 0 ]]; then
    echo "${c_red}\u2717 deployment incomplete or partially broken${c_rst}"
    exit 1
elif [[ $WARN -gt 0 ]]; then
    echo "${c_yel}\u26a0  core works, some optional pieces (Ollama / iMessage FDA / Hermes gateway) need attention${c_rst}"
    exit 0
else
    echo "${c_grn}\u2713 deployment fully verified${c_rst}"
    exit 0
fi
