"""Stage 6 — derived edges.  Spec §13.

Every edge written here carries derived:true, rule_id, inputs and TIER 4. Tier 4 ranks
and suggests; it never answers a licence, compliance or restriction question (hard rule 3).

What is deliberately NOT materialised, and why:

  IN_CLUSTER_WITH — every pair of companies in one cluster is a clique. With 2.5k
    companies in four clusters that is millions of edges carrying no information the
    Cluster node does not already carry. It is a two-hop traversal
    (:Company)-[:LOCATED_IN]->(:Cluster)<-[:LOCATED_IN]-(:Company) at query time.
    (The sibling msme/ subsystem learned this the hard way on one dense Surat cluster.)

  CAN_FILL — requires facility fill volumes and packaging component types, which only
    S14 (company websites) can supply. No inputs yet, so no edge; an empty rule is
    honest, a guessed one is not.

  LIKELY_MANUFACTURES_FOR — requires trade data or label co-occurrence (S13/S12).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib  # noqa: E402
from pipeline.load_neo4j import pv, run, q  # noqa: E402

# NIC-2008 subclass -> plausible ProductCategory spine codes. Tier 4: this says
# "a firm registered under this activity plausibly serves these categories", which is a
# ranking signal, NOT a statement that it is licensed to make them.
NIC_TO_CATEGORY = {
    "20231": ["SOAP"],
    "20234": ["FRAGRANCE"],
    "20235": ["ORAL_CARE"],
    "20236": ["HAIR_CARE", "HAIR_COLOUR"],
    "20237": ["SKIN_CARE", "MAKEUP"],
    "20239": ["FRAGRANCE", "SKIN_CARE"],
}
SUBSTITUTE_TOP_K = 10


def serves_category_from_licence(s) -> None:
    """The tier-1 path: Facility -> Licence -> ProductCategory collapsed to one hop."""
    res = s.run("""
        MATCH (f:Facility)-[:LICENSED_UNDER]->(l:Licence)-[:PERMITS]->(p:ProductCategory)
        MERGE (f)-[e:SERVES_CATEGORY]->(p)
        SET e.derived = true, e.rule_id = 'serves_category_from_licence',
            e._tier = 1, e._confidence = 1.0, e._source_id = 'stage6_derive',
            e.basis = 'licence'
        RETURN count(e) AS n
    """).single()
    print(f"    SERVES_CATEGORY (from licence, tier 1): {res['n']}")


def serves_category_from_nic(s) -> None:
    """The tier-4 path: what the firm's registered activity plausibly covers."""
    rows = []
    for r in q("""SELECT x.entity_id, r.payload FROM raw_company r
                  JOIN crosswalk x ON x.source_id=r.source_id AND x.source_key=r.source_key
                   AND x.entity_type='company' AND x.superseded_by IS NULL
                  WHERE r.source_id IN ('s17_udyam','s03_mca')"""):
        p = r["payload"] or {}
        codes = p.get("nic_codes_cosmetics") or []
        nic_cin = p.get("nic_from_cin")
        if nic_cin:
            codes = codes + [nic_cin]
        cats = sorted({c for code in codes for c in NIC_TO_CATEGORY.get(code, [])})
        for cat in cats:
            rows.append({"company_id": r["entity_id"], "code": cat,
                         "prov": pv("stage6_derive", 4, 0.5, derived=True,
                                    rule_id="serves_category_from_nic",
                                    basis="nic_activity", inputs=codes)})
    run(s, """
        UNWIND $rows AS row
        MATCH (c:Company {company_id: row.company_id})
        MATCH (p:ProductCategory {code: row.code})
        MERGE (c)-[e:SERVES_CATEGORY]->(p)
        SET e += row.prov, e.derived = true
    """, rows, "SERVES_CATEGORY (from NIC, tier 4)")


def company_located_in(s) -> None:
    """Company -> Cluster from its own address.

    The basis differs by source and is stamped on the edge: an MCA registered office is
    NOT a factory, an Udyam address is the enterprise's own premises. Facility ->
    Cluster remains the authoritative geography; this exists so a company with no
    resolved facility is still findable by cluster.
    """
    basis = {"s03_mca": "registered_office", "s17_udyam": "udyam_premises",
             "s01_cdsco": "licence_premises", "s08_exhibitors": "listing"}
    rows = []
    for r in q("""SELECT DISTINCT x.entity_id, r.cluster_id, r.source_id FROM raw_company r
                  JOIN crosswalk x ON x.source_id=r.source_id AND x.source_key=r.source_key
                   AND x.entity_type='company' AND x.superseded_by IS NULL
                  WHERE r.cluster_id IS NOT NULL"""):
        b = basis.get(r["source_id"], "address")
        rows.append({"company_id": r["entity_id"], "cluster_id": r["cluster_id"],
                     "prov": pv("stage2_normalize", 1 if b != "registered_office" else 4,
                                1.0, basis=b)})
    run(s, """
        UNWIND $rows AS row
        MATCH (c:Company {company_id: row.company_id})
        MATCH (cl:Cluster {cluster_id: row.cluster_id})
        MERGE (c)-[e:LOCATED_IN]->(cl)
        SET e += row.prov
    """, rows, "Company LOCATED_IN")


def substitute_for(s) -> None:
    """Bounded SUBSTITUTE_FOR.

    The interesting question is always "this ingredient is restricted — what else does
    the same job?", so the edge is emitted only FROM restricted/prohibited ingredients,
    to at most K unrestricted ingredients sharing a declared function.

    Ranking is done in Python over a function -> ingredient inverted index rather than in
    Cypher: the pure-Cypher form is a cross join of ~1.7k restricted ingredients against
    ~36k candidates and the server gives up on it. The inverted index makes the same
    computation linear in the size of the functions actually involved.
    """
    restricted = {r["inci"]: r["functions"] for r in s.run("""
        MATCH (i:Ingredient)-[:RESTRICTED_BY]->(r:RegulatoryRule)
        WHERE r.status IN ['prohibited','restricted'] AND size(coalesce(i.functions,[])) > 0
        RETURN DISTINCT i.inci_name AS inci, i.functions AS functions
    """)}
    prohibited = {r["inci"] for r in s.run("""
        MATCH (i:Ingredient)-[:RESTRICTED_BY]->(:RegulatoryRule {status:'prohibited'})
        RETURN DISTINCT i.inci_name AS inci
    """)}
    index: dict[str, list[str]] = {}
    for r in s.run("""
        MATCH (i:Ingredient) WHERE size(coalesce(i.functions,[])) > 0
        RETURN i.inci_name AS inci, i.functions AS functions
    """):
        if r["inci"] in prohibited:
            continue                      # never suggest a prohibited substitute
        for f in r["functions"]:
            index.setdefault(f, []).append(r["inci"])
    print(f"    substitutes: {len(restricted)} restricted ingredients, "
          f"{len(index)} declared functions")

    rows = []
    for inci, funcs in restricted.items():
        shared: dict[str, int] = {}
        for f in funcs:
            for cand in index.get(f, ()):
                if cand != inci:
                    shared[cand] = shared.get(cand, 0) + 1
        top = sorted(shared.items(), key=lambda kv: (-kv[1], kv[0]))[:SUBSTITUTE_TOP_K]
        for alt, n in top:
            rows.append({"inci": inci, "alt": alt, "shared": n,
                         "confidence": round(n / len(funcs), 3)})
    run(s, """
        UNWIND $rows AS row
        MATCH (i:Ingredient {inci_name: row.inci})
        MATCH (a:Ingredient {inci_name: row.alt})
        MERGE (i)-[e:SUBSTITUTE_FOR]->(a)
        SET e.derived = true, e.rule_id = 'substitute_shared_function',
            e._tier = 4, e._source_id = 'stage6_derive',
            e._confidence = row.confidence, e.shared_functions = row.shared
    """, rows, f"SUBSTITUTE_FOR (tier 4, top {SUBSTITUTE_TOP_K})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-substitutes", action="store_true",
                    help="the SUBSTITUTE_FOR pass is the expensive one")
    a = ap.parse_args()
    drv = lib.neo4j_driver()
    with drv.session() as s:
        company_located_in(s)
        serves_category_from_licence(s)
        serves_category_from_nic(s)
        if not a.skip_substitutes:
            substitute_for(s)
    drv.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
