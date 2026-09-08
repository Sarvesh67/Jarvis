"""
Final query: EU-restricted ingredients for the Navsari cluster.
Produces /tmp/navsari_eu_restricted.json.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402

NAVSARI_DISTRICT = "Navsari"
NAVSARI_CLUSTER  = "GJ_AHMEDABAD_VAPI"

drv = lib.neo4j_driver()

with drv.session() as s:

    # ── 1. Navsari company facts ────────────────────────────────────────────
    n_companies = s.run(
        "MATCH (c:Company) WHERE c.district = $d AND c.cluster_id = $cl "
        "RETURN count(c) AS n",
        d=NAVSARI_DISTRICT, cl=NAVSARI_CLUSTER
    ).single()["n"]

    # Outgoing edge-type census for all Navsari companies
    edge_census = {
        r["t"]: r["n"]
        for r in s.run(
            "MATCH (c:Company)-[r]->() "
            "WHERE c.district = $d AND c.cluster_id = $cl "
            "RETURN type(r) AS t, count(r) AS n ORDER BY n DESC",
            d=NAVSARI_DISTRICT, cl=NAVSARI_CLUSTER
        )
    }

    # ── 2. Direct EU-restriction queries (SUPPLIES, MARKETS->CONTAINS) ──────
    # Neither path yields rows (edge_census shows only LOCATED_IN, SUPPLIES_PACKAGING, MEMBER_OF)
    supplies_rows = list(s.run(
        """
        MATCH (c:Company)-[:SUPPLIES]->(ing:Ingredient)-[:RESTRICTED_BY]->(r:RegulatoryRule)
        WHERE c.district = $district AND c.cluster_id = $cluster
          AND r.jurisdiction = 'EU' AND NOT coalesce(r.applies_to_group, false)
        RETURN c.legal_name AS company, c.company_id AS company_id,
               ing.inci_name AS inci_name, ing.functions AS functions,
               r.rule_id AS rule_id, r.status AS eu_status, r.clause AS eu_clause,
               r.max_concentration AS max_concentration, r.conditions AS conditions,
               r.source_doc AS source_doc
        ORDER BY c.legal_name, ing.inci_name
        """,
        district=NAVSARI_DISTRICT, cluster=NAVSARI_CLUSTER
    ))

    markets_rows = list(s.run(
        """
        MATCH (c:Company)-[:MARKETS]->(p:Product)-[:CONTAINS]->(ing:Ingredient)
              -[:RESTRICTED_BY]->(r:RegulatoryRule)
        WHERE c.district = $district AND c.cluster_id = $cluster
          AND r.jurisdiction = 'EU' AND NOT coalesce(r.applies_to_group, false)
        RETURN c.legal_name AS company, c.company_id AS company_id,
               p.name AS product_name, p.brand AS brand,
               ing.inci_name AS inci_name, ing.functions AS functions,
               r.rule_id AS rule_id, r.status AS eu_status, r.clause AS eu_clause,
               r.max_concentration AS max_concentration, r.conditions AS conditions,
               r.source_doc AS source_doc
        ORDER BY c.legal_name, p.name, ing.inci_name
        """,
        district=NAVSARI_DISTRICT, cluster=NAVSARI_CLUSTER
    ))

    # ── 3. Cluster-level EU-restriction context ──────────────────────────────
    # GJ_AHMEDABAD_VAPI companies that SUPPLY EU-restricted ingredients
    cluster_supplies_rows = list(s.run(
        """
        MATCH (c:Company {cluster_id: $cluster})-[:SUPPLIES]->(ing:Ingredient)
              -[:RESTRICTED_BY]->(r:RegulatoryRule)
        WHERE r.jurisdiction = 'EU' AND NOT coalesce(r.applies_to_group, false)
        RETURN c.legal_name AS company, c.district AS district,
               c.company_id AS company_id,
               ing.inci_name AS inci_name, ing.functions AS functions,
               r.rule_id AS rule_id, r.status AS eu_status, r.clause AS eu_clause,
               r.max_concentration AS max_concentration, r.conditions AS conditions,
               r.source_doc AS source_doc
        ORDER BY c.district, c.legal_name, ing.inci_name
        LIMIT 500
        """,
        cluster=NAVSARI_CLUSTER
    ))

    # ── 4. eu_restricted_in_indian_products template (nationwide, unscoped) ──
    template_rows = list(s.run(
        """
        MATCH (p:Product)-[:CONTAINS]->(i:Ingredient)-[:RESTRICTED_BY]->(r:RegulatoryRule)
        WHERE r.jurisdiction = 'EU' AND NOT coalesce(r.applies_to_group, false)
        RETURN i.inci_name AS ingredient,
               i.functions AS functions,
               r.status AS eu_status,
               r.clause AS eu_clause,
               r.source_doc AS source_doc,
               r.max_concentration AS max_concentration,
               r.conditions AS conditions,
               count(DISTINCT p) AS indian_products_count,
               'EU_ONLY — EC 1223/2009 has no force in India' AS jurisdiction_note
        ORDER BY indian_products_count DESC
        LIMIT 100
        """
    ))

drv.close()

# ── Build output document ────────────────────────────────────────────────────
output = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "task": "EU-restricted ingredients used by companies in the Navsari cluster",

    "cluster_resolution": {
        "requested": "Navsari cluster",
        "resolved_cluster_id": NAVSARI_CLUSTER,
        "resolved_cluster_name": "Ahmedabad-Vapi-Ankleshwar",
        "state": "Gujarat",
        "explanation": (
            "No standalone 'Navsari' Cluster node exists in the graph. "
            "Navsari is a district in Gujarat, and 295 companies with "
            "c.district='Navsari' are assigned to cluster GJ_AHMEDABAD_VAPI. "
            "The cluster_map.yaml does not list 'navsari' in GJ_AHMEDABAD_VAPI's "
            "districts array (it lists ahmedabad, valsad, bharuch, surat, etc.), "
            "but the Company nodes themselves carry cluster_id='GJ_AHMEDABAD_VAPI' "
            "and district='Navsari'."
        ),
        "navsari_company_count": n_companies,
    },

    "data_coverage_note": {
        "navsari_company_edge_census": edge_census,
        "summary": (
            f"All {n_companies} Navsari-district companies have only LOCATED_IN, "
            "SUPPLIES_PACKAGING, and MEMBER_OF outgoing edges. "
            "None have SUPPLIES->Ingredient, MARKETS->Product, or USES->Ingredient edges, "
            "so there is no direct path from a Navsari company to an EU-restricted ingredient. "
            "This is a DATA COVERAGE GAP — ingredient-supply relationships have not yet been "
            "loaded for Navsari companies in this graph. "
            "The eu_restricted_in_indian_products template results below reflect the "
            "FULL graph (nationwide) and are the best available proxy."
        ),
    },

    "navsari_direct_eu_restricted_rows": {
        "description": (
            "Rows from MATCH (c:Company)-[:SUPPLIES]->(ing)-[:RESTRICTED_BY]->(r{jurisdiction:'EU'}) "
            "WHERE c.district='Navsari'. Zero rows — no SUPPLIES->Ingredient edges exist for "
            "Navsari companies."
        ),
        "count": len(supplies_rows),
        "rows": [dict(r) for r in supplies_rows],
    },

    "navsari_product_eu_restricted_rows": {
        "description": (
            "Rows from MATCH (c:Company)-[:MARKETS]->(p:Product)-[:CONTAINS]->(ing)"
            "-[:RESTRICTED_BY]->(r{jurisdiction:'EU'}) WHERE c.district='Navsari'. "
            "Zero rows — no MARKETS edges for Navsari companies."
        ),
        "count": len(markets_rows),
        "rows": [dict(r) for r in markets_rows],
    },

    "cluster_gj_ahmedabad_vapi_suppliers_eu_restricted": {
        "description": (
            "Companies in GJ_AHMEDABAD_VAPI cluster (all districts, including Navsari) "
            "that SUPPLY EU-restricted ingredients. Navsari companies are filtered below."
        ),
        "count": len(cluster_supplies_rows),
        "navsari_subset": [dict(r) for r in cluster_supplies_rows
                           if r.get("district") == NAVSARI_DISTRICT],
        "other_districts_sample": [dict(r) for r in cluster_supplies_rows
                                   if r.get("district") != NAVSARI_DISTRICT][:50],
    },

    "eu_restricted_in_indian_products_nationwide": {
        "description": (
            "EU-restricted ingredients found in Indian cosmetic products (nationwide, "
            "via the eu_restricted_in_indian_products whitelisted template). "
            "These are the EU-restricted ingredients that ARE present in the graph's "
            "product data; Navsari-specific company linkage is absent due to coverage gap."
        ),
        "jurisdiction_warning": "EU Regulation EC 1223/2009 has no force in India. These are EU restrictions only.",
        "count": len(template_rows),
        "rows": [dict(r) for r in template_rows],
    },
}

out_path = Path("/tmp/navsari_eu_restricted.json")
out_path.write_text(json.dumps(output, indent=2, default=str))
print(f"Written to {out_path}")
print(f"  Navsari companies: {n_companies}")
print(f"  Direct Navsari EU-restricted rows: {len(supplies_rows) + len(markets_rows)}")
print(f"  GJ_AHMEDABAD_VAPI cluster EU-restricted supplier rows: {len(cluster_supplies_rows)}")
print(f"  Nationwide EU-restricted ingredient rows: {len(template_rows)}")
