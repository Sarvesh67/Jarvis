#!/usr/bin/env bash
# Resume Stage 1 deployment from step 3 onward.
#
# Single sudo prompt at the start (kept alive across all sub-scripts), no per-step
# y/N prompts, automatic cleanup of build intermediates. Runs from /tmp so the
# hedgefund subshells don't trip getcwd() on sarvesh's Documents (mode 750).
#
# Use this AFTER 01_create_user.sh and 02_fortress_acls.sh have completed.

set -e

HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/_lib.sh"
require_sarvesh
ensure_sudo   # one-shot prompt; keepalive inherited by every sub-script

# Run from a world-readable cwd — sudo'd hedgefund bashes inherit cwd from us,
# and they print noisy "shell-init: getcwd: Permission denied" warnings if cwd
# is unreadable to hedgefund.
cd /tmp

steps=(
    "03_install_ollama.sh"
    "04_messages_bridge.sh"
    "05_brain_stub.sh"
    "06_install_hermes.sh"
)

for s in "${steps[@]}"; do
    printf "\n${c_blu}════════ %s ════════${c_rst}\n" "$s"
    bash "$HERE/$s"
done

printf "\n${c_blu}════════ post-deploy verification ════════${c_rst}\n"
bash "$HERE/../tests/postdeploy_check.sh" || true

ok "Stage 1 deployment complete. Remember to grant Full Disk Access to mcp-proxy if you haven't already."
