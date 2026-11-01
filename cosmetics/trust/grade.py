"""The evidence grade: how sure we are that a firm is real, and is this firm (TRUST_PLAN §6.1).

Ordered and MONOTONIC — each grade requires every grade below it, which is what the L0-L6
ladder never was (3,408 "corroborated" firms had no identity evidence):

  G4  GST verified      a live GSTN check within 30 days: Active, name and state match
  G3  Corroborated 3+   a registry in force, and >= 3 independent publishers in all
  G2  Corroborated 2    a registry in force, and 2 independent publishers in all
  G1  Registered        one tier-1 registry entry in force
  G0  Listed            in a source, no registry
  flagged (override)    a tier-1 adverse fact, and no registry in force says otherwise

The label describes OUR EVIDENCE, never the supplier. Licence, activity and track record
are facets beside the grade (facets.py), never folded into it.
"""
from __future__ import annotations

from datetime import date

from trust import families

ORDER = ("flagged", "G0", "G1", "G2", "G3", "G4")
SHORT = {"udyam": "Udyam", "mca": "MCA", "cdsco": "CDSCO", "nabl": "NABL",
         "state_fda": "state FDA", "gstn": "GST"}
COUNTED = {"registry_entry", "directory_listing", "gst_verified"}
GST_FRESH_DAYS = 30


def _live(r: dict, today: date) -> bool:
    return r["counts"] and (r["expiry_at"] is None or r["expiry_at"] >= today)


def grade(rows: list[dict], today: date | None = None) -> dict:
    """{code, label, publishers} for one entity's evidence rows."""
    today = today or date.today()
    ident = [r for r in rows if r["facet"] == "identity" and _live(r, today)]
    registries = [r for r in ident if r["signal_type"] in ("registry_entry", "gst_verified")]
    publishers = sorted({r["publisher_family"] for r in ident
                         if r["signal_type"] in COUNTED and r["publisher_family"]})
    adverse = [r for r in rows if r["facet"] == "adverse" and r["tier"] == 1 and r["counts"]]

    if adverse and not registries:
        a = adverse[0]
        return {"code": "flagged", "publishers": publishers,
                "label": a["public_text"].split(" (")[0] if a["public_text"] else "Adverse fact on record"}

    gst = [r for r in ident if r["signal_type"] == "gst_verified" and r["observed_at"]
           and (today - r["observed_at"]).days <= GST_FRESH_DAYS]
    if gst:
        days = (today - gst[0]["observed_at"]).days
        when = "today" if days == 0 else f"{days} d ago"
        return {"code": "G4", "publishers": publishers, "label": f"GST active · checked {when}"}

    if not registries:
        dirs = [r for r in ident if r["signal_type"] == "directory_listing"]
        label = (f"Listed in the {families.display(dirs[0]['publisher_family'])}" if dirs
                 else "Listed only")
        return {"code": "G0", "publishers": publishers, "label": label}

    n = len(publishers)
    if n >= 2:
        return {"code": "G3" if n >= 3 else "G2", "publishers": publishers,
                "label": f"Found in {n} independent sources"}
    fam = registries[0]["publisher_family"]
    return {"code": "G1", "publishers": publishers,
            "label": f"Registered ({SHORT.get(fam, families.display(fam))})"}
