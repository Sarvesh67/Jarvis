"""Write the deterministic supply-chain backbone into the msme FalkorDB graph.

    msme/.venv/bin/python -m msme.graph [--wipe] [--limit N]

Reads out/firms.parquet and writes the COMPACT, factual backbone — no LLM, no cognify (the
`_write_signal_node` pattern, dashboard/app.py). Same FalkorDB graph Cognee uses (`msme_graph`),
isolated labels so it never contaminates doc retrieval:

    (:Enterprise {udyam,name,pincode,district,state,gst_status})
    (:Enterprise)-[:CLASSIFIED_AS]->(:NICClass {code,label})
    (:Enterprise)-[:LOCATED_IN]->(:District {name,state})
    (:NICClass)-[:PRODUCES|CONSUMES]->(:Material {slug,kind})

Supply relationships (SELLS_TO/BUYS_FROM/COMPETES_WITH) are NOT stored — in one dense cluster they
would explode into cliques. They are query-time traversals over PRODUCES/CONSUMES (see search.py).
"""
from __future__ import annotations

import argparse
import json
import pathlib

import pandas as pd
from falkordb import FalkorDB

from . import nic as N

BASE = pathlib.Path(__file__).resolve().parent
PARQUET = BASE / "out" / "firms.parquet"
GRAPH = "msme_graph"
BATCH = 1000
BACKBONE_LABELS = ["Enterprise", "NICClass", "Material", "District"]


def _connect():
    return FalkorDB(host="127.0.0.1", port=6379).select_graph(GRAPH)


def _wipe(g):
    """Remove only the deterministic backbone labels — leaves Cognee's doc nodes untouched."""
    for lbl in BACKBONE_LABELS:
        g.query(f"MATCH (n:{lbl}) DETACH DELETE n")
    print(f"wiped backbone labels: {', '.join(BACKBONE_LABELS)}")


def _ensure_indexes(g):
    """CRITICAL at scale: every MERGE below matches on these keys. Without indexes, MERGE scans the
    whole label set → O(n²) and hangs at 300k+ firms. Create-if-absent (FalkorDB errors if exists)."""
    for lbl, prop in [("Enterprise", "udyam"), ("NICClass", "code"),
                      ("Material", "slug"), ("District", "name")]:
        try:
            g.query(f"CREATE INDEX FOR (n:{lbl}) ON (n.{prop})")
        except Exception:  # noqa: BLE001 — already exists
            pass
    print("indexes ensured: Enterprise.udyam, NICClass.code, Material.slug, District.name")


def _write_nic_classes(g, codes: dict, nic_map, vocab, cache):
    """One (:NICClass)-[:PRODUCES|CONSUMES]->(:Material) sub-graph per distinct code (deduped across
    all firms). Materials come from the cached per-code enrichment — pure lookups here, no LLM."""
    rows = []
    for code, desc in codes.items():
        rec = N.enrich_nic(code, desc, nic_map, vocab, cache, http=None)  # cache/curated only
        rows.append({"code": code, "label": rec["label"],
                     "produces": rec["outputs"], "consumes": rec["inputs"]})
    mat = vocab.materials
    g.query(
        "UNWIND $rows AS r "
        "MERGE (c:NICClass {code:r.code}) SET c.label = r.label, c.id = 'nic:' + r.code "
        "WITH c, r "
        "FOREACH (s IN r.produces | "
        "  MERGE (m:Material {slug:s}) SET m.id = 'mat:' + s "
        "  MERGE (c)-[p:PRODUCES]->(m) "
        "  SET p.source_node_id = c.id, p.target_node_id = m.id, p.relationship_name = 'produces') "
        "FOREACH (s IN r.consumes | "
        "  MERGE (m:Material {slug:s}) SET m.id = 'mat:' + s "
        "  MERGE (c)-[x:CONSUMES]->(m) "
        "  SET x.source_node_id = c.id, x.target_node_id = m.id, x.relationship_name = 'consumes')",
        params={"rows": rows},
    )
    # stamp Material label/kind (nice-to-have for the browser + product navigation)
    g.query(
        "UNWIND $mats AS m MERGE (x:Material {slug:m.slug}) "
        "SET x.label=m.label, x.kind=m.kind, x.id = 'mat:' + m.slug",
        params={"mats": [{"slug": s, "label": v.get("label", s), "kind": v.get("kind", "")}
                         for s, v in mat.items()]},
    )
    print(f"wrote {len(rows)} NICClass nodes + material edges")


def _write_enterprises(g, firms: list[dict]):
    """Batched UNWIND: Enterprise + CLASSIFIED_AS + LOCATED_IN. The per-firm mass of the graph."""
    for i in range(0, len(firms), BATCH):
        chunk = firms[i:i + BATCH]
        # NOTE the `id` / `source_node_id` / `target_node_id` / `relationship_name` properties:
        # Cognee's FalkorDB adapter owns no namespace — its get_graph_data() does a bare
        # `MATCH (n)` over the WHOLE graph and reads record["id"] / edge["source_node_id"].
        # Because our deterministic backbone shares msme_graph with Cognee's semantic layer,
        # backbone nodes missing those props made that call raise KeyError('id') — which silently
        # broke `index_graph_edges`, left the EdgeType vector index empty, and made every graphRAG
        # query return "No relevant knowledge". Stamping them keeps both layers co-resident.
        g.query(
            "UNWIND $rows AS r "
            "MERGE (e:Enterprise {udyam:r.udyam}) "
            "SET e.name=r.name, e.pincode=r.pincode, e.district=r.district, "
            "    e.state=r.state, e.gst_status=r.gst_status, e.id='ent:' + r.udyam "
            "MERGE (d:District {name:r.district, state:r.state}) "
            "SET d.id = 'dist:' + r.state + ':' + r.district "
            "MERGE (e)-[l:LOCATED_IN]->(d) "
            "SET l.source_node_id=e.id, l.target_node_id=d.id, l.relationship_name='located_in' "
            "FOREACH (code IN r.nic_codes | "
            "  MERGE (c:NICClass {code:code}) SET c.id = 'nic:' + code "
            "  MERGE (e)-[cl:CLASSIFIED_AS]->(c) "
            "  SET cl.source_node_id=e.id, cl.target_node_id=c.id, "
            "      cl.relationship_name='classified_as')",
            params={"rows": chunk},
        )
        print(f"  enterprises {min(i + BATCH, len(firms))}/{len(firms)}")


def _load_firms(limit):
    df = pd.read_parquet(PARQUET)
    if limit:
        df = df.head(limit)
    firms = []
    for r in df.to_dict("records"):
        firms.append({
            "udyam": r["udyam"], "name": r["name"], "pincode": str(r["pincode"]),
            "district": r["district"], "state": r["state"], "gst_status": r["gst_status"],
            "nic_codes": json.loads(r["nic_codes"]),
        })
    return firms


def run(wipe: bool, limit):
    if not PARQUET.exists():
        raise SystemExit(f"{PARQUET} missing — run `python -m msme.preprocess --csv … ` first")
    firms = _load_firms(limit)
    codes = {}
    for f in firms:
        for c in f["nic_codes"]:
            codes.setdefault(c, "")
    nic_map = N._materialize("nic_map.json")
    vocab = N.Vocabulary()
    cache = N._load_enrich_cache()

    g = _connect()
    if wipe:
        _wipe(g)
    _ensure_indexes(g)
    print(f"writing backbone for {len(firms)} firms, {len(codes)} NIC codes → {GRAPH}")
    _write_nic_classes(g, codes, nic_map, vocab, cache)
    _write_enterprises(g, firms)

    n = g.query("MATCH (n) WHERE n:Enterprise OR n:NICClass OR n:Material OR n:District "
                "RETURN count(n) AS n").result_set[0][0]
    print(f"done — {n} backbone nodes in {GRAPH}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wipe", action="store_true", help="clear backbone labels before writing")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    run(args.wipe, args.limit)


if __name__ == "__main__":
    main()
