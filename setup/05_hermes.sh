#!/usr/bin/env bash
# Install Hermes (common agent), point its brain at the gateway, and build per-project profiles.
# Profiles: jarvis (generic), hedgefund, msme — each billed to its own LiteLLM key.
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
ensure_path
require_platform_env

# --- install Hermes ---
if have hermes; then ok "Hermes present ($(hermes --version 2>/dev/null | head -1))."; else
  log "Installing Hermes Agent..."
  curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash < /dev/null
  ensure_path
fi
have hermes || fail "hermes not on PATH after install (expected ~/.local/bin/hermes)."

# Patch a config.yaml model: block to use the gateway with a given key.
patch_brain() {  # $1 = config.yaml path, $2 = api key
python3 - "$1" "$2" <<'PY'
import sys, re
path, key = sys.argv[1], sys.argv[2]
want = {
    "default": '"reasoner"',
    "provider": '"custom"',
    "base_url": '"http://127.0.0.1:4000/v1"',
    "context_length": "200000",
    "api_key": f'"{key}"',
}
lines = open(path).read().splitlines()
out, in_model, seen = [], False, set()
for ln in lines:
    stripped = ln.strip()
    # enter/exit the top-level model: block
    if re.match(r"^model:\s*$", ln):
        in_model = True; out.append(ln); continue
    if in_model and re.match(r"^\S", ln):   # next top-level key -> insert any missing
        for k, v in want.items():
            if k not in seen:
                out.append(f"  {k}: {v}")
        in_model = False
    if in_model and not stripped.startswith("#"):
        m = re.match(r"^(\s+)(\w+):", ln)
        if m and m.group(2) in want:
            k = m.group(2); out.append(f"  {k}: {want[k]}"); seen.add(k); continue
    out.append(ln)
open(path, "w").write("\n".join(out) + "\n")
PY
chmod 600 "$1"
}

# --- default profile brain -> jarvis (generic) key ---
patch_brain "$HOME/.hermes/config.yaml" "$(env_get JARVIS_LLM_KEY)"
ok "Default brain -> gateway (jarvis key)."

# --- per-project profiles ---
make_profile() {  # $1 = profile name, $2 = api key
  local name="$1" key="$2"
  [ -d "$HOME/.hermes/profiles/$name" ] || { log "Creating profile $name..."; hermes profile create "$name" >/dev/null 2>&1 || true; }
  cp "$HOME/.hermes/config.yaml" "$HOME/.hermes/profiles/$name/config.yaml"
  patch_brain "$HOME/.hermes/profiles/$name/config.yaml" "$key"
  cp -R "$HOME/.hermes/skills/jarvis-knowledge" "$HOME/.hermes/profiles/$name/skills/jarvis-knowledge" 2>/dev/null || true
  ok "Profile $name -> $name budget key (+ jarvis-knowledge skill)."
}

# Ensure the knowledge skill exists in the base install first.
[ -d "$HOME/.hermes/skills/jarvis-knowledge" ] || warn "jarvis-knowledge skill missing in ~/.hermes/skills (see Jarvis/hermes/README.md)."

make_profile jarvis    "$(env_get JARVIS_LLM_KEY)"
make_profile hedgefund "$(env_get HEDGEFUND_LLM_KEY)"
make_profile msme      "$(env_get MSME_LLM_KEY)"
make_profile cosmetics "$(env_get COSMETICS_LLM_KEY)"

# --- project-specific skills (installed from Jarvis/hermes-config/skills) ---
# jarvis-signals -> hedgefund; jarvis-vendor-search -> msme. Base copy + the owning profile.
install_skill() {  # $1 = skill name, $2 = owning profile
  local src="$JARVIS_ROOT/hermes-config/skills/$1"
  [ -d "$src" ] || { warn "skill $1 missing in hermes-config/skills"; return; }
  cp -R "$src" "$HOME/.hermes/skills/$1"
  mkdir -p "$HOME/.hermes/profiles/$2/skills"
  cp -R "$src" "$HOME/.hermes/profiles/$2/skills/$1"
  ok "Installed skill $1 -> $2 profile."
}
install_skill jarvis-signals       hedgefund
# Domain skills are installed into their OWN profile only. jarvis-vendor-search is the
# Surat TEXTILE graph and jarvis-cosmetics-search is the India COSMETICS graph; a profile
# holding both invites the agent to answer a cosmetics question with the textile tool,
# which is exactly what happened before the cosmetics skill existed.
install_skill jarvis-vendor-search    msme
install_skill jarvis-cosmetics-search cosmetics

log "Smoke test (jarvis brain)..."
jarvis chat -q "Reply with exactly: HERMES_OK" --yolo 2>&1 | grep -q "HERMES_OK" && ok "Hermes responding via gateway." || warn "No HERMES_OK — check gateway + keys."
ok "Hermes ready. Generic: 'jarvis --tui'  ·  projects: 'hedgefund --tui' / 'msme --tui'."
