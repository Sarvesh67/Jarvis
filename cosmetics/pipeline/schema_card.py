"""The schema the agent reads before writing Cypher.

Generated from the LIVE graph plus `ontology/ontology.yaml`, never hand-written. A
hand-maintained schema note rots the moment the data moves — an earlier reference file in
this repo pinned "50 firms in Baddi" while the registry held 976 — and a stale schema is
worse than none, because the agent will confidently write a query against labels that no
longer exist.

Exposed as `python -m pipeline.router --schema`.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib  # noqa: E402

# Properties worth naming per label. The graph carries more; these are the ones a query
# actually filters or returns on, so the card stays readable.
KEY_PROPS = {
    "Company": ["company_id", "legal_name", "norm_name", "cin", "gstins", "district",
                "state", "cluster_id", "website", "email", "phone", "roles",
                "trust_level", "trust_rungs", "trust_reasons"],
    "Facility": ["facility_id", "firm_name", "address_raw", "pincode", "district",
                 "state", "cluster_id"],
    "Licence": ["form_type", "number", "sla_state", "status", "is_loan_licence",
                "granted_on", "expires_on", "regime"],
    "Ingredient": ["inci_name", "cas", "ec_no", "functions"],
    "RegulatoryRule": ["rule_id", "jurisdiction", "source_doc", "clause", "scope",
                       "status", "max_concentration", "conditions", "standard_ref",
                       "applies_to_group"],
    "ProductCategory": ["code", "name", "parent_code"],
    "Cluster": ["cluster_id", "name", "state", "districts", "wedge"],
    "Product": ["product_id", "name", "brand", "source", "country"],
    "Certification": ["cert_id", "name", "body"],
    "ProcessCapability": ["capability_id", "kind", "detail"],
    "PackagingComponent": ["component_id", "type", "material", "volume_ml"],
    "DocChunk": ["chunk_id", "text", "doc_id", "doc_title", "page", "section", "forms"],
    "Association": ["name", "full_name"],
    "Event": ["name", "edition", "year", "city"],
}

# Rules that have already caused a wrong answer in this repo. Each one is a bug that
# shipped, so they are stated as prohibitions rather than advice.
GOTCHAS = [
    "WHERE placed directly after OPTIONAL MATCH joins that optional pattern and filters "
    "NOTHING. Use `WITH ... WHERE`. This silently ignored a cluster filter and answered a "
    "Baddi question with nationwide rows.",
    "RESTRICTED_BY carries ONLY substance-specific restrictions. Blanket EU notes are on "
    "SUBJECT_TO_GENERAL_RULE instead, because attaching them to RESTRICTED_BY reported "
    "AQUA and SODIUM CHLORIDE as EU-restricted.",
    "trust_level is the HIGHEST rung reached, not a quality score. For licence evidence "
    "filter `2 IN c.trust_rungs`, never `c.trust_level >= 2` — L4 (corroborated) outranks "
    "L2 numerically while saying nothing about permission to manufacture.",
    "Registry presence is NOT a licence. A Company with cosmetics roles may have no COS-8; "
    "check `2 IN c.trust_rungs` or traverse to Licence.",
    "roles[] is ADDITIVE and each source uses its own vocabulary. Absence of a role is not "
    "evidence of anything. `*_at_incorporation` roles come from the NIC inside a CIN, which "
    "is never updated after registration and is often stale.",
    "Every query must be bounded. Traversals from a hub node can touch the whole graph.",
    "Licence.regime separates 'cosmetics' from 'drugs'. The CDSCO register is 907 DRUG "
    "licences (Form 25/28 family) and only 3 cosmetics ones (COS-8/COS-9), so ANY question "
    "about who may manufacture COSMETICS must filter `l.regime = 'cosmetics'`. Without it a "
    "pharma licence answers a cosmetics question.",
    "Company roles are regime-stamped: `cosmetics_manufacturer` vs `drugs_manufacturer`. "
    "The bare `manufacturer` role is present on BOTH, so never use it alone to answer a "
    "cosmetics question — exactly 1 company in the graph holds a cosmetics manufacturing "
    "licence, against 453 with a drug one.",
    "LOAN_LICENCE_WITH asserts a PAIRING, never a direction. The CDSCO register names two "
    "firms per loan licence but state FDAs fill the two name columns in opposite "
    "conventions (the same firm appears on both sides in different rows), so the edge is "
    "written both ways with direction_known:false. It can answer 'which firms are tied to "
    "X by a loan licence'; it can NEVER answer 'who manufactures for whom'.",
]

NOT_IN_GRAPH = [
    "Person data — contact names, emails, phones for labs and companies live in Postgres "
    "only and are deliberately unreachable from the graph (hard rule 4 / DPDP). Do not "
    "try to query or report them.",
    "COS-8 / COS-9 state licence registers are largely absent: state authorities do not "
    "publish them in bulk. Licence queries returning little is a COVERAGE GAP, not "
    "evidence that firms are unlicensed.",
]


def build(session) -> dict:
    labels = [{"label": r["l"], "count": r["c"]} for r in session.run(
        "MATCH (n) UNWIND labels(n) AS l RETURN l, count(*) AS c ORDER BY c DESC")]
    rels = [{"type": r["t"], "count": r["c"]} for r in session.run(
        "MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c ORDER BY c DESC")]
    hubs = [{"label": r["lbl"], "id": r["id"], "degree": r["deg"]} for r in session.run(
        """MATCH (n)-[r]-() WITH n, count(r) AS deg WHERE deg > 1000
           RETURN labels(n)[0] AS lbl,
                  coalesce(n.name, n.cluster_id, n.cert_id, n.code, n.inci_name) AS id,
                  deg ORDER BY deg DESC LIMIT 6""")]
    vocab = {
        "cluster_id": [r["v"] for r in session.run(
            "MATCH (c:Cluster) RETURN c.cluster_id AS v ORDER BY v")],
        "ProductCategory.code": [r["v"] for r in session.run(
            "MATCH (c:ProductCategory) RETURN c.code AS v ORDER BY v LIMIT 40")],
        "PackagingComponent.type": [r["v"] for r in session.run(
            "MATCH (p:PackagingComponent) RETURN p.type AS v ORDER BY v")],
        "Licence.form_type": [r["v"] for r in session.run(
            "MATCH (l:Licence) RETURN DISTINCT l.form_type AS v ORDER BY v")],
        "Company.roles": [r["v"] for r in session.run(
            "MATCH (c:Company) UNWIND c.roles AS v RETURN DISTINCT v ORDER BY v")],
        "RegulatoryRule.jurisdiction": [r["v"] for r in session.run(
            "MATCH (r:RegulatoryRule) RETURN DISTINCT r.jurisdiction AS v ORDER BY v")],
    }
    onto = lib.load_yaml("ontology.yaml")
    edges = []
    for key, derived in (("edges", False), ("derived_edges", True)):
        for e in (onto.get(key) or []):
            to = e.get("to")
            edges.append({"type": e["type"], "from": e.get("from"),
                          "to": ", ".join(to) if isinstance(to, list) else to,
                          "derived": derived})
    # LOCATED_IN is declared Facility->Cluster but Stage 6 also derives Company->Cluster;
    # say so rather than let the agent write a query that silently matches half the data.
    edges.append({"type": "LOCATED_IN", "from": "Company", "to": "Cluster",
                  "derived": True})
    return {"labels": labels, "relationships": rels, "ontology_edges": edges,
            "derived_types": sorted({e["type"] for e in edges if e["derived"]}),
            "hubs": hubs, "vocabularies": vocab, "key_properties": KEY_PROPS,
            "provenance": lib.load_yaml("ontology.yaml").get("provenance_envelope"),
            "gotchas": GOTCHAS, "not_in_graph": NOT_IN_GRAPH}


def render(card: dict) -> str:
    out: list[str] = ["# Cosmetics graph schema (generated from the live graph)", ""]
    out.append("## Node labels")
    for x in card["labels"]:
        props = KEY_PROPS.get(x["label"])
        out.append(f"  {x['label']:<20} {x['count']:>7,}"
                   + (f"   {', '.join(props)}" if props else ""))
    out.append("")
    out.append("## Relationship types (live counts)")
    shape: dict[str, list[str]] = {}
    for e in card["ontology_edges"]:
        shape.setdefault(e["type"], []).append(f"({e['from']})->({e['to']})")
    derived = set(card["derived_types"])
    for x in card["relationships"]:
        tag = "  [DERIVED tier 4 — ranks and suggests, never answers compliance]" \
            if x["type"] in derived else ""
        out.append(f"  {x['type']:<24} {x['count']:>7,}   "
                   f"{' | '.join(shape.get(x['type'], []))}{tag}")
    out.append("")
    out.append("## Declared edges with no rows yet (legal to query, currently empty)")
    live = {x["type"] for x in card["relationships"]}
    empty = [e["type"] for e in card["ontology_edges"] if e["type"] not in live]
    out.append("  " + (", ".join(empty) if empty else "(none)"))
    out.append("")
    p = card.get("provenance") or {}
    out.append("## Provenance — on every edge")
    out.append(f"  fields: {', '.join(p.get('fields', []))}")
    out.append("  edge properties are prefixed: _tier, _source_id, _confidence, _retrieved_at")
    out.append(f"  tiers: {p.get('tiers')}")
    out.append("")
    out.append("## Vocabularies — filter on these values, not on free text")
    for k, vs in card["vocabularies"].items():
        out.append(f"  {k}: {', '.join(str(v) for v in vs if v is not None)}")
    out.append("")
    out.append("## Hub nodes — never expand through these unbounded")
    for h in card["hubs"]:
        out.append(f"  {h['label']} {h['id']}  degree={h['degree']:,}")
    out.append("")
    out.append("## Rules (each of these was a real bug)")
    for g in card["gotchas"]:
        out.append(f"  - {g}")
    out.append("")
    out.append("## Not in the graph")
    for n in card["not_in_graph"]:
        out.append(f"  - {n}")
    return "\n".join(out)


def main() -> int:
    drv = lib.neo4j_driver()
    with drv.session() as s:
        print(render(build(s)))
    drv.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
