"""Query Neo4j for NABL-accredited testing labs near Navsari/Gujarat.

Writes results to /tmp/nabl_labs.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402

OUTPUT = "/tmp/nabl_labs.json"


def run_cypher(session, cypher: str, params: dict = None):
    result = session.run(cypher, **(params or {}))
    return [dict(r) for r in result]


def main():
    driver = lib.neo4j_driver()
    results = {}

    with driver.session() as s:
        # ── 1. Enumerate all labels present ────────────────────────────────
        labels = [r["label"] for r in run_cypher(s, "CALL db.labels() YIELD label RETURN label ORDER BY label")]
        print(f"Labels in graph: {labels}")
        results["graph_labels"] = labels

        # ── 2. Enumerate all relationship types ────────────────────────────
        rel_types = [r["relationshipType"] for r in run_cypher(
            s, "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType ORDER BY relationshipType")]
        print(f"Relationship types: {rel_types}")
        results["relationship_types"] = rel_types

        # ── 3. Check for dedicated NablLab / TestingLab labels ─────────────
        nabl_labels = [l for l in labels if any(k in l.upper() for k in ("NABL", "LAB", "TEST"))]
        print(f"Potential NABL/Lab labels: {nabl_labels}")
        results["nabl_candidate_labels"] = nabl_labels

        # ── 4. Sample Company properties to understand what fields exist ───
        sample = run_cypher(s, "MATCH (c:Company) RETURN c LIMIT 1")
        if sample:
            sample_keys = list(sample[0].get("c", {}).keys()) if sample[0].get("c") else []
            print(f"Company property keys (sample): {sample_keys}")
            results["company_property_keys_sample"] = sample_keys

        # ── 5. All NABL testing labs (state = Gujarat or district/city Navsari) ──
        # Labs are Company nodes with roles_claimed containing 'testing_lab'
        # and source from s11_nabl. Filter by state=Gujarat or city/district Navsari.

        # 5a. Gujarat-wide NABL labs
        gujarat_labs_query = """
        MATCH (c:Company)
        WHERE (c.state = 'Gujarat' OR c.state = 'GUJARAT')
          AND (c.roles_claimed IS NOT NULL AND 'testing_lab' IN c.roles_claimed)
        RETURN
            c.company_id        AS company_id,
            c.legal_name        AS name,
            c.city              AS city,
            c.district          AS district,
            c.state             AS state,
            c.pincode           AS pincode,
            c.address_raw       AS address,
            c.roles_claimed     AS roles_claimed,
            c.payload           AS payload_raw
        ORDER BY c.city, c.legal_name
        """
        gujarat_labs = run_cypher(s, gujarat_labs_query)
        print(f"\nGujarat NABL testing labs: {len(gujarat_labs)}")
        results["gujarat_nabl_labs_count"] = len(gujarat_labs)

        # 5b. Specifically Navsari district/city labs (cast wider net)
        navsari_labs_query = """
        MATCH (c:Company)
        WHERE (
            toLower(c.city)     CONTAINS 'navsari'
            OR toLower(c.district) CONTAINS 'navsari'
            OR toLower(c.address_raw) CONTAINS 'navsari'
        )
        AND (c.roles_claimed IS NOT NULL AND 'testing_lab' IN c.roles_claimed)
        RETURN
            c.company_id        AS company_id,
            c.legal_name        AS name,
            c.city              AS city,
            c.district          AS district,
            c.state             AS state,
            c.pincode           AS pincode,
            c.address_raw       AS address,
            c.roles_claimed     AS roles_claimed,
            c.payload           AS payload_raw
        ORDER BY c.legal_name
        """
        navsari_labs = run_cypher(s, navsari_labs_query)
        print(f"Navsari-specific NABL testing labs: {len(navsari_labs)}")
        results["navsari_nabl_labs_count"] = len(navsari_labs)

        # ── 6. Parse payload JSON for accreditation scope / test categories ─
        def enrich_lab(lab: dict) -> dict:
            """Parse the payload_raw JSON string into structured fields."""
            raw = lab.pop("payload_raw", None) or {}
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except Exception:
                    raw = {}
            lab["accreditation"] = raw.get("accreditation", "NABL")
            lab["cab_id"] = raw.get("cab_id")
            lab["certificate_number"] = raw.get("certificate_number")
            lab["certificate_valid_till"] = raw.get("certificate_valid_till")
            lab["certificate_issued"] = raw.get("certificate_issued")
            lab["disciplines"] = raw.get("disciplines", [])
            lab["test_groups"] = raw.get("groups", [])
            lab["groups_matched_cosmetics"] = raw.get("groups_matched", [])
            lab["tests_cosmetics"] = raw.get("tests_cosmetics", False)
            lab["tests_adjacent_pharma"] = raw.get("tests_adjacent_pharma", False)
            lab["discipline_relevant"] = raw.get("discipline_relevant", False)
            lab["standard"] = raw.get("standard")
            return lab

        gujarat_labs_enriched = [enrich_lab(lab) for lab in gujarat_labs]
        navsari_labs_enriched = [enrich_lab(lab) for lab in navsari_labs]

        # ── 7. Check Cluster nodes for Navsari ─────────────────────────────
        navsari_cluster_query = """
        MATCH (cl:Cluster)
        WHERE toLower(cl.name) CONTAINS 'navsari'
           OR ANY(d IN cl.districts WHERE toLower(d) CONTAINS 'navsari')
        RETURN cl.cluster_id AS cluster_id, cl.name AS name,
               cl.state AS state, cl.districts AS districts,
               cl.pincode_ranges AS pincode_ranges
        """
        navsari_clusters = run_cypher(s, navsari_cluster_query)
        print(f"Navsari Cluster nodes: {len(navsari_clusters)}")
        results["navsari_clusters"] = navsari_clusters

        # ── 8. Companies in Navsari cluster ────────────────────────────────
        navsari_company_labs = []
        if navsari_clusters:
            for cl in navsari_clusters:
                cid = cl["cluster_id"]
                cluster_labs_query = """
                MATCH (c:Company)-[:LOCATED_IN|OPERATES*1..2]->(cl:Cluster {cluster_id: $cid})
                WHERE (c.roles_claimed IS NOT NULL AND 'testing_lab' IN c.roles_claimed)
                RETURN
                    c.company_id    AS company_id,
                    c.legal_name    AS name,
                    c.city          AS city,
                    c.state         AS state,
                    c.pincode       AS pincode,
                    c.roles_claimed AS roles_claimed,
                    c.payload       AS payload_raw,
                    cl.cluster_id   AS cluster_id,
                    cl.name         AS cluster_name
                """
                cluster_result = run_cypher(s, cluster_labs_query, {"cid": cid})
                navsari_company_labs.extend(cluster_result)
                print(f"  Cluster {cid}: {len(cluster_result)} labs via LOCATED_IN/OPERATES")

        navsari_cluster_labs_enriched = [enrich_lab(lab) for lab in navsari_company_labs]
        results["navsari_cluster_labs_count"] = len(navsari_cluster_labs_enriched)

        # ── 9. Navsari-area companies (non-lab) linked to testing labs ────
        navsari_co_to_lab_query = """
        MATCH (co:Company)
        WHERE (
            toLower(co.city) CONTAINS 'navsari'
            OR toLower(co.district) CONTAINS 'navsari'
            OR toLower(co.address_raw) CONTAINS 'navsari'
        )
        AND NOT ('testing_lab' IN co.roles_claimed)
        MATCH (co)-[r]->(lab:Company)
        WHERE 'testing_lab' IN lab.roles_claimed
        RETURN
            co.company_id   AS company_id,
            co.legal_name   AS company_name,
            co.city         AS company_city,
            type(r)         AS relationship,
            lab.company_id  AS lab_id,
            lab.legal_name  AS lab_name,
            lab.city        AS lab_city,
            lab.state       AS lab_state
        LIMIT 100
        """
        navsari_co_to_lab = run_cypher(s, navsari_co_to_lab_query)
        print(f"Navsari companies linked to testing labs: {len(navsari_co_to_lab)}")
        results["navsari_company_to_lab_edges"] = navsari_co_to_lab

        # ── 10. Broader Gujarat labs with Navsari pincode prefix 396 ──────
        navsari_pincode_labs_query = """
        MATCH (c:Company)
        WHERE (c.pincode STARTS WITH '396' OR c.pincode STARTS WITH '394')
          AND (c.roles_claimed IS NOT NULL AND 'testing_lab' IN c.roles_claimed)
        RETURN
            c.company_id    AS company_id,
            c.legal_name    AS name,
            c.city          AS city,
            c.district      AS district,
            c.state         AS state,
            c.pincode       AS pincode,
            c.address_raw   AS address,
            c.roles_claimed AS roles_claimed,
            c.payload       AS payload_raw
        ORDER BY c.pincode, c.legal_name
        """
        pincode_labs = run_cypher(s, navsari_pincode_labs_query)
        print(f"Labs with Navsari/Surat-area pincodes (396xxx/394xxx): {len(pincode_labs)}")
        pincode_labs_enriched = [enrich_lab(lab) for lab in pincode_labs]
        results["navsari_pincode_area_labs_count"] = len(pincode_labs_enriched)

        # ── 11. All Gujarat labs with full test scope ──────────────────────
        # De-duplicate across all query buckets
        all_labs_map: dict[str, dict] = {}
        for lab in (gujarat_labs_enriched + navsari_labs_enriched
                    + navsari_cluster_labs_enriched + pincode_labs_enriched):
            key = lab.get("company_id") or lab.get("name", "")
            if key and key not in all_labs_map:
                all_labs_map[key] = lab

        all_labs = list(all_labs_map.values())
        print(f"\nTotal unique labs (Gujarat/Navsari-area, all buckets): {len(all_labs)}")

    # ── Assemble final output ──────────────────────────────────────────────
    output = {
        "query_summary": {
            "description": "NABL-accredited testing labs near Navsari / Gujarat",
            "filters": [
                "Company.state = 'Gujarat' OR 'GUJARAT'",
                "toLower(city/district/address) CONTAINS 'navsari'",
                "pincode STARTS WITH '396' OR '394'",
                "Cluster.districts CONTAINS 'navsari'"
            ],
            "roles_filter": "'testing_lab' IN Company.roles_claimed",
            "total_unique_labs": len(all_labs),
            "gujarat_total": results["gujarat_nabl_labs_count"],
            "navsari_direct_match": results["navsari_nabl_labs_count"],
            "navsari_pincode_match": results["navsari_pincode_area_labs_count"],
            "navsari_cluster_match": results["navsari_cluster_labs_count"],
        },
        "graph_schema": {
            "labels": results["graph_labels"],
            "relationship_types": results["relationship_types"],
            "nabl_candidate_labels": results["nabl_candidate_labels"],
        },
        "navsari_clusters": results["navsari_clusters"],
        "navsari_company_to_lab_edges": results["navsari_company_to_lab_edges"],
        "gujarat_nabl_labs": gujarat_labs_enriched,
        "navsari_direct_labs": navsari_labs_enriched,
        "navsari_pincode_area_labs": pincode_labs_enriched,
        "navsari_cluster_labs": navsari_cluster_labs_enriched,
        "all_unique_labs": all_labs,
    }

    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n✓ Results written to {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
