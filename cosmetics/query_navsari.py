"""
Query Neo4j cosmetics graph for all companies in the Navsari district (Gujarat).

Navsari is part of the GJ_AHMEDABAD_VAPI cluster. No dedicated Cluster node exists for
Navsari; instead, Company nodes carry district='Navsari' + state='Gujarat' directly.

Output: /tmp/navsari_firms.json
"""
import json
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/Users/sarvesh/Documents/Jarvis/cosmetics")
from lib import neo4j_driver

OUTPUT = "/tmp/navsari_firms.json"

driver = neo4j_driver()

# ── 1. Confirm parent cluster ─────────────────────────────────────────────────
with driver.session() as s:
    parent_cluster = s.run("""
        MATCH (cl:Cluster {cluster_id: 'GJ_AHMEDABAD_VAPI'})
        RETURN cl.cluster_id AS cluster_id, cl.name AS name,
               cl.state AS state, cl.districts AS districts
    """).data()
print(f"[1] Parent cluster: {parent_cluster}")

# ── 2. Fetch all Navsari companies ────────────────────────────────────────────
with driver.session() as s:
    rows = s.run("""
        MATCH (co:Company)
        WHERE co.district = 'Navsari' AND co.state = 'Gujarat'
        OPTIONAL MATCH (co)-[:OPERATES]->(f:Facility)
        OPTIONAL MATCH (f)-[:LICENSED_UNDER]->(lic:Licence)-[:PERMITS]->(pc:ProductCategory)
        OPTIONAL MATCH (f)-[:SERVES_CATEGORY]->(sc:ProductCategory)
        OPTIONAL MATCH (co)-[:CLAIMS_CAPABILITY]->(cap:ProcessCapability)
        OPTIONAL MATCH (co)-[:CLAIMS_CERTIFICATION]->(cert:Certification)
        OPTIONAL MATCH (co)-[:MEMBER_OF]->(assoc:Association)
        OPTIONAL MATCH (co)-[:EXHIBITED_AT]->(evt:Event)
        WITH co, f,
             collect(DISTINCT {code: pc.code, name: pc.name,
                                source_vocab: pc.source_vocab}) AS licence_cats,
             collect(DISTINCT {code: sc.code, name: sc.name,
                                source_vocab: sc.source_vocab}) AS served_cats,
             collect(DISTINCT {kind: cap.kind, detail: cap.detail}) AS capabilities,
             collect(DISTINCT {name: cert.name, body: cert.body}) AS certifications,
             collect(DISTINCT assoc.name) AS associations,
             collect(DISTINCT {name: evt.name, year: evt.year}) AS events
        RETURN
            co.company_id         AS company_id,
            co.legal_name         AS legal_name,
            co.norm_name          AS norm_name,
            co.roles              AS roles,
            co.trust_level        AS trust_level,
            co.constitution       AS constitution,
            co.gst_status         AS gst_status,
            co.gstins             AS gstins,
            co.udyam_no           AS udyam_no,
            co.pan                AS pan,
            co.iec                AS iec,
            co.cin                AS cin,
            co.website            AS website,
            co.address_raw        AS address_raw,
            co.pincode            AS pincode,
            co.state              AS state,
            co.district           AS district,
            co.cluster_id         AS cluster_id,
            co.source_ids         AS source_ids,
            co.source_count       AS source_count,
            co.min_tier           AS min_tier,
            co.trust_reasons      AS trust_reasons,
            f.facility_id         AS facility_id,
            f.firm_name           AS facility_name,
            f.address_raw         AS facility_address,
            f.gmp_schedule        AS gmp_schedule,
            licence_cats, served_cats, capabilities, certifications,
            associations, events
        ORDER BY co.trust_level DESC, co.legal_name
    """).data()

print(f"[2] Rows returned: {len(rows)}")

# ── 3. Merge rows by company_id (OPTIONAL MATCH can fan out) ─────────────────
def _clean_cat_list(lst):
    """Remove nulls and dedupe by code."""
    seen = set()
    out = []
    for c in lst:
        if not c or not c.get("code"):
            continue
        if c["code"] not in seen:
            seen.add(c["code"])
            out.append(c)
    return out

def _clean_list(lst, key="name"):
    seen = set()
    out = []
    for item in lst:
        if not item:
            continue
        v = item if isinstance(item, str) else item.get(key)
        if v and v not in seen:
            seen.add(v)
            out.append(item if not isinstance(item, str) else item)
    return out

firms: dict[str, dict] = {}

for r in rows:
    cid = r["company_id"]
    if cid not in firms:
        firms[cid] = {
            "company_id":     r["company_id"],
            "legal_name":     r["legal_name"],
            "norm_name":      r["norm_name"],
            "roles":          r["roles"] or [],
            "trust_level":    r["trust_level"],
            "constitution":   r["constitution"],
            "gst_status":     r["gst_status"],
            "gstins":         r["gstins"] or [],
            "udyam_no":       r["udyam_no"],
            "pan":            r["pan"],
            "iec":            r["iec"],
            "cin":            r.get("cin"),
            "website":        r["website"],
            "address_raw":    r["address_raw"],
            "pincode":        r["pincode"],
            "district":       r["district"],
            "state":          r["state"],
            "cluster_id":     r["cluster_id"],
            "source_ids":     r["source_ids"] or [],
            "source_count":   r["source_count"],
            "min_tier":       r["min_tier"],
            "trust_reasons":  r["trust_reasons"] or [],
            "facilities":     [],
            "licence_product_categories": [],
            "served_product_categories":  [],
            "process_capabilities":       [],
            "certifications":             [],
            "associations":               [],
            "events":                     [],
        }

    firm = firms[cid]

    # Accumulate facility info
    if r.get("facility_id"):
        fac_ids = [f["facility_id"] for f in firm["facilities"]]
        if r["facility_id"] not in fac_ids:
            firm["facilities"].append({
                "facility_id":   r["facility_id"],
                "firm_name":     r["facility_name"],
                "address_raw":   r["facility_address"],
                "gmp_schedule":  r["gmp_schedule"],
            })

    # Accumulate categories
    for cat in _clean_cat_list(r.get("licence_cats", [])):
        codes = {c["code"] for c in firm["licence_product_categories"]}
        if cat["code"] not in codes:
            firm["licence_product_categories"].append(cat)

    for cat in _clean_cat_list(r.get("served_cats", [])):
        codes = {c["code"] for c in firm["served_product_categories"]}
        if cat["code"] not in codes:
            firm["served_product_categories"].append(cat)

    # Capabilities
    for cap in (r.get("capabilities") or []):
        if cap and cap.get("kind"):
            existing = [(c["kind"], c.get("detail")) for c in firm["process_capabilities"]]
            if (cap["kind"], cap.get("detail")) not in existing:
                firm["process_capabilities"].append(cap)

    # Certifications
    for cert in (r.get("certifications") or []):
        if cert and cert.get("name"):
            existing = [c["name"] for c in firm["certifications"]]
            if cert["name"] not in existing:
                firm["certifications"].append(cert)

    # Associations
    for a in (r.get("associations") or []):
        if a and a not in firm["associations"]:
            firm["associations"].append(a)

    # Events
    for ev in (r.get("events") or []):
        if ev and ev.get("name"):
            existing = [(e["name"], e.get("year")) for e in firm["events"]]
            if (ev["name"], ev.get("year")) not in existing:
                firm["events"].append(ev)

result = list(firms.values())

# ── 4. Role summary statistics ────────────────────────────────────────────────
from collections import Counter
role_counter: Counter = Counter()
for f in result:
    for role in f["roles"]:
        role_counter[role] += 1

# ── 5. Build output ───────────────────────────────────────────────────────────
output = {
    "meta": {
        "query_at":           datetime.now(timezone.utc).isoformat(),
        "district":           "Navsari",
        "state":              "Gujarat",
        "parent_cluster_id":  "GJ_AHMEDABAD_VAPI",
        "parent_cluster_name": "Ahmedabad-Vapi-Ankleshwar",
        "note": (
            "No standalone Navsari cluster node exists in the graph. "
            "All 295 companies are tagged cluster_id='GJ_AHMEDABAD_VAPI' with "
            "district='Navsari'. Navsari pincodes: 396xxx (Jalalpore, Maroli, "
            "Bilimora, Navsari city, Gandevi, Vansda)."
        ),
        "total_companies":    len(result),
        "role_distribution":  dict(role_counter.most_common()),
        "trust_level_distribution": dict(
            Counter(f["trust_level"] for f in result).most_common()
        ),
    },
    "firms": result,
}

with open(OUTPUT, "w", encoding="utf-8") as fh:
    json.dump(output, fh, indent=2, ensure_ascii=False, default=str)

print(f"[3] Written {len(result)} companies → {OUTPUT}")

# ── 6. Console summary ────────────────────────────────────────────────────────
print("\n=== Navsari Cosmetics Firms Summary ===")
print(f"Total companies : {len(result)}")
print(f"Role distribution:")
for role, cnt in role_counter.most_common():
    print(f"  {role:45s} {cnt:4d}")

print("\nTop 10 companies by trust level:")
for firm in result[:10]:
    name = firm["legal_name"] or firm["norm_name"] or firm["company_id"]
    print(f"  [{firm['trust_level']}] {name}")
    print(f"       roles={firm['roles']}")
    if firm["gstins"]:
        print(f"       gstins={firm['gstins'][:2]}")
    if firm["udyam_no"]:
        print(f"       udyam={firm['udyam_no']}")
    if firm["licence_product_categories"]:
        print(f"       licence_cats={[c['name'] or c['code'] for c in firm['licence_product_categories']]}")
    if firm["served_product_categories"]:
        print(f"       served_cats={[c['name'] or c['code'] for c in firm['served_product_categories']]}")

driver.close()
print("\nDone.")
