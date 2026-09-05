"""Stage 5 — load into Neo4j.  Spec §11.

MERGE on natural keys, batched via UNWIND. Running twice must produce identical counts;
--assert-idempotent proves it by loading, counting, loading again and comparing.

Every edge carries `_prov` (hard rule 9). Every RegulatoryRule carries `jurisdiction`
(hard rule 2) — asserted before write, not hoped for. Person never appears (hard rule 4).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib  # noqa: E402
from pipeline import reconcile as rec  # noqa: E402

BATCH = 1000


def pv(source_id: str, tier: int, confidence: float = 1.0, **extra) -> dict:
    """Flat provenance for a Neo4j property bag.

    Neo4j property values must be primitives or arrays of primitives — a nested map is
    rejected — so the envelope is flattened onto underscore-prefixed keys (queryable,
    e.g. `WHERE r._tier <= 2`) and the full JSON is kept alongside for round-tripping.
    """
    env = {"_source_id": source_id, "_tier": int(tier), "_confidence": float(confidence),
           "_retrieved_at": lib.now().isoformat()}
    env.update({f"_{k}": v for k, v in extra.items()})
    env["_prov"] = json.dumps({k.lstrip("_"): v for k, v in env.items()})
    return env


def rid(*parts) -> str:
    return "r_" + hashlib.blake2b("|".join(str(p) for p in parts).encode(),
                                  digest_size=8).hexdigest()[:12]


def run(session, cypher: str, rows: list[dict], label: str) -> int:
    n = 0
    for i in range(0, len(rows), BATCH):
        session.run(cypher, rows=rows[i:i + BATCH])
        n += len(rows[i:i + BATCH])
    if rows:
        print(f"    {label}: {n}")
    return n


# --------------------------------------------------------------------------- readers
def q(sql: str, params: tuple = ()) -> list[dict]:
    with lib.pg() as c, c.cursor() as cur:
        cur.execute(sql, params)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]


# --------------------------------------------------------------------------- loaders
def load_clusters(s) -> None:
    rows = [{"cluster_id": cid, "props": {
        "name": c["name"], "state": c["state"], "wedge": bool(c.get("wedge")),
        "districts": c.get("districts") or [],
        "pincode_ranges": [str(p) for p in (c.get("pincode_prefixes") or [])]}}
        for cid, c in lib.clusters().items()]
    run(s, """
        UNWIND $rows AS row
        MERGE (c:Cluster {cluster_id: row.cluster_id})
        SET c += row.props, c.updated_at = datetime()
    """, rows, "Cluster")


def load_categories(s) -> None:
    spine = lib.load_yaml("category_map.yaml")["spine"]
    rows = [{"code": code, "props": {
        "name": v["name"], "source_vocab": "cosmetics-graph spine",
        "parent_code": v.get("parent"), "level": "spine"}}
        for code, v in spine.items()]
    # Fourth Schedule entries hang off the spine as the regulator's own vocabulary
    for r in q("SELECT payload FROM raw_rule WHERE source_id='s05_rules' "
               "AND clause='Fourth Schedule'"):
        p = r["payload"]
        rows.append({"code": p["category_code"], "props": {
            "name": p["category_name"], "source_vocab": p["vocab"],
            "parent_code": None, "level": "regulator", "serial": p["serial"]}})
    run(s, """
        UNWIND $rows AS row
        MERGE (p:ProductCategory {code: row.code})
        SET p += row.props, p.updated_at = datetime()
    """, rows, "ProductCategory")
    # parent links, including Fourth Schedule -> spine
    _p = pv("s05_rules", 1)
    links = [{"child": r["code"], "parent": r["props"]["parent_code"], "prov": _p}
             for r in rows if r["props"].get("parent_code")]
    for r in q("SELECT subject, payload FROM raw_rule WHERE source_id='s05_rules' "
               "AND clause='Fourth Schedule'"):
        links.append({"child": r["payload"]["category_code"], "parent": r["subject"], "prov": _p})
    run(s, """
        UNWIND $rows AS row
        MATCH (c:ProductCategory {code: row.child})
        MATCH (p:ProductCategory {code: row.parent})
        MERGE (c)-[r:CHILD_OF]->(p)
        SET r += row.prov
    """, links, "ProductCategory CHILD_OF")


def load_rules_and_ingredients(s) -> None:
    # ---- Ingredient nodes
    ing = q("SELECT DISTINCT ON (inci_name) inci_name, cas, ec_no, functions, payload "
            "FROM raw_ingredient WHERE source_id='s06_cosing' AND inci_name IS NOT NULL "
            "ORDER BY inci_name, id")
    rows = [{"inci": r["inci_name"], "props": {
        "cas": (r["payload"] or {}).get("cas_all") or ([r["cas"]] if r["cas"] else []),
        "cas_primary": r["cas"], "ec_no": r["ec_no"],
        "functions": r["functions"] or [],
        "chemical_name": (r["payload"] or {}).get("chemical_name"),
        "cosing_ref": (r["payload"] or {}).get("substance_id"),
        "status": (r["payload"] or {}).get("status"),
        "origin_type": "botanical" if "EXTRACT" in r["inci_name"] or "OIL" in r["inci_name"]
                       else "unknown"}}
        for r in ing]
    run(s, """
        UNWIND $rows AS row
        MERGE (i:Ingredient {inci_name: row.inci})
        SET i += row.props, i.updated_at = datetime()
    """, rows, "Ingredient")

    # ---- aliases as TradeName-free alias property (S7, tier 3)
    al = q("SELECT inci_name, array_agg(DISTINCT alias) aliases FROM raw_ingredient "
           "WHERE alias IS NOT NULL GROUP BY inci_name")
    run(s, """
        UNWIND $rows AS row
        MATCH (i:Ingredient {inci_name: row.inci_name})
        SET i.aliases = row.aliases, i._alias_source = 's07_obf',
            i._alias_tier = 3, i._alias_confidence = 0.8
    """, [{"inci_name": r["inci_name"], "aliases": r["aliases"]} for r in al], "Ingredient aliases")

    # ---- RegulatoryRule nodes, deduped on rule identity (many ingredients share one rule)
    raw_rules = q("SELECT * FROM raw_rule")
    nodes: dict[str, dict] = {}
    ing_edges, cat_edges = [], []
    for r in raw_rules:
        assert r["jurisdiction"] in ("IN", "EU"), "hard rule 2: jurisdiction required"
        key = rid(r["jurisdiction"], r["source_doc"], r["clause"], r["status"],
                  r["standard_ref"] or "", (r["conditions"] or "")[:200],
                  r["subject"] if r["rule_scope"] == "category" else "")
        nodes.setdefault(key, {"rule_id": key, "props": {
            "jurisdiction": r["jurisdiction"], "source_doc": r["source_doc"],
            "clause": r["clause"], "scope": r["rule_scope"], "status": r["status"],
            "max_concentration": r["max_concentration"], "conditions": r["conditions"],
            "standard_ref": r["standard_ref"],
            "applies_to_group": bool(r["applies_to_group"]),
            "source_id": r["source_id"], "tier": r["tier"]}})
        edge = {"rule_id": key, "subject": r["subject"],
                "prov": pv(r["source_id"], r["tier"])}
        (cat_edges if r["rule_scope"] == "category" else ing_edges).append(edge)

    run(s, """
        UNWIND $rows AS row
        MERGE (r:RegulatoryRule {rule_id: row.rule_id})
        SET r += row.props, r.updated_at = datetime()
    """, list(nodes.values()), "RegulatoryRule")

    run(s, """
        UNWIND $rows AS row
        MATCH (r:RegulatoryRule {rule_id: row.rule_id})
        MATCH (i:Ingredient {inci_name: row.subject})
        MERGE (i)-[e:RESTRICTED_BY]->(r)
        SET e += row.prov
    """, ing_edges, "RESTRICTED_BY")

    run(s, """
        UNWIND $rows AS row
        MATCH (r:RegulatoryRule {rule_id: row.rule_id})
        MATCH (p:ProductCategory {code: row.subject})
        MERGE (p)-[e:GOVERNED_BY]->(r)
        SET e += row.prov
    """, cat_edges, "GOVERNED_BY")

    # India ingredient rules name substances (MERCURY, LEAD) that may not be INCI names.
    # Mint them so the rule is never orphaned, and mark how they got there.
    missing = q("""SELECT DISTINCT subject FROM raw_rule WHERE rule_scope='ingredient'
                   AND jurisdiction='IN'""")
    run(s, """
        UNWIND $rows AS row
        MERGE (i:Ingredient {inci_name: row.subject})
        ON CREATE SET i.origin_type='regulatory_subject', i.minted_by='s05_rules'
    """, [{"subject": r["subject"]} for r in missing], "Ingredient (IN rule subjects)")
    run(s, """
        UNWIND $rows AS row
        MATCH (r:RegulatoryRule {rule_id: row.rule_id})
        MATCH (i:Ingredient {inci_name: row.subject})
        MERGE (i)-[e:RESTRICTED_BY]->(r)
        SET e += row.prov
    """, ing_edges, "RESTRICTED_BY (retry after minting)")


def load_companies(s) -> dict[str, dict]:
    winners = rec.reconcile("raw_company", rec.COMPANY_PROPS, "company", persist=False)
    udyam_members = {r["entity_id"] for r in q(
        """SELECT DISTINCT x.entity_id FROM raw_company r
           JOIN crosswalk x ON x.source_id=r.source_id AND x.source_key=r.source_key
            AND x.entity_type='company' AND x.superseded_by IS NULL
           WHERE r.payload->>'udyam_registered' = 'true'""")}
    rows = []
    for eid, w in winners.items():
        props = {k: v for k, v in w.items() if k != "company_id" and v is not None}
        props["gstins"] = [w["gstin"]] if w.get("gstin") else []
        props.pop("gstin", None)
        if eid in udyam_members:
            props["udyam_registered"] = True
        rows.append({"company_id": eid, "props": props})
    run(s, """
        UNWIND $rows AS row
        MERGE (c:Company {company_id: row.company_id})
        SET c += row.props, c.updated_at = datetime()
    """, rows, "Company")
    return winners


def load_facilities(s) -> dict[str, dict]:
    winners = rec.reconcile("raw_facility", rec.FACILITY_PROPS, "facility", persist=False)
    rows = [{"facility_id": eid,
             "props": {k: v for k, v in w.items() if k != "facility_id" and v is not None}}
            for eid, w in winners.items()]
    run(s, """
        UNWIND $rows AS row
        MERGE (f:Facility {facility_id: row.facility_id})
        SET f += row.props, f.updated_at = datetime()
    """, rows, "Facility")

    # LOCATED_IN
    _lp = pv("stage2_normalize", 1)
    loc = [{"facility_id": eid, "cluster_id": w["cluster_id"], "prov": _lp}
           for eid, w in winners.items() if w.get("cluster_id")]
    run(s, """
        UNWIND $rows AS row
        MATCH (f:Facility {facility_id: row.facility_id})
        MATCH (c:Cluster {cluster_id: row.cluster_id})
        MERGE (f)-[e:LOCATED_IN]->(c)
        SET e += row.prov
    """, loc, "LOCATED_IN")

    # Licence + LICENSED_UNDER
    facs = q("""SELECT x.entity_id, r.licence_form, r.licence_no, r.state, r.licence_status,
                       r.granted_on, r.expires_on, r.is_loan_licence, r.categories,
                       r.source_id, r.tier, r.payload
                FROM raw_facility r
                JOIN crosswalk x ON x.source_id=r.source_id AND x.source_key=r.source_key
                 AND x.entity_type='facility' AND x.superseded_by IS NULL
                WHERE r.licence_no IS NOT NULL AND r.licence_no <> ''""")
    lic_rows, lic_edges = {}, []
    for r in facs:
        key = (r["licence_form"] or "OTHER", r["licence_no"], r["state"] or "UNKNOWN")
        lic_rows[key] = {
            "form_type": key[0], "number": key[1], "sla_state": key[2],
            "props": {
                "granted_on": r["granted_on"].isoformat() if r["granted_on"] else None,
                "expires_on": r["expires_on"].isoformat() if r["expires_on"] else None,
                # a source that omits status yields 'unknown', NEVER 'active' (spec §6)
                "status": r["licence_status"] or "unknown",
                "is_loan_licence": bool(r["is_loan_licence"]),
                "categories": r["categories"] or [],
                "categories_known": bool(r["categories"]),
                "regime": (r["payload"] or {}).get("regime"),
                "source_id": r["source_id"], "tier": r["tier"]}}
        lic_edges.append({"facility_id": r["entity_id"], "form_type": key[0],
                          "number": key[1], "sla_state": key[2],
                          "prov": pv(r["source_id"], r["tier"])})
    run(s, """
        UNWIND $rows AS row
        MERGE (l:Licence {form_type: row.form_type, number: row.number, sla_state: row.sla_state})
        SET l += row.props, l.updated_at = datetime()
    """, list(lic_rows.values()), "Licence")
    run(s, """
        UNWIND $rows AS row
        MATCH (f:Facility {facility_id: row.facility_id})
        MATCH (l:Licence {form_type: row.form_type, number: row.number, sla_state: row.sla_state})
        MERGE (f)-[e:LICENSED_UNDER]->(l)
        SET e += row.prov
    """, lic_edges, "LICENSED_UNDER")

    # PERMITS only where a source actually states categories. Nothing is invented:
    # CDSCO's listing carries no category field, so this is legitimately sparse and
    # the coverage report says so rather than the graph implying permission.
    permits = []
    for r in facs:
        for cat in (r["categories"] or []):
            permits.append({"form_type": r["licence_form"] or "OTHER",
                            "number": r["licence_no"], "sla_state": r["state"] or "UNKNOWN",
                            "code": cat, "prov": pv(r["source_id"], r["tier"])})
    run(s, """
        UNWIND $rows AS row
        MATCH (l:Licence {form_type: row.form_type, number: row.number, sla_state: row.sla_state})
        MATCH (p:ProductCategory {code: row.code})
        MERGE (l)-[e:PERMITS]->(p)
        SET e += row.prov
    """, permits, "PERMITS")
    return winners


def load_operates(s) -> None:
    rows = q("SELECT company_id, facility_id, score, basis FROM operates_edge")
    run(s, """
        UNWIND $rows AS row
        MATCH (c:Company {company_id: row.company_id})
        MATCH (f:Facility {facility_id: row.facility_id})
        MERGE (c)-[e:OPERATES]->(f)
        SET e += row.prov
    """, [{"company_id": r["company_id"], "facility_id": r["facility_id"],
           "prov": pv("stage3_resolve", 4, float(r["score"]),
                      basis=r["basis"], inferred=True)} for r in rows], "OPERATES")


def load_events(s) -> None:
    rows = q("""SELECT x.entity_id, r.payload FROM raw_company r
                JOIN crosswalk x ON x.source_id=r.source_id AND x.source_key=r.source_key
                 AND x.entity_type='company' AND x.superseded_by IS NULL
                WHERE r.source_id='s08_exhibitors'""")
    events, edges = {}, []
    for r in rows:
        p = r["payload"] or {}
        key = (p.get("event"), p.get("edition"), int(p.get("year") or 0))
        events[key] = {"name": key[0], "edition": key[1], "year": key[2],
                       "props": {"city": p.get("city"), "url": p.get("event_url")}}
        edges.append({"company_id": r["entity_id"], "name": key[0], "edition": key[1],
                      "year": key[2], "booth": p.get("booth"),
                      "prov": pv("s08_exhibitors", 2)})
    run(s, """
        UNWIND $rows AS row
        MERGE (e:Event {name: row.name, edition: row.edition, year: row.year})
        SET e += row.props
    """, list(events.values()), "Event")
    run(s, """
        UNWIND $rows AS row
        MATCH (c:Company {company_id: row.company_id})
        MATCH (e:Event {name: row.name, edition: row.edition, year: row.year})
        MERGE (c)-[r:EXHIBITED_AT]->(e)
        SET r += row.prov, r.booth = row.booth
    """, edges, "EXHIBITED_AT")


def counts(s) -> dict:
    out = {}
    for rec_ in s.run("MATCH (n) UNWIND labels(n) AS l RETURN l, count(*) AS c ORDER BY l"):
        out["node:" + rec_["l"]] = rec_["c"]
    for rec_ in s.run("MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c ORDER BY t"):
        out["edge:" + rec_["t"]] = rec_["c"]
    return out


def load_associations(s) -> None:
    """Association membership — the only source in the register that evidences EXPORT."""
    rows = q("""SELECT DISTINCT x.entity_id, r.payload FROM raw_company r
                JOIN crosswalk x ON x.source_id=r.source_id AND x.source_key=r.source_key
                 AND x.entity_type='company' AND x.superseded_by IS NULL
                WHERE r.payload->>'association' IS NOT NULL""")
    assoc, edges = {}, []
    for r in rows:
        p = r["payload"] or {}
        name = p["association"]
        assoc[name] = {"name": name, "props": {"full_name": p.get("association_full")}}
        edges.append({"company_id": r["entity_id"], "name": name,
                      "member_id": p.get("member_id"),
                      "prov": pv("s09_chemexcil", 2)})
    run(s, """
        UNWIND $rows AS row
        MERGE (a:Association {name: row.name})
        SET a += row.props
    """, list(assoc.values()), "Association")
    run(s, """
        UNWIND $rows AS row
        MATCH (c:Company {company_id: row.company_id})
        MATCH (a:Association {name: row.name})
        MERGE (c)-[e:MEMBER_OF]->(a)
        SET e += row.prov, e.member_id = row.member_id
    """, edges, "MEMBER_OF")


def load_site_claims(s) -> None:
    """Certifications and process capabilities claimed on a company's own website.

    Tier 2, and every edge carries the SENTENCE the company said it in — a capability
    claim without its evidence is indistinguishable from an inference, and the whole
    point of the tier split is that a buyer can see which one they are looking at.
    """
    rows = q("""SELECT x.entity_id, r.payload, r.website FROM raw_company r
                JOIN crosswalk x ON x.source_id=r.source_id AND x.source_key=r.source_key
                 AND x.entity_type='company' AND x.superseded_by IS NULL
                WHERE r.source_id = 's14_sites'""")
    certs, caps, cert_edges, cap_edges = {}, {}, [], []
    for r in rows:
        p = r["payload"] or {}
        for name, evidence in (p.get("certifications") or {}).items():
            certs[name] = {"cert_id": name, "props": {"name": name, "body": None}}
            cert_edges.append({"company_id": r["entity_id"], "cert_id": name,
                               "evidence": evidence, "prov": pv("s14_sites", 2, 0.9)})
        for name, evidence in (p.get("capabilities") or {}).items():
            caps[name] = {"capability_id": name,
                          "props": {"kind": name.split("_")[0], "detail": name}}
            cap_edges.append({"company_id": r["entity_id"], "capability_id": name,
                              "evidence": evidence, "prov": pv("s14_sites", 2, 0.9)})
    run(s, """
        UNWIND $rows AS row
        MERGE (c:Certification {cert_id: row.cert_id}) SET c += row.props
    """, list(certs.values()), "Certification")
    run(s, """
        UNWIND $rows AS row
        MERGE (p:ProcessCapability {capability_id: row.capability_id}) SET p += row.props
    """, list(caps.values()), "ProcessCapability")
    run(s, """
        UNWIND $rows AS row
        MATCH (co:Company {company_id: row.company_id})
        MATCH (ce:Certification {cert_id: row.cert_id})
        MERGE (co)-[e:CLAIMS_CERTIFICATION]->(ce)
        SET e += row.prov, e.evidence = row.evidence
    """, cert_edges, "CLAIMS_CERTIFICATION")
    run(s, """
        UNWIND $rows AS row
        MATCH (co:Company {company_id: row.company_id})
        MATCH (p:ProcessCapability {capability_id: row.capability_id})
        MERGE (co)-[e:CLAIMS_CAPABILITY]->(p)
        SET e += row.prov, e.evidence = row.evidence
    """, cap_edges, "CLAIMS_CAPABILITY")


def load_supplies(s) -> None:
    """Company -[:SUPPLIES]-> Ingredient, observed on the supplier's own product pages."""
    rows = q("""SELECT entity_id, inci_name, evidence, mode, tier, source_id
                FROM raw_supplies""")
    payload = [{"company_id": r["entity_id"], "inci": r["inci_name"],
                "evidence": r["evidence"], "mode": r["mode"],
                "prov": pv(r["source_id"], r["tier"], 0.7)} for r in rows]
    run(s, """
        UNWIND $rows AS row
        MATCH (c:Company {company_id: row.company_id})
        MATCH (i:Ingredient {inci_name: row.inci})
        MERGE (c)-[e:SUPPLIES]->(i)
        SET e += row.prov, e.mode = row.mode, e.evidence = row.evidence
    """, payload, "SUPPLIES")


def load_products(s) -> None:
    """Observed Indian products (S7), tier 3.

    No Company node is ever minted from this source (spec §6/S7) — brand strings are not
    resolved entities and pretending otherwise would put crowd-sourced pack text into the
    company spine. Products exist so that an ingredient can be traced to something on a
    real Indian shelf, which is what makes the EU-vs-India jurisdiction split legible.
    """
    prods = q("""SELECT source_key, product_name, brands, categories_tags,
                        ingredients_text, payload FROM raw_product
                 WHERE source_id='s07_obf'""")
    alias_map = {r["alias"]: r["inci_name"] for r in q(
        "SELECT alias, inci_name FROM raw_ingredient WHERE alias IS NOT NULL")}
    known = {r["inci_name"] for r in q(
        "SELECT DISTINCT inci_name FROM raw_ingredient WHERE source_id='s06_cosing'")}
    cat_map = lib.load_yaml("category_map.yaml")["mappings"]

    nodes, contains, in_cat = [], [], []
    for r in prods:
        pid = f"p_obf_{r['source_key']}"
        nodes.append({"product_id": pid, "props": {
            "name": r["product_name"], "brand": (r["brands"] or [None])[0],
            "source": "open_beauty_facts", "country": "IN",
            "ingredients_text": (r["ingredients_text"] or "")[:2000] or None}})
        toks = (r["payload"] or {}).get("tokens") or []
        pos = 0
        for t in toks:
            inci = t if t in known else alias_map.get(t)
            if not inci:
                continue
            pos += 1
            contains.append({"product_id": pid, "inci": inci, "position": pos,
                             "prov": pv("s07_obf", 3, 0.7)})
        seen = set()
        for tag in (r["categories_tags"] or []):
            m = cat_map.get(tag.lower())
            if m and m["code"] not in seen:
                seen.add(m["code"])
                in_cat.append({"product_id": pid, "code": m["code"],
                               "tags": m.get("tags") or [], "prov": pv("s07_obf", 3, 0.7)})
    run(s, """
        UNWIND $rows AS row
        MERGE (p:Product {product_id: row.product_id})
        SET p += row.props, p.updated_at = datetime()
    """, nodes, "Product")
    run(s, """
        UNWIND $rows AS row
        MATCH (p:Product {product_id: row.product_id})
        MATCH (i:Ingredient {inci_name: row.inci})
        MERGE (p)-[e:CONTAINS]->(i)
        SET e += row.prov, e.position = row.position
    """, contains, "CONTAINS")
    run(s, """
        UNWIND $rows AS row
        MATCH (p:Product {product_id: row.product_id})
        MATCH (c:ProductCategory {code: row.code})
        MERGE (p)-[e:IN_CATEGORY]->(c)
        SET e += row.prov, e.tags = row.tags
    """, in_cat, "IN_CATEGORY")


def load_all(s) -> None:
    load_clusters(s)
    load_categories(s)
    load_rules_and_ingredients(s)
    load_companies(s)
    load_facilities(s)
    load_operates(s)
    load_events(s)
    load_associations(s)
    load_site_claims(s)
    load_supplies(s)
    load_products(s)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wipe", action="store_true", help="drop all nodes before loading")
    ap.add_argument("--assert-idempotent", action="store_true",
                    help="load twice and require identical counts (spec §11)")
    a = ap.parse_args()

    drv = lib.neo4j_driver()
    with drv.session() as s:
        if a.wipe:
            s.run("MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS")
            print("  wiped")
        load_all(s)
        first = counts(s)
        if a.assert_idempotent:
            print("  second pass (idempotency check)...")
            load_all(s)
            second = counts(s)
            diff = {k: (first.get(k), second.get(k)) for k in set(first) | set(second)
                    if first.get(k) != second.get(k)}
            if diff:
                print(f"  !! NOT IDEMPOTENT: {diff}")
                return 1
            print("  idempotency: PASS — identical counts on re-run")
        print("\n  graph:")
        for k, v in sorted(first.items()):
            print(f"    {k:34s} {v}")
    drv.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
