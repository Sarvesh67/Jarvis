"""S17 — Udyam MSME registry (cosmetics slice).  Tier 1.

Not in the original source register, and it is the single most important addition.

The spec's stated blind spot is that MCA "covers companies and LLPs only —
proprietorships and partnerships, a large share of Indian contract manufacturers, are
entirely absent." Udyam is the registry that DOES see them: registration is by
enterprise, not by legal form, and it carries the NIC-2008 activity codes, district,
pincode and registration date directly.

Reads the parquet already produced by the sibling `msme/` subsystem (DuckDB, no re-parse),
selects rows whose NIC codes fall in the cosmetics manufacture / wholesale / retail
classes, and lands them as raw_company. Bulk stays out of the graph — same discipline as
msme/ and marketdata/: only the cosmetics slice is promoted.

Coverage caveat, surfaced not buried: the available parquet is Gujarat-only. Every other
state's Udyam extract is a data-sourcing task, not a code task.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib  # noqa: E402

SOURCE = "s17_udyam"
TIER = 1
DEFAULT_PARQUET = lib.JARVIS_ROOT / "msme" / "out" / "firms.parquet"

# NIC-2008 subclasses. 2023x is the cosmetics/soap/perfume manufacturing class;
# 46491 and 47722 are the cosmetics wholesale and retail subclasses — tagged with a
# role, never merged into manufacturers (spec §4/S3).
MANUFACTURE = {"20231", "20232", "20233", "20234", "20235", "20236", "20237", "20238", "20239"}
WHOLESALE = {"46491"}
RETAIL = {"47722"}
# Packaging converters. A cosmetics buyer's second question, after "who can make it?",
# is "who packs it?" — and the spec's CAN_FILL rule needs component types on one side and
# facility fill volumes (extracted by S14) on the other.
#
# The "n.e.c." catch-alls 22209 (1,940 firms), 25999 (1,807) and 32909 (11,229) are
# DELIBERATELY EXCLUDED: they are residual buckets whose members are mostly not packaging
# at all, and including them would triple the count while destroying precision.
#
# Each code is typed by the registry's OWN description of it, which Udyam returns with
# every record (decisions/0027), not by the class name. Read that way, four codes this map
# used to carry are not packaging: 23101 is sheet and plate glass, 23109 is construction,
# optical and watch glass (it was feeding every `glass_container` edge), 25910 is generic
# forging and stamping, 25920 is machining and coating. 23103, 25992 and 18115 are the
# codes that say bottles and jars, tins and cans, and printing on plastic, glass or metal.
PACKAGING = {
    "22203": ("plastic_container", "Plastic articles for packing of goods — bottles, "
                                   "jars, containers, closures"),
    "22201": ("plastic_film", "Semi-finished plastics — film, foil, sheet (laminates)"),
    "22202": ("plastic_toiletware", "Plastic tableware, household and toilet articles"),
    "23103": ("glass_container", "Hollow glassware (bottles, jars etc.) for the "
                                 "conveyance or packing of goods"),
    "17022": ("corrugated_box", "Corrugated paperboard containers"),
    "17023": ("carton", "Card board boxes"),
    "17029": ("paper_container", "Other paper and paperboard containers"),
    "25992": ("metal_container", "Containers such as tins and cans for food products "
                                 "and boxes"),
    "18115": ("printed_packaging", "Printing directly onto flexographic plastic, glass, "
                                   "metal, wood and ceramics"),
}

WANTED = MANUFACTURE | WHOLESALE | RETAIL | set(PACKAGING)

# The cosmetics-relevant subclasses, in the regulator's own words, so the graph can
# explain WHY a firm is here rather than just asserting it.
NIC_LABEL = {**{c: d for c, (_, d) in PACKAGING.items()}, **{
    "20231": "Manufacture of soap, all forms",
    "20232": "Manufacture of cleaning and polishing products",
    "20233": "Manufacture of detergent and similar washing agents excluding soap",
    "20234": "Manufacture of perfumes and cologne de-eau",
    "20235": "Manufacture of preparations for oral or dental hygiene",
    "20236": "Manufacture of hair oil, shampoo, hair dye etc.",
    "20237": "Manufacture of cosmetics and toiletries",
    "20238": 'Manufacture of "agarbatti" and other preparations which operate by burning',
    "20239": "Manufacture of other perfumes and toilet preparations n.e.c.",
    "46491": "Wholesale of toiletry, perfumery and cosmetics",
    "47722": "Retail sale of perfumery and cosmetic articles",
}}
# subclasses whose output is a cosmetic in the Cosmetics Rules sense (drives the wedge)
CORE_COSMETIC = {"20234", "20235", "20236", "20237", "20239"}



def roles_for(codes: list[str]) -> list[str]:
    roles = set()
    for c in codes:
        if c in MANUFACTURE:
            roles.add("manufacturer_candidate")
        if c in WHOLESALE:
            roles.add("distributor")
        if c in RETAIL:
            roles.add("retailer")
        if c in CORE_COSMETIC:
            roles.add("cosmetics_core")
        if c in PACKAGING:
            roles.add("packaging_converter")
    return sorted(roles)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", default=str(DEFAULT_PARQUET))
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    src = Path(a.parquet)
    if not src.exists():
        print(f"! parquet not found: {src}")
        return 1

    import duckdb

    con = duckdb.connect()
    codes_sql = ", ".join(f"'{c}'" for c in sorted(WANTED))
    sql = f"""
        SELECT udyam, name, state, district, pincode, reg_date, address,
               nic_codes, activity_labels, outputs, inputs, gstin, gst_status
        FROM read_parquet('{src.as_posix()}')
        WHERE list_has_any(
                CAST(from_json(nic_codes, '["VARCHAR"]') AS VARCHAR[]),
                [{codes_sql}])
        {f'LIMIT {a.limit}' if a.limit else ''}
    """
    df = con.execute(sql).fetchdf()
    print(f"  {len(df)} cosmetics-NIC Udyam firms in {src.name}")

    rows, by_state = [], {}
    for r in df.itertuples(index=False):
        codes = [c for c in json.loads(r.nic_codes or "[]") if c in WANTED]
        all_codes = json.loads(r.nic_codes or "[]")
        gstin = (r.gstin or "").strip().upper() or None
        state = (r.state or "").strip().title() or None
        by_state[state] = by_state.get(state, 0) + 1
        rows.append({
            "source_id": SOURCE, "source_key": r.udyam, "raw_path": str(src),
            "retrieved_at": lib.now(), "tier": TIER,
            "legal_name": (r.name or "").strip() or None,
            "gstin": gstin,
            "constitution": None,     # Udyam does not publish legal form; S16 supplies it
            "address_raw": (r.address or "").strip() or None,
            "pincode": (str(r.pincode) if r.pincode else None),
            "city": (r.district or "").strip().title() or None,
            "state": state,
            "district": (r.district or "").strip().title() or None,
            "roles_claimed": roles_for(codes),
            # The msme/ parquet carries a DERIVED slug (pincode-name-date), not the
            # UDYAM-XX-00-0000000 registration number, so udyam_no stays NULL rather
            # than publishing something that looks like a government identifier and
            # is not one. Registry MEMBERSHIP is the fact, and it is recorded as such.
            "udyam_no": None,
            "payload": lib.jsonb({
                "udyam_registered": True,
                "udyam_local_key": r.udyam,
                "nic_codes_cosmetics": codes,
                "nic_codes_all": all_codes,
                "nic_labels": [NIC_LABEL[c] for c in codes],
                "activity_labels": json.loads(r.activity_labels or "[]"),
                "materials_out": json.loads(r.outputs or "[]"),
                "materials_in": json.loads(r.inputs or "[]"),
                "gst_status": r.gst_status,
                "registered_on": r.reg_date,
                "core_cosmetic": bool(set(codes) & CORE_COSMETIC),
                "packaging_types": sorted({PACKAGING[c][0] for c in codes if c in PACKAGING}),
            }),
        })

    n = lib.upsert("raw_company", rows, "source_id, source_key")
    core = sum(1 for x in rows if json.loads(x["payload"])["core_cosmetic"])
    pack = sum(1 for x in rows if json.loads(x["payload"])["packaging_types"])
    with_gstin = sum(1 for x in rows if x["gstin"])
    print(f"S17 done: {n} Udyam companies — {core} core-cosmetic, {pack} packaging "
          f"converters, {with_gstin} with GSTIN")
    print(f"  states present: {by_state}")
    if len(by_state) == 1:
        print(f"  ! coverage is limited to {list(by_state)[0]}; other states need an "
              f"Udyam extract before the long tail is national")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
