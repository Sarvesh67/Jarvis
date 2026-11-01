"""The facts behind a firm's evidence grade and facets (TRUST_PLAN §6, phase T1).

One row per fact, each with its facet, tier, publisher, date and the line a buyer reads.
`grade.py` and `facets.py` read only these rows, so every chip on a card traces to one
(`every_chip_has_evidence`). Rows land in Postgres `trust_evidence`; only the public lines
reach Neo4j.

Input is one entity as `pipeline.trust.gather()` builds it.
"""
from __future__ import annotations

from datetime import date, timedelta

from pipeline.gstin import public_gstin
from trust import families

ACTIVITY_LIFE = timedelta(days=548)     # 18 months (TRUST_PLAN §6.3)
FAIR_LIFE = timedelta(days=365)         # "exhibited within 12 months"
FACETS = ("identity", "adverse", "licence", "activity", "track")
COSMETIC_FORMS = {"COS-8", "COS-9"}


def _row(entity_id, facet, signal, text, *, rec=None, family=None, tier=None, prov=None,
         counts=True, observed=None, expiry=None, value=None, visibility="public") -> dict:
    return {"entity_id": entity_id, "facet": facet, "signal_type": signal,
            "value": value or {}, "source_id": rec["source_id"] if rec else None,
            "source_key": rec["source_key"] if rec else None, "publisher_family": family,
            "tier": tier, "provenance_class": prov, "visibility": visibility,
            "public_text": text if visibility == "public" else None, "counts": counts,
            "observed_at": observed, "expiry_at": expiry}


def _year(ddmmyyyy: str | None) -> str | None:
    d = families._ddmmyyyy(ddmmyyyy)
    return str(d.year) if d else None


def rows_for(entity_id: str, e: dict, today: date | None = None) -> list[dict]:
    today = today or date.today()
    out: list[dict] = []
    add = out.append
    has_mca = any(r["source_id"] == "s03_mca" for r in e["records"])

    for rec in e["records"]:
        sid, p = rec["source_id"], rec["payload"]
        kind = families.kind_of(sid)
        fam = rec["family"]
        seen = rec["retrieved_at"].date() if rec["retrieved_at"] else None
        base = families.by_source()[sid].family if kind else None

        if kind == "registry":
            reg = dict(rec=rec, family=fam, tier=1, prov="registry", observed=seen)
            if base == "udyam":
                since = _year(p.get("registration_date"))
                add(_row(entity_id, "identity", "registry_entry",
                         "Registered on the Udyam MSME registry" + (f" since {since}" if since else ""),
                         **reg))
            elif base == "mca":
                status = p.get("company_status") or "unknown"
                if rec["in_force"]:
                    add(_row(entity_id, "identity", "registry_entry",
                             f"CIN {rec['cin']}: MCA status Active, as of {seen}", **reg))
                    add(_row(entity_id, "activity", "mca_active",
                             f"MCA status Active, as of {seen}", **reg,
                             expiry=seen + ACTIVITY_LIFE if seen else None))
                elif status in families.MCA_DEAD:
                    # Public only as an adverse fact, and only when no live registry says
                    # otherwise (trust.py _mca_status). Here: context, never graded on.
                    add(_row(entity_id, "identity", "registry_lapsed", None, **reg,
                             counts=False, value={"status": status}, visibility="private"))
                else:   # Amalgamated, Converted to LLP: a succession, not a death
                    add(_row(entity_id, "identity", "registry_lapsed",
                             f"CIN {rec['cin']}: MCA status {status}, as of {seen}. "
                             "A succession, so the old CIN adds no identity", **reg,
                             counts=False, value={"status": status}))
            elif base == "nabl":
                cert, till = p.get("certificate_number"), p.get("certificate_valid_till")
                till_d = families._ddmmyyyy(till)
                if rec["in_force"]:
                    add(_row(entity_id, "identity", "registry_entry",
                             f"NABL accreditation {cert}, valid till {till}", **reg, expiry=till_d))
                    ends = [d for d in (till_d, seen + ACTIVITY_LIFE if seen else None) if d]
                    add(_row(entity_id, "activity", "nabl_in_date",
                             f"NABL certificate in date, valid till {till}", **reg,
                             expiry=min(ends) if ends else None))
                    if p.get("tests_cosmetics"):
                        scope = ", ".join(p.get("groups_matched") or []) or "a cosmetics group"
                        add(_row(entity_id, "licence", "nabl_cosmetics_scope",
                                 f"NABL scope covers cosmetics testing: {scope}", **reg, expiry=till_d))
                else:
                    add(_row(entity_id, "identity", "registry_lapsed",
                             f"NABL certificate {cert} expired {till}", **reg, counts=False))
            else:
                add(_row(entity_id, "identity", "registry_entry",
                         f"On the {families.display(fam)}", **reg))

        elif kind == "directory":
            text = (f"Exhibited at {p.get('event')} {p.get('year')}" if base == "fair"
                    else f"Listed in the {families.display(fam)}")
            add(_row(entity_id, "identity", "directory_listing", text, rec=rec, family=fam,
                     tier=rec["tier"], prov="directory", observed=seen))
            if base == "fair" and str(p.get("year") or "").isdigit():
                held = date(int(p["year"]), 1, 1)      # the year is all the list gives
                add(_row(entity_id, "activity", "exhibited", text, rec=rec, family=fam,
                         tier=rec["tier"], prov="directory", observed=held,
                         expiry=held + FAIR_LIFE))

        elif kind == "self":
            # The firm about itself: shown, never graded on, never corroborates (§6.2).
            if rec["gstin"]:
                add(_row(entity_id, "identity", "identifier_claimed",
                         f"GSTIN {public_gstin(rec['gstin'])} shown on the company's own website",
                         rec=rec, tier=3, prov="self", observed=seen, counts=False))
            if rec["cin"] and not has_mca:
                add(_row(entity_id, "identity", "identifier_claimed",
                         f"CIN {rec['cin']} shown on the company's own website",
                         rec=rec, tier=3, prov="self", observed=seen, counts=False))
            if seen:
                add(_row(entity_id, "activity", "website_live",
                         f"Website live, checked {seen:%b %Y}", rec=rec, tier=3, prov="self",
                         observed=seen, expiry=seen + ACTIVITY_LIFE))

    for g in e.get("gst", []):
        if g.get("provider_class") != "live_gstn":     # defence in depth: stub never verifies
            continue
        checked = g["verified_at"].date()
        rec = {"source_id": "s16_gstin", "source_key": str(g["id"])}
        base = dict(rec=rec, family="gstn", tier=1, prov="verification", observed=checked)
        if g["status"] == "Active" and g["name_match"] and g["state_match"] is not False:
            # G4 while fresh; grade.py lets it decay to a plain registry entry after 30 days
            add(_row(entity_id, "identity", "gst_verified",
                     f"GST active, checked {checked} (GST Network via {g['provider']})", **base))
            add(_row(entity_id, "identity", "registry_entry",
                     f"GST registration active, checked {checked}", **base))
            add(_row(entity_id, "activity", "gst_active", f"GST active, checked {checked}",
                     **base, expiry=checked + timedelta(days=30)))
        elif g["status"] == "Active":
            # §7.1: a mismatch is shown as a fact, never a name, and counts for nothing
            add(_row(entity_id, "identity", "gst_mismatch",
                     f"GST record differs from ours (name or place), checked {checked}",
                     **base, counts=False))
        elif g["status"] == "not_found":
            add(_row(entity_id, "identity", "gst_not_found", None, **base, counts=False,
                     visibility="private"))
        else:
            # §11.7 wording: the fact and its date, never the exact status in public
            add(_row(entity_id, "adverse", "gst_not_active",
                     f"GST could not be confirmed active, checked {checked}", **base,
                     value={"status": g["status"]}))

    a = e.get("mca_adverse")
    if a:
        add(_row(entity_id, "adverse", a["signal_type"],
                 f"MCA status: {a['shown']}, as of {a['as_of']} (MCA company register via data.gov.in)",
                 family="mca", tier=1, prov="registry", observed=a["as_of"],
                 value={"cin": a["cin"], "status": a["status"]}))
    for t in e["adverse"]:
        # written by its own source; below tier 1 a signal is a private warning at most
        add(_row(entity_id, "adverse", t, None, prov="registry", visibility="private"))

    for l in e["licences"]:
        if (l["form"] in COSMETIC_FORMS or l["regime"] == "cosmetics") and l["status"] != "cancelled":
            add(_row(entity_id, "licence", "cosmetics_licence",
                     f"{l['form']} licence on the CDSCO register (status and categories "
                     "not published)", family="cdsco", tier=1, prov="registry",
                     expiry=l["expires_on"]))
    return out
