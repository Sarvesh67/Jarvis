#!/usr/bin/env bash
# Full Jarvis platform install/rebuild. Idempotent — re-run any time.
# Order matters: prereqs -> platform -> keys -> cognee -> hermes -> dashboard.
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

log "Jarvis platform setup starting from $JARVIS_ROOT"
for step in 01_prereqs 02_platform 03_keys 04_cognee 05_hermes 06_dashboard; do
  echo
  log "=== $step ==="
  bash "$JARVIS_ROOT/setup/$step.sh"
done
echo
ok "All phases done."
note "Dashboard:  http://127.0.0.1:8080"
note "Gateway UI: http://127.0.0.1:4000/ui"
note "Graph UI:   http://127.0.0.1:3000"
note "Agents:     jarvis --tui  ·  hedgefund --tui  ·  msme --tui"
