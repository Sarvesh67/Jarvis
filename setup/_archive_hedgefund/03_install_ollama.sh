#!/usr/bin/env bash
# Installs Ollama, llama.cpp (for quantization), and Llama-Open-Finance-8B
# (built locally from DragonLLM source). Single Pass-1 model — sentiment +
# 12-field YAML extraction both come from llama-open-finance.
#
# Why local quantize: the only public Q4_K_M of Llama-Open-Finance is from
# `enfantdupeuple` (single-model maintainer, low downloads). Quantizing the
# DragonLLM original ourselves gives us a vetted source.
#
# This step is the slowest in the install (~30-60min on M4: 16GB download
# + convert to GGUF + quantize). Idempotent: rerun is safe.

source "$(dirname "$0")/_lib.sh"
require_sarvesh
ensure_sudo

# --- 1. Ollama (CLI binary or .app — either is fine) ---
HAS_OLLAMA_APP=0
[[ -d /Applications/Ollama.app ]] && HAS_OLLAMA_APP=1

if command -v ollama >/dev/null 2>&1 || [[ $HAS_OLLAMA_APP -eq 1 ]]; then
    ok "Ollama already installed ($(ollama --version 2>&1 | head -1 || echo 'app bundle'))."
else
    log "Installing Ollama..."
    if command -v brew >/dev/null 2>&1; then
        brew install ollama
    else
        # Official curl installer drops Ollama.app into /Applications + CLI symlink.
        # The installer uses osascript to quit any running Ollama; ignore "Unable
        # to find application named 'Ollama'" — that just means none was running.
        /bin/bash -c "$(curl -fsSL https://ollama.com/install.sh)" || true
    fi
    [[ -d /Applications/Ollama.app ]] && HAS_OLLAMA_APP=1
    command -v ollama >/dev/null 2>&1 || fail "Ollama install failed."
    ok "Ollama installed."
fi

# --- 2. Ollama service (must be running before we can pull/create models) ---
if curl -sS -m 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    ok "Ollama service already responding on 11434."
else
    log "Starting Ollama service..."
    if [[ $HAS_OLLAMA_APP -eq 1 ]]; then
        # GUI-app install: launching the .app starts its bundled server.
        open -a Ollama || warn "open -a Ollama failed; falling back to ad-hoc serve."
    fi
    # Ad-hoc fallback (or extra safety)
    if ! curl -sS -m 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
        nohup ollama serve >/tmp/ollama-serve.log 2>&1 &
        disown 2>/dev/null || true
    fi
    # Wait up to 15s for the server to come up
    for i in {1..15}; do
        curl -sS -m 1 http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break
        sleep 1
    done
    if curl -sS -m 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
        ok "Ollama service up on 11434."
    else
        fail "Ollama did not start within 15s. Check /tmp/ollama-serve.log."
    fi
fi

# --- 3. Llama-Open-Finance-8B: download pre-quantized Q4_K_M GGUF from public mirror ---
# We used to convert+quantize from DragonLLM source, but that repo is gated (401 without
# HF account + access agreement). The community mirror `enfantdupeuple` ships a public
# Q4_K_M GGUF of the same weights — same model, third-party quantization, no auth.
# Quality verified by bench_llama_finance.py post-install.

LOF_NAME="llama-open-finance"
WORK_DIR="$HEDGEFUND_HOME/ollama-build/llama-open-finance"
HF_MIRROR="enfantdupeuple/Llama-Open-Finance-8B-Q4_K_M-GGUF"

if sudo -u hedgefund -H ollama list 2>/dev/null | grep -qw "$LOF_NAME"; then
    ok "Llama-Open-Finance already in Ollama. Skipping download."
else
    log "Downloading pre-quantized Llama-Open-Finance Q4_K_M GGUF (~5GB, ~5-15 min)."

    sudo -u hedgefund mkdir -p "$WORK_DIR"
    sudo chown -R hedgefund:staff "$HEDGEFUND_HOME/ollama-build"

    # 3a. Download GGUF via snapshot_download (public repo, no auth needed).
    # Writes the *.gguf file directly into $WORK_DIR.
    log "[1/2] Downloading $HF_MIRROR ..."
    HF_SCRIPT=$(mktemp /tmp/hf_dl_XXXXXX.py)
    cat > "$HF_SCRIPT" <<PYEOF
from huggingface_hub import snapshot_download
import glob, os, sys

local = snapshot_download(
    repo_id="$HF_MIRROR",
    local_dir="$WORK_DIR",
    allow_patterns=["*.gguf"],  # skip README/config — we only want the weights
)
ggufs = sorted(glob.glob(os.path.join(local, "*.gguf")))
if not ggufs:
    sys.exit("ERROR: no .gguf file found in downloaded repo")
# Print the path to stdout so the shell can capture it
print(ggufs[0])
PYEOF
    chmod 644 "$HF_SCRIPT"

    GGUF_PATH=$(sudo -u hedgefund -H bash -lc "
        set -e
        cd \"\$HOME\"
        python3 -m pip install --quiet --user 'huggingface_hub>=0.20'
        python3 '$HF_SCRIPT'
    " | tail -1) || { rm -f "$HF_SCRIPT"; fail "GGUF download failed. Check internet / HF availability / disk space."; }
    rm -f "$HF_SCRIPT"

    if [[ -z "$GGUF_PATH" || ! -f "$GGUF_PATH" ]]; then
        fail "GGUF download claimed success but no file at: $GGUF_PATH"
    fi
    ok "GGUF downloaded → $GGUF_PATH"

    # 3b. ollama create from Modelfile (points at the downloaded GGUF)
    log "[2/2] Creating Ollama model '$LOF_NAME' ..."
    sudo cp "$JARVIS_ROOT/hermes-config/Modelfile.llama-finance" "$WORK_DIR/Modelfile"
    sudo sed -i '' "s|__GGUF_PATH__|$GGUF_PATH|" "$WORK_DIR/Modelfile"
    sudo chown hedgefund:staff "$WORK_DIR/Modelfile"

    sudo -u hedgefund -H bash -lc "
        cd '$WORK_DIR'
        ollama create $LOF_NAME -f ./Modelfile
    " || fail "ollama create failed."
    ok "Ollama model '$LOF_NAME' registered."
fi

# --- 5. Smoke test ---
log "Smoke-testing Llama-Open-Finance..."
SMOKE=$(sudo -u hedgefund -H bash -lc "
    cd \"\$HOME\"
    echo 'Reliance Industries reported Q4 EPS of 28.5, beating consensus 25.1.' | \
        ollama run $LOF_NAME --format json 2>&1 | head -20
" 2>&1 || true)
if [[ -n "$SMOKE" ]]; then
    ok "Smoke output (first lines):"
    note "$SMOKE" | head -10
else
    warn "Smoke test produced no output. Model is registered but may need debug."
fi

ok "Step 3 complete."
