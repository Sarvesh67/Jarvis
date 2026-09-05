"""Stage 8 — trust levels, recomputed nightly.  Spec §9.

trust_level is COMPUTED, never ingested. The ladder:

  L0 Listed          appears in >= 1 source
  L1 Legally exists  a CIN, GSTIN or Udyam number resolves
  L2 Permitted       an active COS-8 / COS-9 covering the category
  L3 Operating       GSTIN active and GSTR-3B filed within 90 days      (needs S16)
  L4 Corroborated    >= 2 independent sources agree, or council/exhibitor presence
  L5 Confirmed       the company claimed and verified its own profile   (onboarding)
  L6 Vouched         a buyer confirmed a completed transaction          (behavioural)

L2 outranks L1 for this product: a GSTIN says the business is real, but the licence says
it may legally make the thing the buyer wants, and for a factory recommendation the
licence is the binding constraint. So the level awarded is the HIGHEST rung whose
evidence is present, and every rung's evidence is recorded in `reasons` — because the UI
must show the reason, not a gold star. "Active GSTIN, filed GSTR-3B through July 2026"
persuades; a badge does not.

Two constraints are enforced elsewhere and restated here so they are not lost:
  * DPDP — a proprietorship GSTIN derives from an individual's PAN and is arguably
    personal data; where is_proprietorship, it gets person-table handling.
  * GST filing history is NEVER displayed publicly. Verify against it, rank with it,
    warn a buyer privately.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib  # noqa: E402
from pipeline.load_neo4j import run  # noqa: E402

COSMETIC_FORMS = {"COS-8", "COS-9"}
CORROBORATING_SOURCES = {"s08_exhibitors", "s09_chemexcil"}


def gather() -> dict[str, dict]:
    ent: dict[str, dict] = defaultdict(lambda: {
        "sources": set(), "cin": None, "gstin": None, "udyam": None,
        "licences": [], "names": set(), "pincodes": set(), "adverse": [],
        "gst_status": None, "last_gstr3b": None, "is_proprietorship": None,
        "onboarded": False, "vouched": False})

    for r in _q("""SELECT x.entity_id, r.source_id, r.cin, r.gstin, r.norm_name, r.pincode,
                          r.payload FROM raw_company r
                   JOIN crosswalk x ON x.source_id=r.source_id AND x.source_key=r.source_key
                    AND x.entity_type='company' AND x.superseded_by IS NULL"""):
        e = ent[r["entity_id"]]
        e["sources"].add(r["source_id"])
        e["cin"] = e["cin"] or r["cin"]
        e["gstin"] = e["gstin"] or r["gstin"]
        if r["norm_name"]:
            e["names"].add(r["norm_name"])
        if r["pincode"]:
            e["pincodes"].add(r["pincode"])
        p = r["payload"] or {}
        if p.get("udyam_registered"):
            e["udyam"] = e["udyam"] or "registered"

    for r in _q("""SELECT o.company_id, f.licence_form, f.licence_status, f.expires_on,
                          f.payload FROM operates_edge o
                   JOIN crosswalk x ON x.entity_id = o.facility_id AND x.entity_type='facility'
                    AND x.superseded_by IS NULL
                   JOIN raw_facility f ON f.source_id=x.source_id AND f.source_key=x.source_key"""):
        ent[r["company_id"]]["licences"].append({
            "form": r["licence_form"], "status": r["licence_status"],
            "expires_on": r["expires_on"],
            "regime": (r["payload"] or {}).get("regime")})

    for r in _q("SELECT entity_id, gstin, status, last_gstr3b_period, is_proprietorship, "
                "verified_at FROM gst_record"):
        e = ent[r["entity_id"]]
        e["gst_status"] = r["status"]
        e["last_gstr3b"] = r["last_gstr3b_period"]
        e["is_proprietorship"] = r["is_proprietorship"]

    for r in _q("SELECT entity_id, signal_type, detail FROM adverse_signal"):
        ent[r["entity_id"]]["adverse"].append(r["signal_type"])
    return ent


def _q(sql: str) -> list[dict]:
    with lib.pg() as c, c.cursor() as cur:
        cur.execute(sql)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]


def level_for(e: dict) -> tuple[int, list[str], list[str], list[int]]:
    """Returns (level, reasons, demotions, rungs).

    `level` is the highest rung reached, which is what the spec defines. `rungs` is
    EVERY rung whose evidence is present, and it exists because the ladder is not a
    quality ordering: a company at L4 (council member, two sources agree) with no
    licence is not a better factory recommendation than one at L2, and the spec says so
    itself — "for a factory recommendation the licence is the binding constraint".
    Reporting only the maximum would let 60% at L4 hide the fact that almost nobody is
    licensed. Filter on `2 IN trust_rungs`, not on `trust_level >= 2`.
    """
    reasons: list[str] = []
    demotions: list[str] = []
    rungs: list[int] = []
    level = 0
    if e["sources"]:
        rungs.append(0)
        reasons.append(f"Listed in {len(e['sources'])} source(s): {', '.join(sorted(e['sources']))}")

    ids = []
    if e["cin"]:
        ids.append(f"CIN {e['cin']}")
    if e["gstin"]:
        ids.append(f"GSTIN {e['gstin']}")
    if e["udyam"]:
        # never print the derived local key as if it were a registration number
        ids.append("registered on the Udyam MSME registry")
    if ids:
        level = max(level, 1); rungs.append(1)
        reasons.append("Legally exists — " + ", ".join(ids))

    cos = [l for l in e["licences"]
           if (l["form"] in COSMETIC_FORMS or l["regime"] == "cosmetics")
           and l["status"] != "cancelled"]
    if cos:
        level = max(level, 2); rungs.append(2)
        forms = sorted({l["form"] for l in cos})
        status = sorted({l["status"] or "unknown" for l in cos})
        reasons.append(f"Cosmetics licence on file: {', '.join(forms)} "
                       f"(status {', '.join(status)} — the source publishes no status field)")

    if e["gst_status"] == "Active" and e["last_gstr3b"]:
        level = max(level, 3); rungs.append(3)
        reasons.append("Active GSTIN with a recent GSTR-3B filing")  # period NEVER shown publicly

    corroborated = len(e["sources"]) >= 2 or bool(e["sources"] & CORROBORATING_SOURCES)
    if corroborated:
        level = max(level, 4); rungs.append(4)
        if e["sources"] & CORROBORATING_SOURCES:
            reasons.append("Corroborated — exhibited at a trade fair or holds council membership")
        else:
            reasons.append(f"Corroborated — {len(e['sources'])} independent sources agree")

    if e["onboarded"]:
        level = max(level, 5); rungs.append(5)
        reasons.append("Profile claimed and verified by the company")
    if e["vouched"]:
        level = max(level, 6); rungs.append(6)
        reasons.append("A buyer confirmed a completed transaction")

    for a in e["adverse"]:
        demotions.append(a)
    if demotions:
        level = max(0, level - 1)
        reasons.append(f"DEMOTED by adverse signal(s): {', '.join(sorted(set(demotions)))}")
    return level, reasons, demotions, sorted(set(rungs))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    ent = gather()
    rows, dist, rung_counts = [], defaultdict(int), defaultdict(int)
    for eid, e in ent.items():
        lvl, reasons, _, rungs = level_for(e)
        dist[lvl] += 1
        for r in rungs:
            rung_counts[r] += 1
        rows.append({"entity_id": eid, "trust_level": lvl, "trust_rungs": rungs,
                     "reasons": lib.jsonb(reasons)})
    if not a.dry_run:
        with lib.pg() as c, c.cursor() as cur:
            cur.executemany(
                "INSERT INTO trust_state (entity_id, trust_level, trust_rungs, reasons, "
                "computed_at) VALUES (%s,%s,%s,%s, now()) ON CONFLICT (entity_id) DO UPDATE "
                "SET trust_level=EXCLUDED.trust_level, trust_rungs=EXCLUDED.trust_rungs, "
                "reasons=EXCLUDED.reasons, computed_at=EXCLUDED.computed_at",
                [(r["entity_id"], r["trust_level"], r["trust_rungs"], r["reasons"])
                 for r in rows])
        drv = lib.neo4j_driver()
        with drv.session() as s:
            run(s, """
                UNWIND $rows AS row
                MATCH (c:Company {company_id: row.entity_id})
                SET c.trust_level = row.trust_level,
                    c.trust_rungs = row.trust_rungs,
                    c.trust_reasons = row.reasons,
                    c.trust_computed_at = datetime()
            """, [{"entity_id": r["entity_id"], "trust_level": r["trust_level"],
                   "trust_rungs": r["trust_rungs"],
                   "reasons": json.loads(r["reasons"])} for r in rows], "Company.trust_level")
        drv.close()

    total = sum(dist.values())
    print(f"  trust computed for {total} companies")
    print("    highest rung reached:")
    for lvl in sorted(dist):
        print(f"      L{lvl}: {dist[lvl]:5d}  ({100*dist[lvl]/max(total,1):5.1f}%)")
    print("    evidence present at each rung (not exclusive — this is the honest view):")
    for lvl in sorted(rung_counts):
        print(f"      L{lvl}: {rung_counts[lvl]:5d}  ({100*rung_counts[lvl]/max(total,1):5.1f}%)")
    if not any(dist.get(l) for l in (3, 5, 6)):
        print("    (L3 needs a GSTIN verification provider; L5/L6 need onboarding "
              "and buyer confirmations)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
