"""Graph context retrieval — the GraphRAG path.

Retrieval ONLY. It returns facts and their connections; the agent does the reasoning.
That split is deliberate and matches the platform's cost model: paying one model to
summarise before the agent reasons is the double-LLM waste Jarvis avoids everywhere else.

Two entry points, and the second is what stops this being a passive keyword fetch:

    from_question(q)                  -- link entities in the question, seed, expand
    expand_from(refs, edge_types, ..) -- the agent steers a further hop from whatever it
                                         found interesting in the first result

Sampling honesty: this returns a NEIGHBOURHOOD, not a census. Counting and ranking belong
in generated Cypher (`pipeline/cypher_guard.py`), and every result says so, because a
sample presented as a total is how "3 suppliers" becomes a wrong answer.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib  # noqa: E402

# Budgets — a context blob must fit in a prompt and still leave room to think.
MAX_SEEDS = 8
PER_SEED = 25
MAX_NODES = 60
MAX_EDGES = 120
MAX_CHUNKS = 6
# Above this a node is a hub: it may be a seed and it may be returned, but hop 2 never
# expands *out* of it. Cluster(Ahmedabad-Vapi) has degree 16,917 — one careless hop
# through it returns the whole graph.
HUB_DEGREE = 500

# Derived, tier-4 edges. SUBSTITUTE_FOR alone is 80k and would swamp any neighbourhood.
DERIVED_EDGES = ["SUBSTITUTE_FOR", "SERVES_CATEGORY", "IN_CLUSTER_WITH",
                 "LIKELY_MANUFACTURES_FOR", "CAN_FILL", "LIKELY_MAKES"]

# label -> the property that names it, used to build a stable, human-readable ref
KEY_OF = {
    "Company": "company_id", "Facility": "facility_id", "Ingredient": "inci_name",
    "ProductCategory": "code", "Cluster": "cluster_id", "RegulatoryRule": "rule_id",
    "Product": "product_id", "Certification": "cert_id", "Association": "name",
    "ProcessCapability": "capability_id", "PackagingComponent": "component_id",
    "DocChunk": "chunk_id", "Event": "name", "Licence": "number",
}
NAME_OF = {"Company": "legal_name", "Facility": "firm_name", "Cluster": "name",
           "ProductCategory": "name", "Ingredient": "inci_name", "Product": "name"}


def _label(node) -> str:
    labs = [l for l in node.labels]
    return labs[0] if labs else "Node"


def ref_of(node) -> str:
    lab = _label(node)
    key = node.get(KEY_OF.get(lab, "")) or node.element_id
    return f"{lab}:{key}"


def _node_out(node) -> dict:
    lab = _label(node)
    d = {"ref": ref_of(node), "label": lab}
    name = node.get(NAME_OF.get(lab, ""))
    if name:
        d["name"] = name
    # a compact, useful projection rather than every property
    for k in ("district", "state", "cluster_id", "trust_level", "trust_rungs", "roles",
              "jurisdiction", "clause", "status", "form_type", "is_loan_licence",
              "type", "code", "website", "cin"):
        v = node.get(k)
        if v not in (None, [], ""):
            d[k] = v
    return d


def _edge_out(rel, a, b) -> dict:
    d = {"from": ref_of(a), "type": rel.type, "to": ref_of(b)}
    for k in ("_tier", "_source_id", "_confidence", "evidence", "mode", "derived"):
        v = rel.get(k)
        if v not in (None, ""):
            d[k.lstrip("_")] = v
    return d


# Named entities are not the only things a question anchors on. "a NABL lab", "restricted
# in the EU", "COS-8" are CONCEPTS: they name a set of nodes rather than one node, and
# without them a multi-entity question seeds on the one proper noun it happens to contain
# and silently drops the rest of what was asked.
CONCEPTS: list[tuple[str, str, str, str]] = [
    # (regex, label, cypher WHERE fragment on `n`, description)
    (r"\bNABL\b|\baccredit\w*\b|\btesting lab\w*\b|\blaborator\w*\b|\btest(ed|ing)?\b",
     "Certification", "n.body = 'NABL'", "NABL accreditation"),
    (r"\bEU\b|\beurope\w*\b|\bcosing\b|\bannex\b",
     "RegulatoryRule", "n.jurisdiction = 'EU' AND NOT coalesce(n.applies_to_group,false)",
     "EU restriction"),
    (r"\bindia\w*\b.{0,24}\b(rule|restrict\w*|prohibit\w*|allow\w*|permit\w*)|"
     r"\b(rule|restrict\w*|prohibit\w*)\b.{0,24}\bindia\b",
     "RegulatoryRule", "n.jurisdiction = 'IN'", "India restriction"),
    (r"\bCOS-?\s?8\b|\blicen[cs]\w*\b", "Licence", "n.form_type IS NOT NULL", "licence"),
    (r"\bCOS-?\s?9\b|\bloan licen[cs]\w*\b", "Licence", "n.is_loan_licence = true",
     "loan licence"),
    (r"\bexport\w*\b|\bchemexcil\b", "Association", "true", "export council"),
]


def concept_seeds(session, question: str, budget: int) -> list[dict]:
    import re as _re

    out: list[dict] = []
    for pattern, label, where, desc in CONCEPTS:
        if len(out) >= budget or not _re.search(pattern, question, _re.I):
            continue
        prop = KEY_OF.get(label)
        # take the best-connected few: a concept names a SET, and the densest members are
        # the ones that actually tie the rest of the neighbourhood together
        for r in session.run(
                f"""MATCH (n:{label}) WHERE {where}
                    RETURN n.{prop} AS v, COUNT {{ (n)--() }} AS deg
                    ORDER BY deg DESC LIMIT 2"""):
            if r["v"] is not None and len(out) < budget:
                out.append({"label": label, "prop": prop, "value": r["v"],
                            "matched_by": f"concept: {desc}"})
    return out


def link_entities(session, question: str) -> list[dict]:
    """Deterministic entity linking — the same longest-match matcher used by S14b and the
    doc indexer, so the graph and the linker can never disagree about what a name means."""
    from pipeline.router import _vocab

    vocab = _vocab(session)
    toks = lib.tokens_of(question)
    seeds: list[dict] = []

    def add(label: str, prop: str, value, how: str):
        if value is None or len(seeds) >= MAX_SEEDS:
            return
        if any(s["label"] == label and s["value"] == value for s in seeds):
            return
        seeds.append({"label": label, "prop": prop, "value": value, "matched_by": how})

    for kind, (label, prop) in {
            "cluster_id": ("Cluster", "cluster_id"),
            "category_code": ("ProductCategory", "code"),
            "packaging_type": ("PackagingComponent", "type")}.items():
        table = vocab.get(kind) or {}
        for name, code in lib.ngrams(toks, set(table)).items():
            add(label, prop, table[name], f"'{name}' -> {kind}")

    # A district is a PROPERTY, not a node, so it cannot be a seed on its own. Seeding the
    # cluster instead would answer "Navsari" with the whole Ahmedabad-Vapi belt, so seed
    # the best-connected companies actually IN that district — local context, not regional.
    dtable = vocab.get("district") or {}
    for name in lib.ngrams(toks, set(dtable)):
        for r in session.run(
                """MATCH (c:Company) WHERE toUpper(c.district) = $d
                   RETURN c.company_id AS id, COUNT { (c)--() } AS deg
                   ORDER BY deg DESC LIMIT 3""", d=dtable[name]):
            add("Company", "company_id", r["id"], f"in district {name}")

    # ingredients: the vocabulary is 35k names, so match against the graph directly
    for r in session.run(
            "MATCH (i:Ingredient) WHERE i.inci_name IN $c RETURN i.inci_name AS n",
            c=[" ".join(toks[i:i + k]) for k in range(1, 5)
               for i in range(max(0, len(toks) - k + 1))][:400]):
        add("Ingredient", "inci_name", r["n"], "INCI name in question")

    for r in session.run(
            """MATCH (c:Company) WHERE toUpper(c.legal_name) IN $c
               RETURN c.company_id AS id, c.legal_name AS n LIMIT 5""",
            c=[" ".join(toks[i:i + k]) for k in range(2, 6)
               for i in range(max(0, len(toks) - k + 1))][:400]):
        add("Company", "company_id", r["id"], f"company name '{r['n']}'")

    for c in concept_seeds(session, question, MAX_SEEDS - len(seeds)):
        add(c["label"], c["prop"], c["value"], c["matched_by"])
    return seeds


def _seed_nodes(session, seeds: list[dict]) -> list:
    out = []
    for s in seeds:
        rec = session.run(
            f"MATCH (n:{s['label']} {{{s['prop']}: $v}}) RETURN n, "
            f"COUNT {{ (n)--() }} AS deg LIMIT 1", v=s["value"]).single()
        if rec:
            out.append((rec["n"], rec["deg"]))
    return out


def _expand(session, start_ids: list[str], *, edge_types: list[str] | None,
            include_derived: bool, per_seed: int):
    """One hop out of the given nodes, capped, with richer nodes preferred."""
    excluded = [] if include_derived else DERIVED_EDGES
    return list(session.run(
        """
        MATCH (n) WHERE elementId(n) IN $ids
        CALL (n) {
            WITH n
            MATCH (n)-[r]-(m)
            WHERE ($types IS NULL OR type(r) IN $types)
              AND NOT type(r) IN $excluded
            RETURN r, m
            ORDER BY size(keys(m)) DESC
            LIMIT $per_seed
        }
        RETURN n, r, m, startNode(r) = n AS outgoing
        """,
        ids=start_ids, types=edge_types, excluded=excluded, per_seed=per_seed))


def chunks_for(session, question: str, k: int = MAX_CHUNKS) -> list[dict]:
    try:
        vec = lib.embed([question])[0]
    except Exception as e:
        return [{"error": f"embeddings unavailable: {e}"}]
    return [{"score": round(r["score"], 3), "document": r["doc"], "page": r["page"],
             "section": r["section"], "passage": r["passage"],
             "mentions": [x for x in (r["mentions"] or []) if x]}
            for r in session.run("""
        CALL db.index.vector.queryNodes('docchunk_embedding', $k, $v)
        YIELD node, score
        OPTIONAL MATCH (node)-[:MENTIONS]->(e)
        RETURN score, node.doc_title AS doc, node.page AS page, node.section AS section,
               left(node.text, 500) AS passage,
               collect(DISTINCT coalesce(e.legal_name, e.inci_name, e.code))[..6] AS mentions
        ORDER BY score DESC
    """, k=k, v=vec)]


def _collect(session, seed_pairs, *, edge_types, include_derived, hops) -> tuple[dict, list, list]:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    seen_edges: set[str] = set()
    notes: list[str] = []
    frontier = []
    for n, deg in seed_pairs:
        nodes[ref_of(n)] = _node_out(n) | {"seed": True, "degree": deg}
        frontier.append((n.element_id, deg))

    for hop in range(hops):
        if not frontier or len(nodes) >= MAX_NODES:
            break
        # hop 1 may leave a hub (that is often the point — "firms in Baddi"); deeper hops
        # never do, or one traversal drags the whole graph back
        ids = [eid for eid, deg in frontier if hop == 0 or deg <= HUB_DEGREE]
        skipped = len(frontier) - len(ids)
        if skipped:
            notes.append(f"hop {hop + 1}: did not expand out of {skipped} hub node(s)")
        if not ids:
            break
        nxt = []
        for rec in _expand(session, ids, edge_types=edge_types,
                           include_derived=include_derived, per_seed=PER_SEED):
            n, r, m = rec["n"], rec["r"], rec["m"]
            a, b = (n, m) if rec["outgoing"] else (m, n)
            ek = f"{ref_of(a)}|{r.type}|{ref_of(b)}"
            if ek in seen_edges or len(edges) >= MAX_EDGES:
                continue
            seen_edges.add(ek)
            edges.append(_edge_out(r, a, b))
            mr = ref_of(m)
            if mr not in nodes and len(nodes) < MAX_NODES:
                nodes[mr] = _node_out(m)
                nxt.append((m.element_id, None))
        if not nxt:
            break
        # degrees for the next frontier, so hub rules apply on the following hop
        degs = {r["eid"]: r["deg"] for r in session.run(
            "MATCH (n) WHERE elementId(n) IN $ids RETURN elementId(n) AS eid, "
            "COUNT { (n)--() } AS deg", ids=[e for e, _ in nxt])}
        frontier = [(e, degs.get(e, 0)) for e, _ in nxt]
    return nodes, edges, notes


def from_question(session, question: str, *, hops: int = 2,
                  include_derived: bool = False) -> dict:
    seeds = link_entities(session, question)
    seed_pairs = _seed_nodes(session, seeds)
    nodes, edges, notes = _collect(session, seed_pairs, edge_types=None,
                                   include_derived=include_derived, hops=hops)
    chunks = chunks_for(session, question)
    if not seeds:
        notes.append("No entity in the question matched the graph vocabulary, so this is "
                     "document context only. Name a cluster, category, ingredient or "
                     "company to get graph context.")
    return _envelope(question, seeds, nodes, edges, chunks, notes, include_derived)


def expand_from(session, refs: list[str], *, edge_types: list[str] | None = None,
                hops: int = 1, include_derived: bool = False) -> dict:
    """Steered expansion — the agent names what to expand from."""
    seed_pairs = []
    unknown = []
    for ref in refs:
        lab, _, key = ref.partition(":")
        prop = KEY_OF.get(lab)
        if not prop:
            unknown.append(ref)
            continue
        rec = session.run(f"MATCH (n:{lab} {{{prop}: $v}}) RETURN n, COUNT {{ (n)--() }} AS deg",
                          v=key).single()
        (seed_pairs.append((rec["n"], rec["deg"])) if rec else unknown.append(ref))
    nodes, edges, notes = _collect(session, seed_pairs, edge_types=edge_types,
                                   include_derived=include_derived, hops=hops)
    if unknown:
        notes.append(f"unresolved refs: {', '.join(unknown)} — use the `ref` value "
                     f"exactly as returned, e.g. Cluster:HP_BBN")
    return _envelope("(expansion)", [], nodes, edges, [], notes, include_derived)


def _envelope(question, seeds, nodes, edges, chunks, notes, include_derived) -> dict:
    return {
        "answered_by": "graph_context",
        "question": question,
        "seeds": seeds,
        "nodes": list(nodes.values()),
        "edges": edges,
        "chunks": chunks,
        "counts": {"nodes": len(nodes), "edges": len(edges), "chunks": len(chunks)},
        "budget": {"max_nodes": MAX_NODES, "max_edges": MAX_EDGES,
                   "per_seed": PER_SEED, "hub_degree": HUB_DEGREE},
        "derived_edges_included": include_derived,
        "notes": notes,
        "trust": "EXPLORATORY SAMPLE — a neighbourhood, not a census. Do NOT count or rank "
                 "from this; use generated Cypher for totals. Every edge carries its tier: "
                 "1 regulator, 2 company-declared, 3 observed, 4 inferred. Tier 4 never "
                 "answers a licence, compliance or restriction question.",
    }
