"""Stage 8 — trust levels, recomputed nightly.  Spec §9.

trust_level is COMPUTED, never ingested. The ladder:

  L0 Listed          appears in >= 1 source
  L1 Legally exists  a tier-1 registry entry in force (Udyam; MCA while Active; CDSCO;
                     NABL while in date), or a CIN/GSTIN on record
  L2 Permitted       an active COS-8 / COS-9 covering the category
  L3 Operating       GSTIN active and GSTR-3B filed within 90 days      (needs S16)
  L4 Corroborated    L1, and >= 2 independent PUBLISHERS agree (trust/families.py)
  L5 Confirmed       the company claimed and verified its own profile   (onboarding)
  L6 Vouched         a buyer confirmed a completed transaction          (behavioural)

L2 outranks L1 for this product: a GSTIN says the business is real, but the licence says
it may legally make the thing the buyer wants, and for a factory recommendation the
licence is the binding constraint. So the level awarded is the HIGHEST rung whose
evidence is present, and every rung's evidence is recorded in `reasons` — because the UI
must show the reason, not a gold star. "Active GSTIN, filed GSTR-3B through July 2026"
persuades; a badge does not.

Corroboration counts publishers, not source ids (TRUST_PLAN §6.2, 2026-10-31). Counting
sources made 4,843 firms L4: Udyam counted twice, a firm's own website counted as a second
opinion, and council membership alone counted as corroboration — 3,408 of them had no
identity evidence at all. A dead company's CIN no longer counts as identity either: it is
a tier-1 adverse fact (`adverse_signal`), or a review item when the same entity also has a
live registry entry, since that may be a wrong merge.

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
from pipeline.gstin import is_proprietorship, public_gstin  # noqa: E402
from pipeline.load_neo4j import run  # noqa: E402
from trust import evidence, facets, families, grade  # noqa: E402

COSMETIC_FORMS = {"COS-8", "COS-9"}
# adverse_signal rows trust.py derives from raw_company each run (and so rebuilds); any
# other signal type is written by its own source and only read here.
DERIVED_ADVERSE = {"mca_struck_off", "mca_dissolved", "mca_liquidated"}
_MCA_SIGNAL = {"Strike Off": "mca_struck_off", "Dissolved": "mca_dissolved",
               "Liquidated": "mca_liquidated", "Converted to LLP and dissolved": "mca_dissolved"}


def gather() -> dict[str, dict]:
    ent: dict[str, dict] = defaultdict(lambda: {
        "sources": set(), "records": [], "cin": None, "cin_from_mca": None,
        "gstin": None, "udyam": None,
        "licences": [], "names": set(), "pincodes": set(), "adverse": [],
        "gst_status": None, "last_gstr3b": None, "is_proprietorship": None, "gst": [],
        "onboarded": False, "vouched": False})

    for r in _q("""SELECT x.entity_id, r.source_id, r.source_key, r.retrieved_at, r.cin,
                          r.gstin, r.norm_name, r.pincode, r.tier, r.payload FROM raw_company r
                   JOIN crosswalk x ON x.source_id=r.source_id AND x.source_key=r.source_key
                    AND x.entity_type='company' AND x.superseded_by IS NULL"""):
        e = ent[r["entity_id"]]
        e["sources"].add(r["source_id"])
        p = r["payload"] or {}
        e["records"].append({"source_id": r["source_id"], "source_key": r["source_key"],
                             "retrieved_at": r["retrieved_at"], "payload": p,
                             "cin": r["cin"], "gstin": r["gstin"], "tier": r["tier"],
                             "family": families.family_of(r["source_id"], p),
                             "in_force": families.registry_in_force(r["source_id"], p)})
        if r["source_id"] == "s03_mca" and r["cin"]:
            # the register's own status decides whether this CIN still means anything
            e["cin_from_mca"] = p.get("company_status")
        e["cin"] = e["cin"] or r["cin"]
        e["gstin"] = e["gstin"] or r["gstin"]
        if r["norm_name"]:
            e["names"].add(r["norm_name"])
        if r["pincode"]:
            e["pincodes"].add(r["pincode"])
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

    # The latest LIVE check per GSTIN. A stub or vendor-cache record verifies nothing
    # (TRUST_PLAN §2.5, stub_never_verifies), so it is not even read here.
    for r in _q("""SELECT DISTINCT ON (gstin) id, entity_id, gstin, status, last_gstr3b_period,
                          is_proprietorship, verified_at, provider, provider_class,
                          name_match, state_match
                   FROM gst_record WHERE provider_class = 'live_gstn'
                   ORDER BY gstin, verified_at DESC"""):
        e = ent[r["entity_id"]]
        e["gst_status"] = r["status"]
        e["last_gstr3b"] = r["last_gstr3b_period"]
        e["is_proprietorship"] = r["is_proprietorship"]
        e["gst"].append(r)

    # Without a verification record, the GSTIN still says whose PAN it carries (char 6).
    # Leaving this unset is how 12 proprietors' PANs reached the public reasons.
    for e in ent.values():
        if e["is_proprietorship"] is None and e["gstin"]:
            e["is_proprietorship"] = is_proprietorship(e["gstin"])

    for r in _q("SELECT entity_id, signal_type, detail FROM adverse_signal"):
        if r["signal_type"] not in DERIVED_ADVERSE:      # those are re-derived below
            ent[r["entity_id"]]["adverse"].append(r["signal_type"])
    for e in ent.values():
        e["mca_adverse"], e["mca_review"] = _mca_status(e)
    return ent


def _mca_status(e: dict) -> tuple[dict | None, dict | None]:
    """A dead company on the MCA register: (adverse fact, review item) — at most one.

    If the entity also has a registry entry that is in force (Udyam, CDSCO, NABL), the
    struck-off CIN and the live firm may be two firms merged on name; publishing
    "Struck off" on a live firm would be the worst error this layer can make, so it goes
    to review instead (TRUST_PLAN §6.1).
    """
    dead = [r for r in e["records"] if r["source_id"] == "s03_mca"
            and r["payload"].get("company_status") in families.MCA_DEAD]
    if not dead:
        return None, None
    d = dead[0]
    status = d["payload"]["company_status"]
    live = [r for r in e["records"] if r["in_force"] and r is not d]
    if live:
        return None, {"left": f"s03_mca|{d['source_key']}",
                      "right": f"{live[0]['source_id']}|{live[0]['source_key']}",
                      "status": status}
    as_of = d["retrieved_at"].date().isoformat() if d["retrieved_at"] else None
    return {"signal_type": _MCA_SIGNAL[status], "cin": e["cin"], "status": status,
            "shown": families.MCA_DEAD[status], "as_of": as_of,
            "source_key": d["source_key"]}, None


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
    in_force = sorted({r["family"] for r in e["records"] if r["in_force"] and r["family"]})
    # a CIN counts unless MCA itself says the company is no longer Active
    cin_ok = e["cin"] and e["cin_from_mca"] in (None, *families.MCA_LIVE)
    if cin_ok:
        ids.append(f"CIN {e['cin']}" + (" (MCA: Active)" if e["cin_from_mca"] else ""))
    if e["gstin"]:
        # reasons are public: a GSTIN on a personal PAN shows with the PAN part masked
        shown = public_gstin(e["gstin"])
        ids.append(f"GSTIN {shown}" + (" (PAN withheld as personal data)"
                                       if shown != e["gstin"] else ""))
    if e["udyam"]:
        # never print the derived local key as if it were a registration number
        ids.append("registered on the Udyam MSME registry")
    for fam in in_force:
        if fam not in ("udyam", "mca"):
            ids.append(f"on the {families.display(fam)}")
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

    # Independent publishers that agree this firm exists: registries in force plus
    # third-party directories. The firm's own site is never one (families.family_of).
    pubs = sorted({r["family"] for r in e["records"] if r["family"]
                   and (r["in_force"] or families.kind_of(r["source_id"]) == "directory")})
    if 1 in rungs and len(pubs) >= 2:
        level = max(level, 4); rungs.append(4)
        reasons.append(f"Corroborated — {len(pubs)} independent publishers agree: "
                       + ", ".join(families.display(f) for f in pubs))

    if e["onboarded"]:
        level = max(level, 5); rungs.append(5)
        reasons.append("Profile claimed and verified by the company")
    if e["vouched"]:
        level = max(level, 6); rungs.append(6)
        reasons.append("A buyer confirmed a completed transaction")

    if e.get("mca_adverse"):
        a = e["mca_adverse"]
        # a fact with its source and date, never an adjective (TRUST_PLAN §7.4)
        reasons.append(f"MCA status: {a['shown']}, as of {a['as_of']} "
                       "(MCA company register via data.gov.in)")
        demotions.append(a["signal_type"])
    for a in e["adverse"]:
        demotions.append(a)
    if e["adverse"]:
        level = max(0, level - 1)
        reasons.append(f"DEMOTED by adverse signal(s): {', '.join(sorted(set(e['adverse'])))}")
    return level, reasons, demotions, sorted(set(rungs))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    ent = gather()
    rows, dist, rung_counts = [], defaultdict(int), defaultdict(int)
    ev_rows, grades, facet_counts = [], defaultdict(int), defaultdict(int)
    for eid, e in ent.items():
        lvl, reasons, _, rungs = level_for(e)
        dist[lvl] += 1
        for r in rungs:
            rung_counts[r] += 1
        # T1 (TRUST_PLAN §6): one grade and three facets, read from evidence rows only
        ev = evidence.rows_for(eid, e)
        g = grade.grade(ev)
        fs = facets.all_facets(ev)
        grades[g["code"]] += 1
        for name, f in fs.items():
            facet_counts[(name, f["state"])] += 1
        ev_rows.extend(ev)
        rows.append({"entity_id": eid, "trust_level": lvl, "trust_rungs": rungs,
                     "reasons": lib.jsonb(reasons), "grade": g, "facets": fs,
                     "lines": facets.public_lines(ev)})
    if not a.dry_run:
        with lib.pg() as c, c.cursor() as cur:
            cur.executemany(
                "INSERT INTO trust_state (entity_id, trust_level, trust_rungs, reasons, "
                "trust_grade, facets, computed_at) VALUES (%s,%s,%s,%s,%s,%s, now()) "
                "ON CONFLICT (entity_id) DO UPDATE "
                "SET trust_level=EXCLUDED.trust_level, trust_rungs=EXCLUDED.trust_rungs, "
                "reasons=EXCLUDED.reasons, trust_grade=EXCLUDED.trust_grade, "
                "facets=EXCLUDED.facets, computed_at=EXCLUDED.computed_at",
                [(r["entity_id"], r["trust_level"], r["trust_rungs"], r["reasons"],
                  r["grade"]["code"], lib.jsonb(r["facets"])) for r in rows])
            # trust_evidence is derived from the append-only stores, so it is replaced
            # whole, in one transaction: a reader never sees it half-written
            # (sql/003_trust_evidence.sql). lib.pg() is autocommit, hence the explicit block.
            cols = ("entity_id", "facet", "signal_type", "value", "source_id", "source_key",
                    "publisher_family", "tier", "provenance_class", "visibility",
                    "public_text", "counts", "observed_at", "expiry_at")
            with c.transaction():
                cur.execute("DELETE FROM trust_evidence")
                with cur.copy(f"COPY trust_evidence ({', '.join(cols)}) FROM STDIN") as cp:
                    for r in ev_rows:
                        cp.write_row([json.dumps(r[k], default=str) if k == "value" else r[k]
                                      for k in cols])
            print(f"  {len(ev_rows)} evidence rows -> trust_evidence")
            # trust_state is derived, so an entity superseded by re-resolution must leave
            # it. Upsert-only once kept 4,753 dead entities here, and bi.trust_distribution
            # counted them: 992 phantom L4s.
            cur.execute("DELETE FROM trust_state WHERE NOT (entity_id = ANY(%s))",
                        ([r["entity_id"] for r in rows],))
            if cur.rowcount:
                print(f"  pruned {cur.rowcount} trust rows for entities that no longer exist")
            adverse = [(eid, e["mca_adverse"]) for eid, e in ent.items() if e["mca_adverse"]]
            with c.transaction():                  # derived rows: replaced, atomically
                cur.execute("DELETE FROM adverse_signal WHERE signal_type = ANY(%s)",
                            (sorted(DERIVED_ADVERSE),))
                cur.executemany(
                    "INSERT INTO adverse_signal (entity_id, signal_type, detail, source_id, "
                    "observed_at) VALUES (%s, %s, %s, 's03_mca', %s)",
                    [(eid, a["signal_type"],
                      lib.jsonb({k: a[k] for k in ("cin", "status", "source_key")}),
                      a["as_of"]) for eid, a in adverse])
            review = [e["mca_review"] for e in ent.values() if e["mca_review"]]
            cur.executemany(
                "INSERT INTO review_queue (entity_type, left_key, right_key, features) "
                "SELECT 'company', %s, %s, %s WHERE NOT EXISTS (SELECT 1 FROM review_queue "
                "WHERE left_key=%s AND right_key=%s "
                "AND features->>'reason' = 'adverse_vs_live_registry')",
                [(rv["left"], rv["right"], lib.jsonb({"reason": "adverse_vs_live_registry",
                                                      "mca_status": rv["status"]}),
                  rv["left"], rv["right"]) for rv in review])
            print(f"  {len(adverse)} tier-1 adverse facts (MCA), "
                  f"{len(review)} dead CINs on a live registry entity -> review_queue")
        drv = lib.neo4j_driver()
        with drv.session() as s:
            run(s, """
                UNWIND $rows AS row
                MATCH (c:Company {company_id: row.entity_id})
                SET c.trust_level = row.trust_level,
                    c.trust_rungs = row.trust_rungs,
                    c.trust_reasons = row.reasons,
                    c.trust_grade = row.grade,
                    c.trust_grade_label = row.grade_label,
                    c.trust_publishers = row.publishers,
                    c.trust_licence = row.licence, c.trust_licence_note = row.licence_note,
                    c.trust_activity = row.activity, c.trust_activity_note = row.activity_note,
                    c.trust_track = row.track, c.trust_track_note = row.track_note,
                    c.trust_lines = row.lines,
                    c.trust_computed_at = datetime()
            """, [{"entity_id": r["entity_id"], "trust_level": r["trust_level"],
                   "trust_rungs": r["trust_rungs"],
                   "reasons": json.loads(r["reasons"]),
                   "grade": r["grade"]["code"], "grade_label": r["grade"]["label"],
                   "publishers": len(r["grade"]["publishers"]),
                   **{k: r["facets"][k]["state"] for k in facets.FACETS},
                   **{f"{k}_note": r["facets"][k]["note"] for k in facets.FACETS},
                   "lines": r["lines"]} for r in rows], "Company.trust_grade")
        drv.close()

    total = sum(dist.values())
    print(f"  trust computed for {total} companies")
    print("    highest rung reached:")
    for lvl in sorted(dist):
        print(f"      L{lvl}: {dist[lvl]:5d}  ({100*dist[lvl]/max(total,1):5.1f}%)")
    print("    evidence present at each rung (not exclusive — this is the honest view):")
    for lvl in sorted(rung_counts):
        print(f"      L{lvl}: {rung_counts[lvl]:5d}  ({100*rung_counts[lvl]/max(total,1):5.1f}%)")
    print("    evidence grade (TRUST_PLAN §6.1 — this is what the card shows):")
    for code in grade.ORDER:
        print(f"      {code:7s} {grades[code]:6d}  ({100*grades[code]/max(total,1):5.1f}%)")
    for name in facets.FACETS:
        print(f"    {name:8s} " + "  ".join(f"{st} {facet_counts[(name, st)]}"
              for st in ("verified", "claimed", "unknown", "adverse") if facet_counts[(name, st)]))
    if not any(dist.get(l) for l in (3, 5, 6)):
        print("    (L3 needs a GSTIN verification provider; L5/L6 need onboarding "
              "and buyer confirmations)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
