"""Stage 5 — load into Neo4j.  Spec §11.

MERGE on natural keys, batched via UNWIND. Running twice must produce identical counts;
--assert-idempotent proves it by loading, counting, loading again and comparing.

Every edge carries `_prov` (hard rule 9). Every RegulatoryRule carries `jurisdiction`
(hard rule 2) — asserted before write, not hoped for. Person never appears (hard rule 4).
"""
from __future__ import annotations

import os

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib  # noqa: E402
from pipeline import reconcile as rec  # noqa: E402
from pipeline.contact import business_contacts  # noqa: E402
from pipeline.gstin import is_personal, is_personal_pan, is_proprietorship, public_gstin  # noqa: E402

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
    ing_edges, cat_edges, grp_edges = [], [], []
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
        if r["rule_scope"] == "category":
            cat_edges.append(edge)
        elif r["applies_to_group"]:
            # A blanket note ("ban substances that deplete the ozone layer") is attached
            # to thousands of ingredients and asserts nothing about any one of them.
            # Keeping it out of RESTRICTED_BY entirely is what makes the guarantee
            # GENERIC: every consumer — the agent, ad-hoc Cypher, a future template —
            # gets substance-specific restrictions from that edge, without having to
            # know to filter. Reported AQUA and SODIUM CHLORIDE as EU-restricted before.
            grp_edges.append(edge)
        else:
            ing_edges.append(edge)

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
    """, ing_edges, "RESTRICTED_BY (substance-specific only)")

    run(s, """
        UNWIND $rows AS row
        MATCH (r:RegulatoryRule {rule_id: row.rule_id})
        MATCH (i:Ingredient {inci_name: row.subject})
        MERGE (i)-[e:SUBJECT_TO_GENERAL_RULE]->(r)
        SET e += row.prov
    """, grp_edges, "SUBJECT_TO_GENERAL_RULE (blanket notes)")

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

    # An annex entry names a FAMILY: "SALICYLIC ACID AND ITS SALTS" (Annex V/3),
    # "1-NAPHTHOL AND ITS SALTS" (Annex III/16). CosIng also publishes the bare substance
    # as its own INCI ingredient, so the graph holds two nodes and the restriction lands
    # only on the family one. Asking about salicylic acid — one of the most-asked
    # ingredients in cosmetics — therefore returned NOTHING, which in a compliance answer
    # reads as "not restricted". A false negative here is the worst error this product
    # can make, and it is silent.
    #
    # The family entry covers the bare substance by its own wording, so the edge is a
    # restatement of the rule, not an inference: `covers_family` records that the rule was
    # written about the family and this ingredient is a member, so an answer can say so.
    # Only the head of the phrase is matched — never a suffix — so "SODIUM SALICYLATE"
    # does not pick up salicylic acid's entry by resembling it.
    run(s, """
        UNWIND $rows AS row
        MATCH (fam:Ingredient {inci_name: row.subject})-[:RESTRICTED_BY]->(r:RegulatoryRule)
        MATCH (base:Ingredient {inci_name: row.base})
        WHERE base <> fam
        MERGE (base)-[e:RESTRICTED_BY]->(r)
        SET e += row.prov, e.covers_family = row.subject
    """, _family_members(ing_edges), "RESTRICTED_BY (annex family -> named substance)")


KEEP_PHARMA = os.environ.get("COSMETICS_KEEP_PHARMA") == "1"


def pharma_only_entities() -> set[str]:
    """Firms present ONLY as a DRUG licence on the CDSCO register.

    The register is 907 drug licences against 3 cosmetics ones, and of its 491 firms
    only 7 appear in any cosmetics source. The other 484 are pharmaceutical companies
    with no cosmetics footprint: they cannot answer a cosmetics sourcing question, but
    they can very easily be mistaken for an answer to one. They stay in Postgres with
    full provenance -- this only keeps them out of the ANSWERABLE graph, so the decision
    is reversible by re-running the load without this filter.
    """
    return {r["entity_id"] for r in q("""
        WITH linked AS (
          SELECT x.entity_id, bool_or(r.source_id <> 's01_cdsco') AS other_source,
                 bool_or(r.payload->>'regime' = 'cosmetics') AS cosmetics_regime
          FROM raw_company r
          JOIN crosswalk x ON x.source_id = r.source_id AND x.source_key = r.source_key
           AND x.entity_type = 'company' AND x.superseded_by IS NULL
          GROUP BY x.entity_id)
        SELECT entity_id FROM linked
        WHERE other_source IS FALSE AND cosmetics_regime IS FALSE
        -- ...except a firm standing in a loan relationship. 117 of the 484 do: those
        -- pairs are the only observed contract-manufacturing structure we hold, they
        -- carry regime on the edge, and dropping them would delete the relationship
        -- rather than the noise. The 367 that remain are lone Form-25/28 holders with
        -- no edge to anything.
          AND entity_id NOT IN (
            SELECT x.entity_id FROM raw_facility f
            JOIN crosswalk x ON x.source_id = f.source_id AND x.entity_type = 'company'
             AND x.superseded_by IS NULL
             AND x.source_key IN (f.company_source_key, f.loan_premises_name)
            WHERE f.is_loan_licence AND f.loan_premises_name IS NOT NULL)""")}


def load_companies(s) -> dict[str, dict]:
    winners = rec.reconcile("raw_company", rec.COMPANY_PROPS, "company", persist=False)
    drop = pharma_only_entities() if not KEEP_PHARMA else set()
    if drop:
        winners = {k: v for k, v in winners.items() if k not in drop}
        print(f"  excluded {len(drop)} pharma-only firms from the graph "
              f"(still in Postgres; set COSMETICS_KEEP_PHARMA=1 to load them)")
    udyam_members = {r["entity_id"] for r in q(
        """SELECT DISTINCT x.entity_id FROM raw_company r
           JOIN crosswalk x ON x.source_id=r.source_id AND x.source_key=r.source_key
            AND x.entity_type='company' AND x.superseded_by IS NULL
           WHERE r.payload->>'udyam_registered' = 'true'""")}
    # Where the firm is, as a point, so supplier questions rank by distance rather than
    # by cluster membership (decisions/0028). The pincode's GeoNames centroid, which can
    # sit 10-20 km from the town (Baddi lands ~17 km west): enough to rank a sourcing
    # shortlist, never enough to quote a distance as fact, and labelled as such.
    centroid = {r["pincode"]: (r["lat"], r["lon"]) for r in q(
        "SELECT pincode, lat, lon FROM ref_pincode WHERE lat IS NOT NULL")}
    rows = []
    for eid, w in winners.items():
        props = {k: v for k, v in w.items() if k != "company_id" and v is not None}
        ll = centroid.get(w.get("pincode") or "")
        props["lat"], props["lon"] = ll if ll else (None, None)
        props["location_basis"] = "pincode_centroid" if ll else None
        g = w.get("gstin")
        # Neo4j is readable by the agent's own Cypher, so it holds only what may be shown.
        # A GSTIN on a personal PAN (proprietorship, HUF) is masked and the PAN dropped;
        # the full values stay in Postgres, where person-grade data lives. A null in the
        # map REMOVES the property, which clears values loaded before this rule existed.
        props["gstins"] = [public_gstin(g)] if g else []
        # Only the firm's own lines reach the graph (decisions/0029): no contact at all
        # for a proprietorship, a role mailbox but no phone where the firm's form is
        # unknown. A sole trader's mobile and a named employee's address stay in Postgres.
        props["phone"], props["email"] = business_contacts(
            w.get("legal_name"), w.get("cin"), g, w.get("pan"),
            w.get("phone"), w.get("email"))
        props.pop("gstin", None)
        if is_personal(g) or is_personal_pan(w.get("pan")):
            props["pan"] = None
        if g:
            props["is_proprietorship"] = is_proprietorship(g)
        if eid in udyam_members:
            props["udyam_registered"] = True
        rows.append({"company_id": eid, "props": props})
    run(s, """
        UNWIND $rows AS row
        MERGE (c:Company {company_id: row.company_id})
        SET c += row.props, c.updated_at = datetime(),
            c.location = CASE WHEN row.props.lat IS NULL THEN null
                              ELSE point({latitude: row.props.lat,
                                          longitude: row.props.lon}) END
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


def load_loan_pairs(s) -> None:
    """Tier-1 loan-licence pairings from the CDSCO register.

    Each loan row names the licensee and the premises firm, but not which is which
    (see ontology note on LOAN_LICENCE_WITH). We therefore write the pairing in BOTH
    directions with direction_known:false, so a traversal can find "firms tied to this
    one by a loan licence" without any query being able to conclude who owns the plant.
    """
    rows = q("""SELECT xa.entity_id AS a, xb.entity_id AS b,
                       r.licence_no, r.licence_form, r.payload, r.source_id, r.tier
                FROM raw_facility r
                JOIN crosswalk xa ON xa.source_id = r.source_id
                 AND xa.source_key = r.company_source_key
                 AND xa.entity_type = 'company' AND xa.superseded_by IS NULL
                JOIN crosswalk xb ON xb.source_id = r.source_id
                 AND xb.source_key = r.loan_premises_name
                 AND xb.entity_type = 'company' AND xb.superseded_by IS NULL
                WHERE r.is_loan_licence AND r.loan_premises_name IS NOT NULL""")
    edges = []
    for r in rows:
        if r["a"] == r["b"]:          # register names the same firm on both sides
            continue
        prov = pv(r["source_id"], r["tier"])
        props = {**prov, "licence_no": r["licence_no"],
                 "licence_form": r["licence_form"],
                 "regime": (r["payload"] or {}).get("regime"),
                 "direction_known": False}
        edges.append({"a": r["a"], "b": r["b"], "prov": props})
        edges.append({"a": r["b"], "b": r["a"], "prov": props})
    run(s, """
        UNWIND $rows AS row
        MATCH (a:Company {company_id: row.a})
        MATCH (b:Company {company_id: row.b})
        MERGE (a)-[e:LOAN_LICENCE_WITH {licence_no: row.prov.licence_no}]->(b)
        SET e += row.prov
    """, edges, "LOAN_LICENCE_WITH")


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
    # raw_supplies is the whole truth for this edge, and S14b rewrites it whenever the
    # matching rules change. MERGE alone cannot retract, so a claim the current rules no
    # longer support would survive in the graph; drop the edges and rebuild them.
    s.run("MATCH ()-[e:SUPPLIES]->() "
          "CALL (e) { DELETE e } IN TRANSACTIONS OF 1000 ROWS").consume()
    rows = q("""SELECT entity_id, inci_name, matched_text, evidence, mode, tier, source_id,
                       cosmetics_relevant FROM raw_supplies""")
    payload = [{"company_id": r["entity_id"], "inci": r["inci_name"],
                "matched": r["matched_text"],
                "evidence": r["evidence"], "mode": r["mode"],
                "cosmetics_relevant": r["cosmetics_relevant"],
                "prov": pv(r["source_id"], r["tier"], 0.7)} for r in rows]
    run(s, """
        UNWIND $rows AS row
        MATCH (c:Company {company_id: row.company_id})
        MATCH (i:Ingredient {inci_name: row.inci})
        MERGE (c)-[e:SUPPLIES]->(i)
        SET e += row.prov, e.mode = row.mode, e.evidence = row.evidence,
            e.matched = row.matched, e.cosmetics_relevant = row.cosmetics_relevant
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


def load_packaging(s) -> None:
    """Packaging converters and the component types they make (S17, tier 1).

    A buyer's second question after "who can make it?" is "who packs it?". Component
    VOLUMES are not published by Udyam, so the spec's CAN_FILL rule (facility fill volume
    contains packaging volume) still has no inputs and no CAN_FILL edge is invented.
    Co-location is answered by traversing the shared Cluster rather than materialised:
    1,136 manufacturers x 969 converters in one cluster is a clique, not information.
    """
    # Each run is the whole truth for this edge. When a code stops being packaging
    # (decisions/0027) its edges and its now-unused component node must go, and MERGE
    # alone cannot retract them.
    # batched small: Neo4j runs with ~1.4 GB of real headroom in a 3.8 GB Docker VM shared
    # with Metabase, LiteLLM and FalkorDB, and a 60k-edge delete took it down
    s.run("MATCH ()-[e:SUPPLIES_PACKAGING]->() "
          "CALL (e) { DELETE e } IN TRANSACTIONS OF 1000 ROWS").consume()
    rows = q("""SELECT DISTINCT x.entity_id, r.source_id, r.payload FROM raw_company r
                JOIN crosswalk x ON x.source_id=r.source_id AND x.source_key=r.source_key
                 AND x.entity_type='company' AND x.superseded_by IS NULL
                WHERE r.source_id IN ('s17_udyam','s17b_udyam_api')
                  AND jsonb_array_length(coalesce(r.payload->'packaging_types','[]'::jsonb)) > 0""")
    comps, edges = {}, []
    for r in rows:
        p = r["payload"] or {}
        for t in p.get("packaging_types") or []:
            comps[t] = {"component_id": t, "props": {
                "type": t, "material": t.split("_")[0], "volume_ml": None}}
            edges.append({"company_id": r["entity_id"], "component_id": t,
                          "prov": pv(r["source_id"], 1, 1.0,
                                     basis="udyam_nic_activity")})
    run(s, """
        UNWIND $rows AS row
        MERGE (p:PackagingComponent {component_id: row.component_id}) SET p += row.props
    """, list(comps.values()), "PackagingComponent")
    run(s, """
        UNWIND $rows AS row
        MATCH (c:Company {company_id: row.company_id})
        MATCH (p:PackagingComponent {component_id: row.component_id})
        MERGE (c)-[e:SUPPLIES_PACKAGING]->(p)
        SET e += row.prov
    """, edges, "SUPPLIES_PACKAGING")
    gone = s.run("""MATCH (p:PackagingComponent) WHERE NOT (p)<-[:SUPPLIES_PACKAGING]-()
                    DETACH DELETE p RETURN count(p) AS n""").single()["n"]
    if gone:
        print(f"    (removed {gone} PackagingComponent type(s) no source now supports)")


_FAMILY_HEAD = re.compile(
    r"^(?P<base>.+?)\s+AND\s+ITS\s+(SALTS|ESTERS|SALTS\s+AND\s+ESTERS)\b", re.I)


def _family_members(ing_edges: list[dict]) -> list[dict]:
    """The bare substance each `X and its salts/esters` annex entry names explicitly."""
    out, seen = [], set()
    for e in ing_edges:
        m = _FAMILY_HEAD.match(e["subject"] or "")
        if not m:
            continue
        base = m.group("base").strip().rstrip(",")
        if not base or (e["rule_id"], base) in seen:
            continue
        seen.add((e["rule_id"], base))
        out.append({**e, "base": base})
    return out


def load_labs(s) -> None:
    """NABL accredited testing laboratories (S11), tier 1.

    A lab is a Company with a `testing_lab` role — not a manufacturer, and nothing
    downstream should treat it as one. The accreditation is an ACCREDITED_AS edge rather
    than CLAIMS_CERTIFICATION because it comes from the accreditation body's own register,
    not from the lab's marketing. The scope groups ride on the edge so a query can ask for
    a lab accredited for cosmetics specifically, rather than any accredited lab.
    """
    rows = q("""SELECT DISTINCT x.entity_id, r.payload FROM raw_company r
                JOIN crosswalk x ON x.source_id=r.source_id AND x.source_key=r.source_key
                 AND x.entity_type='company' AND x.superseded_by IS NULL
                WHERE r.source_id='s11_nabl'""")
    # The register has one row per accredited LABORATORY SITE, and resolution merges a
    # multi-site lab into one Company — so SGS India arrives as 8 rows against the same
    # ISO/IEC 17025 standard, of which one carries the cosmetics scope. A per-row SET on
    # a MERGEd (company, certificate) edge therefore let the last row written decide the
    # scope, and the non-cosmetics rows outnumber the cosmetics ones 33:1: 159 labs with
    # a cosmetics scope collapsed to 52, losing SGS, Vimta, Spectro and Equinox — the
    # labs a buyer is most likely to have heard of.
    #
    # Scope is ADDITIVE across a lab's rows for the same reason roles[] is additive
    # across sources (hard rule 1): another site's accreditation is not evidence that
    # this one lapsed. So the rows are folded per (company, certificate) BEFORE the
    # write — flag OR-ed, groups and disciplines unioned — and the certificate number
    # reported is the one that actually carries the cosmetics scope, so the answer cites
    # the accreditation it is relying on rather than an arbitrary sibling site's.
    certs: dict[str, dict] = {}
    agg: dict[tuple[str, str], dict] = {}
    for r in rows:
        p = r["payload"] or {}
        std = (p.get("standard") or "").strip()
        cert_id = f"NABL ISO/IEC 17025:{std}" if std else "NABL ISO/IEC 17025"
        certs[cert_id] = {"cert_id": cert_id,
                          "props": {"name": cert_id, "body": "NABL"}}
        cos = bool(p.get("tests_cosmetics"))
        key = (r["entity_id"], cert_id)
        e = agg.get(key)
        if e is None:
            e = agg[key] = {
                "company_id": r["entity_id"], "cert_id": cert_id,
                "certificate_number": None, "valid_till": None,
                "groups": [], "disciplines": [], "groups_matched": [],
                "tests_cosmetics": False, "accredited_sites": 0,
                "prov": pv("s11_nabl", 1, 1.0, basis="nabl_accreditation_register"),
            }
        e["accredited_sites"] += 1
        e["tests_cosmetics"] = e["tests_cosmetics"] or cos
        for fld, vals in (("groups", p.get("groups") or []),
                          ("disciplines", p.get("disciplines") or []),
                          ("groups_matched", p.get("groups_matched") or [])):
            for v in vals:
                if v not in e[fld]:
                    e[fld].append(v)
        # Prefer the cosmetics-scoped site's certificate; otherwise take the first seen.
        if cos and not e.get("_cos_cert"):
            e["certificate_number"] = p.get("certificate_number")
            e["valid_till"] = p.get("certificate_valid_till")
            e["_cos_cert"] = True
        elif e["certificate_number"] is None:
            e["certificate_number"] = p.get("certificate_number")
            e["valid_till"] = p.get("certificate_valid_till")
    edges = []
    for e in agg.values():
        e.pop("_cos_cert", None)
        # `groups` is the audit trail for `tests_cosmetics`. The adapter deliberately
        # keeps 40 of them because truncating hid the matching scope for 166 labs; the
        # loader was then re-truncating to 12 and hiding it again. Cap generously, and
        # put the scopes that actually matched at the front so the evidence for the flag
        # is the first thing an answer shows.
        e["groups"] = e["groups_matched"] + [g for g in e["groups"]
                                             if g not in e["groups_matched"]]
        e["groups"] = e["groups"][:40]
        edges.append(e)
    run(s, """
        UNWIND $rows AS row
        MERGE (c:Certification {cert_id: row.cert_id}) SET c += row.props
    """, list(certs.values()), "Certification (NABL)")
    run(s, """
        UNWIND $rows AS row
        MATCH (co:Company {company_id: row.company_id})
        MATCH (ce:Certification {cert_id: row.cert_id})
        MERGE (co)-[e:ACCREDITED_AS]->(ce)
        SET e += row.prov, e.certificate_number = row.certificate_number,
            e.valid_till = row.valid_till, e.groups = row.groups,
            e.disciplines = row.disciplines, e.tests_cosmetics = row.tests_cosmetics,
            e.groups_matched = row.groups_matched,
            e.accredited_sites = row.accredited_sites
    """, edges, "ACCREDITED_AS")


def load_all(s) -> None:
    load_clusters(s)
    load_categories(s)
    load_rules_and_ingredients(s)
    load_companies(s)
    load_facilities(s)
    load_operates(s)
    load_loan_pairs(s)
    load_events(s)
    load_associations(s)
    load_site_claims(s)
    load_supplies(s)
    load_products(s)
    load_packaging(s)
    load_labs(s)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wipe", action="store_true", help="drop all nodes before loading")
    ap.add_argument("--assert-idempotent", action="store_true",
                    help="load twice and require identical counts (spec §11)")
    a = ap.parse_args()

    drv = lib.neo4j_driver()
    with drv.session() as s:
        if a.wipe:
            # Relationships first, then nodes, both in small batches. A single
            # DETACH DELETE pass over ~90k nodes carrying ~180k relationships (80k of
            # them SUBSTITUTE_FOR) exhausted the 2 GB heap and took the server down
            # mid-rebuild. Deleting edges first keeps each transaction small and makes
            # `make rebuild` survivable, which the spec requires.
            s.run("MATCH ()-[r]->() CALL (r) { DELETE r } IN TRANSACTIONS OF 5000 ROWS")
            s.run("MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 5000 ROWS")
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
