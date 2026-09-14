"""Promote a SHORTLIST of firm profiles into Cognee — the semantic (graphRAG) layer.

    # a connected slice across the textile chain (the demo default):
    msme/.venv/bin/python -m msme.promote --chain --per-code 55
    # explicit codes / counts:
    msme/.venv/bin/python -m msme.promote --nic-codes "13991:80,13111:40,14101:60"
    # or by search / by id:
    msme/.venv/bin/python -m msme.promote --from-search "embroidered dress material" --top 50
    msme/.venv/bin/python -m msme.promote --udyam 395010-foo-01_01_2024

WHY THIS EXISTS (the two-layer split):
  - The DETERMINISTIC backbone (msme/graph.py) already holds ALL 326k firms as
    (:Enterprise)-[:CLASSIFIED_AS]->(:NICClass)-[:PRODUCES|CONSUMES]->(:Material). It answers
    structured questions ("who supplies cotton yarn in Surat") by traversal — no LLM, no embeddings.
  - Cognee's retriever works by EMBEDDING nodes and vector-searching them. Our backbone nodes were
    written with raw Cypher, so they have no embeddings and are invisible to it. Cognifying all 326k
    firms would be needlessly expensive, so we promote a bounded, *connected* slice: those firms'
    prose profiles get embedded + entity-extracted, which is what makes fuzzy natural-language
    questions ("what do you know about the embroidery cluster near Katargam?") work.

Selection favours firms in the DENSEST pincodes per NIC code, so the promoted slice interconnects
(a random 500 across Surat would be mutually unrelated and make a sparse, unconvincing subgraph).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess

import duckdb

BASE = pathlib.Path(__file__).resolve().parent
PARQUET = BASE / "out" / "firms.parquet"
DOCS = BASE / "out" / "documents.jsonl"
COGNEE = BASE.parent / "cognee"
COGNEE_PY = COGNEE / ".venv" / "bin" / "python"

# The Surat textile value chain, in flow order. Promoting across ALL of these (rather than one
# code) is what gives the semantic layer a story: fibre -> yarn -> fabric -> dyeing -> embroidery ->
# garment -> wholesale/retail.
CHAIN_CODES = [
    "13111",  # cotton spinning
    "13114",  # texturising / filament yarn
    "13121",  # cotton weaving
    "13131",  # dyeing / printing / finishing
    "13911",  # knitted fabrics
    "13991",  # embroidery, laces, fringes
    "13999",  # other textile products n.e.c.
    "14101",  # readymade garments
    "14109",  # wearing apparel n.e.c.
    "46411",  # wholesale of textiles
    "47711",  # retail of textiles/garments
]


def _load_docs() -> dict:
    if not DOCS.exists():
        raise SystemExit(f"{DOCS} missing — run preprocess with --emit-docs first")
    out = {}
    with DOCS.open() as fh:
        for line in fh:
            d = json.loads(line)
            out[d["udyam"]] = d
    return out


def select_by_nic(spec: dict[str, int]) -> list[str]:
    """udyam ids for each NIC code, preferring the code's densest pincodes so the slice connects."""
    if not PARQUET.exists():
        raise SystemExit(f"{PARQUET} missing — run the pipeline first")
    con = duckdb.connect()
    picked: list[str] = []
    for code, n in spec.items():
        rows = con.execute(
            f"""
            WITH hits AS (
              SELECT udyam, pincode FROM read_parquet('{PARQUET}')
              WHERE nic_codes LIKE '%"{code}"%'
            ),
            dense AS (
              SELECT pincode, count(*) AS c FROM hits GROUP BY pincode
            )
            SELECT h.udyam FROM hits h JOIN dense d USING (pincode)
            ORDER BY d.c DESC, h.udyam
            LIMIT {int(n)}
            """
        ).fetchall()
        got = [r[0] for r in rows]
        picked.extend(got)
        print(f"  {code}: selected {len(got)}")
    # dedupe, order-stable (a firm with two chain codes shouldn't be promoted twice)
    seen, out = set(), []
    for u in picked:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _run_batch(docs: list[dict], model_role: str) -> bool:
    """One job file, one ingest.py run: N× add(per-item node_set) + a single incremental cognify."""
    job = {
        "items": [{"text": d["text"], "tags": d["tags"]} for d in docs],
        "model": model_role,
    }
    jobfile = COGNEE / "data" / "msme" / "uploads" / "promote_batch.json"
    jobfile.parent.mkdir(parents=True, exist_ok=True)
    jobfile.write_text(json.dumps(job))
    print(f"cognifying {len(docs)} firm profiles (this can take 20-60 min)…")
    proc = subprocess.Popen(
        [str(COGNEE_PY), "ingest.py", "--project", "msme", "--job", str(jobfile)],
        cwd=str(COGNEE), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    ok = False
    for line in proc.stdout:  # stream so a long cognify shows progress
        line = line.rstrip()
        if line and not line[:4].isdigit():  # drop cognee's ISO-timestamped structlog noise
            print("  " + line, flush=True)
        if "INGEST_DONE" in line:
            ok = True
    proc.wait()
    return ok and proc.returncode == 0


def run(udyams, from_search, role, top, nic_spec, model_role):
    docs = _load_docs()
    if nic_spec:
        udyams = select_by_nic(nic_spec)
    elif from_search:
        from . import search as S
        udyams = [h["udyam"] for h in S.search(from_search, role, top=top)]
    if not udyams:
        raise SystemExit("no firms selected (use --chain, --nic-codes, --from-search or --udyam)")

    selected = [docs[u] for u in udyams if u in docs]
    missing = len(udyams) - len(selected)
    print(f"promoting {len(selected)} firm profiles into the msme Cognee dataset"
          + (f" ({missing} selected ids had no document)" if missing else ""))
    if not selected:
        raise SystemExit("none of the selected ids had documents — re-run preprocess --emit-docs")
    print("done" if _run_batch(selected, model_role) else "FAILED — see log above")


def _parse_nic_spec(s: str) -> dict[str, int]:
    spec = {}
    for part in (s or "").split(","):
        part = part.strip()
        if not part:
            continue
        code, _, n = part.partition(":")
        spec[code.strip()] = int(n or 50)
    return spec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--udyam", nargs="*", default=[], help="explicit udyam ids")
    ap.add_argument("--from-search", default="", help="promote the top-K hits of this need")
    ap.add_argument("--role", choices=["supplier", "buyer"], default="supplier")
    ap.add_argument("--top", type=int, default=50)
    ap.add_argument("--nic-codes", default="", help='e.g. "13991:80,13111:40"')
    ap.add_argument("--chain", action="store_true",
                    help="promote across the whole Surat textile chain (see CHAIN_CODES)")
    ap.add_argument("--per-code", type=int, default=45, help="with --chain: firms per NIC code")
    ap.add_argument("--model", default="extractor",
                    help="role handle for cognify (default extractor = DeepSeek flash)")
    args = ap.parse_args()

    spec = {}
    if args.chain:
        spec = {c: args.per_code for c in CHAIN_CODES}
    elif args.nic_codes:
        spec = _parse_nic_spec(args.nic_codes)
    run(args.udyam, args.from_search, args.role, args.top, spec, args.model)


if __name__ == "__main__":
    main()
