"""
Query EU-restricted ingredients for companies in the Navsari district
(which maps to cluster GJ_AHMEDABAD_VAPI).

Navsari is a district in Gujarat, mapped to the GJ_AHMEDABAD_VAPI cluster.
There is no standalone "Navsari cluster" node; the Navsari sub-cluster is
identified by c.district = 'Navsari' within GJ_AHMEDABAD_VAPI.

Results written to /tmp/navsari_eu_restricted.json.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402

NAVSARI_DISTRICT = "Navsari"
NAVSARI_CLUSTER  = "GJ_AHMEDABAD_VAPI"   # confirmed above

drv = lib.neo4j_driver()

output = {
    "task": "EU-restricted ingredients used/supplied by companies in the Navsari cluster",
    "navsari_cluster_id": NAVSARI_CLUSTER,
    "navsari_district": NAVSARI_DISTRICT,
    "note": (
        "Navsari is a district in Gujarat assigned to cluster GJ_AHMEDABAD_VAPI. "
        "No standalone 'Navsari' Cluster node exists in the graph. "
        "Companies are filtered by c.district = 'Navsari' AND c.cluster_id = 'GJ_AHMEDABAD_VAPI'. "
        "EU restriction is traversed via RESTRICTED_BY -> RegulatoryRule{jurisdiction:'EU'}, "
        "substance-specific only (applies_to_group IS NOT true)."
    ),
    "eu_restricted_rows": [],
    "meta": {},
}

with drv.session() as s:

    # ---- company count sanity check ----
    n = s.run(
        "MATCH (c:Company) WHERE c.district = $d AND c.cluster_id = $cl RETURN count(c) AS n",
        d=NAVSARI_DISTRICT, cl=NAVSARI_CLUSTER
    ).single()["n"]
    output["meta"]["navsari_company_count"] = n
    print(f"Navsari companies (district+cluster match): {n}")

    # ---- primary query: SUPPLIES -> Ingredient -> RESTRICTED_BY (EU) ----
    # USES does not exist (0 edges); SUPPLIES is the right predicate (788 edges).
    supplies_rows = list(s.run(
        """
        MATCH (c:Company)-[:SUPPLIES]->(ing:Ingredient)-[:RESTRICTED_BY]->(r:RegulatoryRule)
        WHERE c.district = $district
          AND c.cluster_id = $cluster
          AND r.jurisdiction = 'EU'
          AND NOT coalesce(r.applies_to_group, false)
        RETURN c.legal_name       AS company,
               c.company_id      AS company_id,
               c.district        AS district,
               c.cluster_id      AS cluster_id,
               c.website         AS website,
               c.trust_level     AS trust_level,
               ing.inci_name     AS inci_name,
               ing.cas           AS cas,
               ing.functions     AS functions,
               r.rule_id         AS rule_id,
               r.status          AS eu_status,
               r.clause          AS eu_clause,
               r.max_concentration AS max_concentration,
               r.conditions      AS conditions,
               r.source_doc      AS source_doc
        ORDER BY c.legal_name, ing.inci_name
        LIMIT 500
        """,
        district=NAVSARI_DISTRICT,
        cluster=NAVSARI_CLUSTER,
    ))
    print(f"SUPPLIES path rows: {len(supplies_rows)}")

    # ---- secondary query: MARKETS -> Product -> CONTAINS -> Ingredient -> RESTRICTED_BY ----
    product_rows = list(s.run(
        """
        MATCH (c:Company)-[:MARKETS]->(p:Product)-[:CONTAINS]->(ing:Ingredient)
              -[:RESTRICTED_BY]->(r:RegulatoryRule)
        WHERE c.district = $district
          AND c.cluster_id = $cluster
          AND r.jurisdiction = 'EU'
          AND NOT coalesce(r.applies_to_group, false)
        RETURN c.legal_name       AS company,
               c.company_id      AS company_id,
               c.district        AS district,
               c.cluster_id      AS cluster_id,
               c.website         AS website,
               c.trust_level     AS trust_level,
               p.name            AS product_name,
               p.brand           AS brand,
               ing.inci_name     AS inci_name,
               ing.cas           AS cas,
               ing.functions     AS functions,
               r.rule_id         AS rule_id,
               r.status          AS eu_status,
               r.clause          AS eu_clause,
               r.max_concentration AS max_concentration,
               r.conditions      AS conditions,
               r.source_doc      AS source_doc
        ORDER BY c.legal_name, p.name, ing.inci_name
        LIMIT 500
        """,
        district=NAVSARI_DISTRICT,
        cluster=NAVSARI_CLUSTER,
    ))
    print(f"MARKETS->CONTAINS path rows: {len(product_rows)}")

    # ---- sample all companies to see what relationship types they have ----
    print("\nChecking outgoing edge types for Navsari companies (sample 10):")
    sample = list(s.run(
        "MATCH (c:Company) WHERE c.district = $d AND c.cluster_id = $cl "
        "RETURN c.company_id AS id, c.legal_name AS name LIMIT 10",
        d=NAVSARI_DISTRICT, cl=NAVSARI_CLUSTER
    ))
    edge_summary: dict[str, int] = {}
    for sc in sample:
        edges = list(s.run(
            "MATCH (c:Company {company_id: $cid})-[r]->() RETURN DISTINCT type(r) AS t, count(r) AS n",
            cid=sc["id"]
        ))
        for e in edges:
            edge_summary[e["t"]] = edge_summary.get(e["t"], 0) + e["n"]
        types = [(e["t"], e["n"]) for e in edges]
        print(f"  {sc['name']}: {types}")
    output["meta"]["navsari_sample_edge_types"] = edge_summary

    # ---- If still no rows, broaden: all Navsari companies' outbound ingredient edges ----
    if not supplies_rows and not product_rows:
        print("\nNo rows found via standard paths. Checking all Navsari ingredient edges...")
        any_ingredient_edges = list(s.run(
            """
            MATCH (c:Company)-[r]->(ing:Ingredient)
            WHERE c.district = $district AND c.cluster_id = $cluster
            RETURN type(r) AS rel_type, count(r) AS n
            """,
            district=NAVSARI_DISTRICT, cluster=NAVSARI_CLUSTER
        ))
        print(f"  Ingredient edge types from Navsari companies: {[(r['rel_type'], r['n']) for r in any_ingredient_edges]}")
        output["meta"]["navsari_ingredient_edge_types"] = [dict(r) for r in any_ingredient_edges]

        # Try reverse: ingredient -> company
        reverse_ing = list(s.run(
            """
            MATCH (ing:Ingredient)<-[r]-(c:Company)
            WHERE c.district = $district AND c.cluster_id = $cluster
            RETURN type(r) AS rel_type, count(r) AS n
            """,
            district=NAVSARI_DISTRICT, cluster=NAVSARI_CLUSTER
        ))
        print(f"  Reverse ingredient edges: {[(r['rel_type'], r['n']) for r in reverse_ing]}")

        # Broader: all outgoing edge types from Navsari companies
        all_navsari_edges = list(s.run(
            """
            MATCH (c:Company)-[r]->()
            WHERE c.district = $district AND c.cluster_id = $cluster
            RETURN type(r) AS t, count(r) AS n ORDER BY n DESC
            """,
            district=NAVSARI_DISTRICT, cluster=NAVSARI_CLUSTER
        ))
        print(f"\n  All outgoing edge types from Navsari companies:")
        for e in all_navsari_edges:
            print(f"    {e['t']}: {e['n']}")
        output["meta"]["navsari_all_edge_types"] = [dict(r) for r in all_navsari_edges]

    # ---- Also try: eu_restricted_in_indian_products template logic
    # but scoped to Navsari companies via MARKETS ----
    print("\n=== Template eu_restricted_in_indian_products (original, unscoped) ===")
    template_rows = list(s.run(
        """
        MATCH (p:Product)-[:CONTAINS]->(i:Ingredient)-[:RESTRICTED_BY]->(r:RegulatoryRule)
        WHERE r.jurisdiction = 'EU'
          AND NOT coalesce(r.applies_to_group, false)
        WITH i, r, count(DISTINCT p) AS indian_products
        // Now check if any Navsari company markets a product containing this ingredient
        OPTIONAL MATCH (c:Company)-[:MARKETS]->(:Product)-[:CONTAINS]->(i)
        WHERE c.district = $district AND c.cluster_id = $cluster
        WITH i, r, indian_products, collect(DISTINCT c.legal_name) AS navsari_companies
        WHERE size(navsari_companies) > 0
        RETURN i.inci_name AS ingredient, r.status AS eu_status, r.clause AS eu_clause,
               r.source_doc AS source_doc, indian_products,
               navsari_companies,
               'EU_ONLY — no equivalent India rule in this graph' AS jurisdiction_note
        ORDER BY indian_products DESC
        LIMIT 200
        """,
        district=NAVSARI_DISTRICT,
        cluster=NAVSARI_CLUSTER,
    ))
    print(f"  Template-style rows: {len(template_rows)}")

    # Aggregate all unique rows into the main result
    seen = set()
    all_rows = []

    for r in supplies_rows:
        key = (r["company_id"], r["inci_name"], r.get("rule_id"))
        if key not in seen:
            seen.add(key)
            all_rows.append({
                "source": "SUPPLIES",
                **dict(r)
            })

    for r in product_rows:
        key = (r["company_id"], r["inci_name"], r.get("rule_id"))
        if key not in seen:
            seen.add(key)
            all_rows.append({
                "source": "MARKETS->CONTAINS",
                **dict(r)
            })

    for r in template_rows:
        for company_name in (r["navsari_companies"] or []):
            key = (company_name, r["ingredient"], None)
            if key not in seen:
                seen.add(key)
                all_rows.append({
                    "source": "eu_restricted_in_indian_products_template",
                    "company": company_name,
                    "inci_name": r["ingredient"],
                    "eu_status": r["eu_status"],
                    "eu_clause": r["eu_clause"],
                    "source_doc": r["source_doc"],
                    "indian_products_count": r["indian_products"],
                    "jurisdiction_note": r["jurisdiction_note"],
                })

    output["eu_restricted_rows"] = all_rows
    output["meta"]["total_unique_rows"] = len(all_rows)
    output["meta"]["supplies_path_count"] = len(supplies_rows)
    output["meta"]["product_contains_path_count"] = len(product_rows)
    output["meta"]["template_path_count"] = len(template_rows)

drv.close()

# ---- Write output ----
out_path = Path("/tmp/navsari_eu_restricted.json")
out_path.write_text(json.dumps(output, indent=2, default=str))
print(f"\n=== Written: {out_path} ===")
print(f"  navsari_company_count: {output['meta']['navsari_company_count']}")
print(f"  total unique EU-restricted rows: {len(output['eu_restricted_rows'])}")
if output["eu_restricted_rows"]:
    print("\n  First 5 rows:")
    for row in output["eu_restricted_rows"][:5]:
        print(f"    {row}")
