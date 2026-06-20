#!/usr/bin/env bash
# Prereqs: Homebrew, Colima + Docker CLI + Compose, Ollama + local models.
# Idempotent — safe to re-run.
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
ensure_path

# --- Homebrew ---
if have brew; then ok "Homebrew present."; else
  log "Installing Homebrew (will prompt for your password)..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  eval "$(/opt/homebrew/bin/brew shellenv)"
  grep -q 'brew shellenv' ~/.zprofile 2>/dev/null || \
    echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
fi

# --- Colima + Docker CLI + Compose ---
for pkg in colima docker docker-compose; do
  brew list --formula 2>/dev/null | grep -qx "$pkg" && ok "$pkg present." || { log "brew install $pkg"; brew install "$pkg"; }
done
# Make `docker compose` find the Homebrew plugin.
python3 - <<'PY'
import json, os
p = os.path.expanduser("~/.docker/config.json"); os.makedirs(os.path.dirname(p), exist_ok=True)
cfg = json.load(open(p)) if os.path.exists(p) else {}
dirs = set(cfg.get("cliPluginsExtraDirs", [])); dirs.add("/opt/homebrew/lib/docker/cli-plugins")
cfg["cliPluginsExtraDirs"] = sorted(dirs); json.dump(cfg, open(p, "w"), indent=2)
PY

# --- Colima VM (start + autostart at login) ---
if colima status >/dev/null 2>&1; then ok "Colima running."; else
  log "Starting Colima (vz, 4 CPU / 4GB / 60GB)..."
  colima start --vm-type vz --mount-type virtiofs --cpu 4 --memory 4 --disk 60
fi
brew services list 2>/dev/null | grep -q '^colima.*started' || { log "Enabling Colima autostart..."; brew services start colima; }

# --- Ollama + local models (embeddings + domain bulk) ---
have ollama || fail "Ollama not found. Install from https://ollama.com then re-run."
curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1 || warn "Ollama not serving on :11434 — start it (the app or 'ollama serve')."
ollama list 2>/dev/null | grep -q '^nomic-embed-text' && ok "nomic-embed-text present." || { log "Pulling nomic-embed-text..."; ollama pull nomic-embed-text; }

ok "Prereqs ready."
