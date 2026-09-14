#!/usr/bin/env bash
# One-command MSME prototype pipeline: NIC master -> enrichment -> firms -> FalkorDB backbone ->
# (optional) Cognee slice -> summary. Fail-fast, idempotent, self-locating.
#
#   bash msme/run.sh --csv /path/to/udyam.csv
#     [--district surat] [--promote "embroidered dress material"] [--top N] [--no-llm] [--no-cognee]
#
# Prompts for --csv if omitted. Enrichment + Cognee need the LiteLLM gateway (:4000) + MSME_LLM_KEY;
# without them the run continues on curated codes / skips Cognee. FalkorDB (:6379) is always required.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # -> Jarvis repo root
cd "$HERE"
PY="msme/.venv/bin/python"
NIC_PDF_URL="https://www.ncs.gov.in/Documents/NIC_Sector.pdf"
NIC_PDF="msme/data/NIC_Sector.pdf"

CSV=""; DISTRICT="surat"; PROMOTE=""; TOP=20; NO_LLM=0; NO_COGNEE=0; FORCE=0; REENRICH=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --csv) CSV="${2:-}"; shift 2;;
    --district) DISTRICT="${2:-}"; shift 2;;
    --promote) PROMOTE="${2:-}"; shift 2;;
    --top) TOP="${2:-}"; shift 2;;
    --no-llm) NO_LLM=1; shift;;
    --no-cognee) NO_COGNEE=1; shift;;
    --force) FORCE=1; shift;;         # rebuild every stage even if outputs look up to date
    --reenrich) REENRICH=1; shift;;   # clear the enrichment cache (re-run LLM; captures raw materials)
    -h|--help)
      echo "usage: bash msme/run.sh --csv PATH [--district surat] [--promote QUERY] [--top N]"
      echo "                        [--no-llm] [--no-cognee] [--force] [--reenrich]"
      exit 0;;
    *) echo "unknown arg: $1"; exit 1;;
  esac
done

say() { printf "\n\033[1;36m== %s ==\033[0m\n" "$1"; }
warn() { printf "\033[1;33m   %s\033[0m\n" "$1"; }
skip() { printf "\033[1;32m   ✓ skip — %s\033[0m\n" "$1"; }
# up_to_date TARGET DEP... : true if TARGET exists and is newer than every existing DEP.
up_to_date() { local t="$1"; shift; [[ -f "$t" ]] || return 1
  local d; for d in "$@"; do [[ -f "$d" && "$d" -nt "$t" ]] && return 1; done; return 0; }

[[ -n "$CSV" ]] || read -rp "Path to Udyam CSV: " CSV
[[ -f "$CSV" ]] || { echo "CSV not found: $CSV"; exit 1; }

# --- stage 1: env ---
say "1/7 env"
[[ -x "$PY" ]] || bash setup/07_msme.sh
[[ -f platform/.env ]] && export MSME_LLM_KEY="$(grep -m1 '^MSME_LLM_KEY=' platform/.env | cut -d= -f2- || true)"

# --- prereq: FalkorDB (hard) ---
"$PY" - <<'PYCHK' || { echo "FalkorDB not reachable on :6379 — start the platform: docker compose -f platform/docker-compose.yml up -d"; exit 1; }
from falkordb import FalkorDB
FalkorDB(host="127.0.0.1", port=6379).select_graph("msme_graph").query("RETURN 1")
PYCHK

LLM_OK=1
{ [[ "$NO_LLM" == "1" ]] || [[ -z "${MSME_LLM_KEY:-}" ]]; } && LLM_OK=0

[[ "$REENRICH" == "1" ]] && { rm -f msme/cache/nic_enrich.json; warn "cleared enrichment cache (--reenrich): codes will re-run the LLM and capture raw materials"; }

# --- stage 2: NIC master (deterministic; skip if already parsed) ---
say "2/7 NIC master"
if [[ ! -f "$NIC_PDF" ]]; then
  mkdir -p msme/data
  curl -fsSL -o "$NIC_PDF" "$NIC_PDF_URL" || warn "NIC PDF download failed — using curated seeds only"
fi
if [[ "$FORCE" == "0" && -f msme/data/nic2008.csv ]]; then
  skip "data/nic2008.csv exists (--force to re-parse)"
elif [[ -f "$NIC_PDF" ]]; then
  "$PY" -m msme.nic_master --file "$NIC_PDF"
else
  warn "NIC parse skipped (no PDF)"
fi

# firms.parquet is up to date iff newer than every input that affects it: the CSV, the data files
# (vocab/NIC map/enrichment cache), AND the CODE that computes it (nic.py/preprocess.py) — a logic
# fix (e.g. a matcher bug) must also force a rebuild, or the skip-guard would silently serve stale,
# bug-affected output forever since none of the DATA files changed.
NEED_PP=1
if [[ "$FORCE" == "0" && "$REENRICH" == "0" ]] && up_to_date msme/out/firms.parquet "$CSV" \
     msme/seeds/materials.json msme/cache/nic_map.json msme/cache/nic_enrich.json \
     msme/nic.py msme/preprocess.py; then
  NEED_PP=0
fi

# --- stage 3: material enrichment (LLM, cached) ---
say "3/7 material enrichment (DeepSeek, cached)"
if [[ "$NEED_PP" == "0" ]]; then
  skip "firms.parquet up to date — inputs unchanged"
elif [[ "$LLM_OK" == "1" ]]; then
  "$PY" -m msme.preprocess --csv "$CSV" --enrich-only || warn "enrichment had issues (check gateway :4000)"
else
  warn "skipped (--no-llm or no MSME_LLM_KEY) — only curated NIC codes get materials"
fi

# --- stage 4: firm preprocess ---
say "4/7 firm preprocess"
if [[ "$NEED_PP" == "0" ]]; then
  skip "firms.parquet up to date (--force to rebuild)"
else
  PP=(--connected-only --emit-docs)
  [[ "$LLM_OK" == "1" ]] || PP+=(--no-llm)
  "$PY" -m msme.preprocess --csv "$CSV" "${PP[@]}"
fi

# --- stage 5: deterministic backbone -> FalkorDB (skip if graph already loaded from this parquet) ---
say "5/7 graph backbone -> FalkorDB msme_graph"
MARK=msme/out/.graph_loaded
if [[ "$FORCE" == "0" ]] && up_to_date "$MARK" msme/out/firms.parquet msme/graph.py; then
  skip "graph already loaded from current firms.parquet (--force to reload)"
else
  "$PY" -m msme.graph --wipe && touch "$MARK"
fi

# --- stage 6: Cognee semantic slice (optional) ---
say "6/7 Cognee semantic slice"
if [[ "$NO_COGNEE" == "1" || -z "$PROMOTE" || "$LLM_OK" == "0" ]]; then
  warn "skipped (--no-cognee / no --promote / no LLM)"
else
  "$PY" -m msme.promote --from-search "$PROMOTE" --role buyer --top "$TOP" || warn "cognify had issues"
fi

# --- stage 7: summary + sample search ---
say "7/7 summary"
"$PY" - <<'PYSUM'
from msme.search import _falkor
g = _falkor()
for l in ["Enterprise", "NICClass", "Material", "District"]:
    print(f"   :{l} = {g.query(f'MATCH (n:{l}) RETURN count(n)').result_set[0][0]}")
PYSUM
echo "   sample search (suppliers of cotton yarn in $DISTRICT):"
"$PY" -m msme.search --graph --need "cotton yarn" --role supplier --district "$DISTRICT" --top 5 || true
say "done — browse the graph at http://127.0.0.1:3000  ·  ask the msme chatbox for vendors"
