"""Validation — runs on every load.  Spec §15.

Prints, and returns non-zero on, four classes of failure:
  1. referential   — orphans, unresolved links, rules without jurisdiction
  2. false-merge   — one entity carrying two distinct CINs (hard fail)
  3. jurisdiction  — an India-scoped path returning an EU-only rule
  4. golden        — the query suite in golden_queries.yaml

And prints the COVERAGE TABLE, which is not a pass/fail — it is the honest number to
put in front of a customer (spec §15.2). A low cell is a data-sourcing task, stated
plainly, not something to paper over.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib  # noqa: E402

GOLDEN = Path(__file__).resolve().parent / "golden_queries.yaml"

COVERAGE_FIELDS = [
    ("identifier or registry membership",
     "c.cin IS NOT NULL OR size(coalesce(c.gstins,[])) > 0 OR c.udyam_no IS NOT NULL "
     "OR c.udyam_registered = true"),
    ("verified identifier (CIN or GSTIN)",
     "c.cin IS NOT NULL OR size(coalesce(c.gstins,[])) > 0"),
    ("pincode", "c.pincode IS NOT NULL"),
    ("cluster_id", "c.cluster_id IS NOT NULL"),
    ("district", "c.district IS NOT NULL"),
    ("a licensed facility", "EXISTS { (c)-[:OPERATES]->(:Facility)-[:LICENSED_UNDER]->() }"),
    ("a COSMETICS licence", "EXISTS { (c)-[:OPERATES]->(:Facility)-[:LICENSED_UNDER]->"
                            "(l:Licence) WHERE l.regime='cosmetics' }"),
    ("licence categories stated",
     "EXISTS { (c)-[:OPERATES]->(:Facility)-[:LICENSED_UNDER]->(:Licence)-[:PERMITS]->() }"),
    ("website", "c.website IS NOT NULL"),
    ("business email or phone", "c.email IS NOT NULL OR c.phone IS NOT NULL"),
    ("a claimed capability", "EXISTS { (c)-[:CLAIMS_CAPABILITY]->() }"),
    ("a claimed certification", "EXISTS { (c)-[:CLAIMS_CERTIFICATION]->() }"),
    ("supplies a known ingredient", "EXISTS { (c)-[:SUPPLIES]->() }"),
    ("trust_level", "c.trust_level IS NOT NULL"),
    # ask for the rung, not the maximum: L4 (council/exhibitor corroboration) outranks
    # L2 numerically but says nothing about whether the company may legally make the thing
    ("L2 evidence (a cosmetics licence)", "2 IN c.trust_rungs"),
    ("L4 evidence (corroborated)", "4 IN c.trust_rungs"),
]


def coverage(s) -> None:
    total = s.run("MATCH (c:Company) RETURN count(c) AS n").single()["n"]
    wedge = s.run("MATCH (c:Company)-[:LOCATED_IN]->(:Cluster {wedge:true}) "
                  "RETURN count(DISTINCT c) AS n").single()["n"]
    print(f"\n  COVERAGE — {total} companies ({wedge} in a wedge cluster)")
    print(f"    {'field':38s} {'all':>12s} {'wedge':>12s}")
    for label, pred in COVERAGE_FIELDS:
        a = s.run(f"MATCH (c:Company) WHERE {pred} RETURN count(c) AS n").single()["n"]
        w = s.run(f"MATCH (c:Company)-[:LOCATED_IN]->(:Cluster {{wedge:true}}) "
                  f"WHERE {pred} RETURN count(DISTINCT c) AS n").single()["n"]
        print(f"    {label:38s} {a:6d} {100*a/max(total,1):5.1f}% "
              f"{w:6d} {100*w/max(wedge,1):5.1f}%")


def false_merge() -> list[str]:
    with lib.pg() as c, c.cursor() as cur:
        cur.execute("""
            SELECT x.entity_id, array_agg(DISTINCT r.cin) cins
            FROM crosswalk x JOIN raw_company r
              ON r.source_id = x.source_id AND r.source_key = x.source_key
            WHERE x.entity_type='company' AND x.superseded_by IS NULL AND r.cin IS NOT NULL
            GROUP BY x.entity_id HAVING count(DISTINCT r.cin) > 1
        """)
        return [f"{r[0]}: {r[1]}" for r in cur.fetchall()]


def golden(s) -> tuple[int, int, list[str]]:
    import yaml

    specs = yaml.safe_load(GOLDEN.read_text())
    passed, failed, notes = 0, 0, []
    print("\n  GOLDEN QUERIES")
    for spec in specs:
        recs = list(s.run(spec["cypher"]))
        if "expect_zero" in spec:
            key = spec["expect_zero"]
            n = recs[0][key] if recs else 0
            ok = n == 0
            detail = f"{n} violation(s)"
        else:
            n = len(recs)
            if n == 1 and len(recs[0].keys()) == 1 and isinstance(recs[0][0], int):
                n = recs[0][0]
            ok = n >= spec.get("expect_min", 0)
            detail = f"{n} row(s)"
            if "expect_contains" in spec:
                flat = " ".join(str(v) for r in recs for v in r.values()).upper()
                for want in spec["expect_contains"]:
                    if want.upper() not in flat:
                        ok = False
                        detail += f"; missing {want}"
        mark = "PASS" if ok else "FAIL"
        passed, failed = (passed + ok, failed + (not ok))
        print(f"    [{mark}] {spec['id']:42s} {detail}")
        if not ok and spec.get("coverage_gap"):
            notes.append(f"{spec['id']}: {spec['coverage_gap'].strip()}")
        elif ok and n == 0 and spec.get("coverage_gap"):
            notes.append(f"{spec['id']} (empty, known gap): {spec['coverage_gap'].strip()}")
    return passed, failed, notes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--coverage-only", action="store_true")
    a = ap.parse_args()

    drv = lib.neo4j_driver()
    rc = 0
    with drv.session() as s:
        coverage(s)
        if a.coverage_only:
            drv.close()
            return 0

        print("\n  FALSE-MERGE DETECTOR")
        bad = false_merge()
        if bad:
            print(f"    [FAIL] {len(bad)} entity(ies) carry two distinct CINs")
            for b in bad[:5]:
                print(f"      {b}")
            rc = 1
        else:
            print("    [PASS] no entity carries two distinct CINs")

        passed, failed, notes = golden(s)
        print(f"\n  {passed} passed, {failed} failed")
        if notes:
            print("\n  KNOWN COVERAGE GAPS (thin answers, not silent ones)")
            for n in notes:
                print(f"    - {n}")
        rc = rc or (1 if failed else 0)
    drv.close()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
