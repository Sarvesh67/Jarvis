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
# SYMLINK, never copy. Two reasons, both learned the hard way:
#
#   1. `cp -R src dst` puts src INSIDE dst when dst already exists, so every re-run of
#      this script nested another copy — `<skill>/<skill>/SKILL.md`. Hermes loads the
#      OUTER one, so the nested copy is invisible, and which of the two is newer depends
#      on the order the runs happened in. The cosmetics profile ended up loading a v1
#      skill with a v2 sitting one level below it.
#   2. A copy drifts. hermes-config/ is maintained and reviewed; a profile copy is a
#      snapshot that silently stops matching it the next time the repo copy changes.
#
# A symlink has neither failure. Editing the repo file changes what every profile loads,
# and there is one history.
install_skill() {  # $1 = skill name, $2 = owning profile
  local src="$JARVIS_ROOT/hermes-config/skills/$1"
  [ -d "$src" ] || { warn "skill $1 missing in hermes-config/skills"; return; }
  local dst
  for dst in "$HOME/.hermes/skills/$1" "$HOME/.hermes/profiles/$2/skills/$1"; do
    mkdir -p "$(dirname "$dst")"
    if [ "$(readlink "$dst" 2>/dev/null)" = "$src" ]; then continue; fi
    if [ -e "$dst" ] && [ ! -L "$dst" ]; then
      mv "$dst" "$dst.pre-symlink-$(date +%Y%m%d%H%M%S)"
      warn "archived an unversioned $1 that was in $(dirname "$dst")"
    fi
    rm -f "$dst"
    ln -s "$src" "$dst"
  done
  ok "Linked skill $1 -> $2 profile."
}
install_skill jarvis-signals       hedgefund
# Domain skills are installed into their OWN profile only. jarvis-vendor-search is the
# Surat TEXTILE graph and jarvis-cosmetics-search is the India COSMETICS graph; a profile
# holding both invites the agent to answer a cosmetics question with the textile tool,
# which is exactly what happened before the cosmetics skill existed.
install_skill jarvis-vendor-search    msme
install_skill jarvis-cosmetics-search cosmetics
# Shared retrieval over each project's Cognee graph. Linked into the profiles that held a
# byte-identical copy of it.
#
# `msme` is POINTEDLY ABSENT. Its copy carries ~1.7 KB the others do not — multi-angle
# parallel querying, a Surat-cluster reference, conversation-level caching — written by
# hand on the graph that needed it. Linking msme here would delete that in favour of the
# leaner file, which is a decision about whether the technique generalises, not a tidy-up.
# Until someone makes it, msme keeps its own copy. See hermes-config/skills/jarvis-knowledge/README.md.
for _p in hedgefund cosmetics jarvis; do
  install_skill jarvis-knowledge "$_p"
done

log "Smoke test (jarvis brain)..."
jarvis chat -q "Reply with exactly: HERMES_OK" --yolo 2>&1 | grep -q "HERMES_OK" && ok "Hermes responding via gateway." || warn "No HERMES_OK — check gateway + keys."
ok "Hermes ready. Generic: 'jarvis --tui'  ·  projects: 'hedgefund --tui' / 'msme --tui'."
