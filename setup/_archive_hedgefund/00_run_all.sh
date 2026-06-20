#!/usr/bin/env bash
# One-shot driver. Runs steps 01-06 with confirmation gates between destructive operations.
# Re-running is safe (each step is idempotent).

source "$(dirname "$0")/_lib.sh"
require_sarvesh

cat <<BANNER
${c_blu}╔══════════════════════════════════════════════════════════════╗
║          Hermes Agent — One-Shot Deployment                  ║
║          Target: Mac Mini M4 (dedicated hedgefund user)      ║
╚══════════════════════════════════════════════════════════════╝${c_rst}

This will:
  [1] Create 'hedgefund' system user and move financial-data to its home
  [2] Grant hedgefund READ-ONLY ACL on the Obsidian fortress
  [3] Install Ollama and build Llama-Open-Finance from DragonLLM source
  [4] Install mac-messages-mcp + HTTP bridge (LaunchAgent)
  [5] Deploy stub brain (LaunchAgent, binds 127.0.0.1:8765)
  [6] Install Hermes Agent, drop config, install gateway

Prerequisites:
  • sarvesh is a macOS admin with sudo
  • Homebrew installed  ($(command -v brew >/dev/null && echo YES || echo NO))
  • Messages.app signed in
  • Claude Code CLI bridge running on 127.0.0.1:3456
    ($(curl -sS -m 2 http://127.0.0.1:3456/v1/models 2>/dev/null | grep -q '"data"' && echo "YES — bridge reachable" || echo "NOT REACHABLE — start it before Stage 2 smoke test"))
  • ~30GB free disk (Llama-Open-Finance build needs ~16GB temp)

${c_yel}Step 1 is DESTRUCTIVE (creates a user, moves financial-data).${c_rst}
${c_yel}Step 2 modifies ACLs on the fortress (non-destructive, reversible).${c_rst}

BANNER

confirm "Proceed with the full sequence?" || fail "Aborted."

ensure_sudo

steps=(
    "01_create_user.sh"
    "02_fortress_acls.sh"
    "03_install_ollama.sh"
    "04_messages_bridge.sh"
    "05_brain_stub.sh"
    "06_install_hermes.sh"
)

HERE="$(cd "$(dirname "$0")" && pwd)"
for script in "${steps[@]}"; do
    printf "\n${c_blu}════════ %s ════════${c_rst}\n" "$script"
    bash "$HERE/$script"
    printf "\n"
done

cat <<DONE

${c_grn}╔══════════════════════════════════════════════════════════════╗
║                   Setup complete.                            ║
╚══════════════════════════════════════════════════════════════╝${c_rst}

Next steps (manual):
  • Grant Full Disk Access to mcp-proxy (see step 4 output)
  • Verify:  curl http://127.0.0.1:5000/   (iMessage bridge)
  • Verify:  curl http://127.0.0.1:8765/healthz  (stub brain)
  • First Hermes session:  sudo -u hedgefund -H hermes
  • Run phase0-verify:  (inside hermes) /skill run phase0-verify
  • Schedule cron:  sudo -u hedgefund -H hermes cron create "every 15 minutes" "run rss-poll skill"

Rollback doc:  $JARVIS_ROOT/RUNBOOK.md

DONE
