"""Rung 0 — what a GSTIN says about itself, before anyone pays GSTN to ask.  TRUST_PLAN §3.

A GSTIN is two state digits, a ten-character PAN, a registration number, a literal Z and
a check character. That makes four facts free, and they are checked before any paid
verification is allowed to run:

  checksum       a typo, or a PAN-shaped string next to two digits, fails here
  state          chars 1-2. Must agree with at least one state the record sits in
  holder type    PAN char 4 (GSTIN char 6): C company, F firm/LLP, P individual, H HUF ...
  registrations  GSTIN char 13: this holder's nth registration in that state

Holder type is the one that matters most. A `P` GSTIN is a proprietorship, and chars 3-12
are a natural person's PAN — personal data under DPDP. So `public_gstin()` masks the PAN
part on every surface the agent or the demo can read (Neo4j, trust reasons). Postgres
keeps the full value: verification needs it, and Postgres is where person-grade data lives.

A state mismatch is recorded, never acted on. A head-office GSTIN printed on a branch's
website is normal for a multi-state firm. The record is evidence for a reviewer, and the
reason a paid call should not run blind. A record with no resolvable state is unknown,
not a mismatch (the decisions/0008 rule).

Pure functions plus one stage that writes `gstin_check`. No cosmetics imports in the
functions, so msme/gst.py can use them unchanged.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

B36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")

# GST state codes. 25 (Daman and Diu) was merged into 26 in 2020, but GSTINs issued under
# it are still live. 28 is Andhra Pradesh's pre-bifurcation code. 97 and 99 are not
# places, so they never produce a mismatch.
STATE_CODES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab",
    "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana", "07": "Delhi",
    "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim",
    "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur", "15": "Mizoram",
    "16": "Tripura", "17": "Meghalaya", "18": "Assam", "19": "West Bengal",
    "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh",
    "24": "Gujarat", "25": "Daman and Diu",
    "26": "Dadra and Nagar Haveli and Daman and Diu", "27": "Maharashtra",
    "28": "Andhra Pradesh", "29": "Karnataka", "30": "Goa", "31": "Lakshadweep",
    "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman and Nicobar Islands", "36": "Telangana", "37": "Andhra Pradesh",
    "38": "Ladakh", "97": "Other Territory", "99": "Centre Jurisdiction",
}
NOT_A_PLACE = {"97", "99"}

PAN_TYPES = {
    "C": "company", "F": "firm or LLP", "P": "individual", "H": "HUF",
    "A": "association of persons", "T": "trust", "B": "body of individuals",
    "L": "local authority", "J": "artificial juridical person", "G": "government",
}
# Masked in public. P is a person. H is a family, and its PAN is held in the karta's
# name, so it is treated as personal too: the cost of masking one wrongly is nothing.
PERSONAL_PAN_TYPES = {"P", "H"}

# Spellings that differ between GST, ref_pincode and source rows for the same state.
_STATE_ALIASES = {
    "pondicherry": "puducherry",
    "orissa": "odisha",
    "uttaranchal": "uttarakhand",
    "nct of delhi": "delhi",
    "daman and diu": "dadra and nagar haveli and daman and diu",
    "dadra and nagar haveli": "dadra and nagar haveli and daman and diu",
    # ref_pincode files Ladakh's pincodes under Jammu and Kashmir
    "ladakh": "jammu and kashmir",
}


def checksum_ok(g: str | None) -> bool:
    """GSTIN check character (base-36, alternating weights 1/2)."""
    if not g or len(g) != 15 or any(c not in B36 for c in g):
        return False
    total = 0
    for i, c in enumerate(g[:14]):
        v = B36.index(c) * (1 if i % 2 == 0 else 2)
        total += v // 36 + v % 36
    return B36[(36 - total % 36) % 36] == g[14]


def pan_type(g: str | None) -> str | None:
    return g[5] if g and len(g) == 15 else None


def is_personal(g: str | None) -> bool:
    return pan_type(g) in PERSONAL_PAN_TYPES


def is_personal_pan(pan: str | None) -> bool:
    return bool(pan) and len(pan) == 10 and pan[3] in PERSONAL_PAN_TYPES


def is_proprietorship(g: str | None) -> bool:
    return pan_type(g) == "P"


def mask(g: str) -> str:
    """Hide the PAN (chars 3-12). State code and the last three characters say nothing
    about the person: 24AAAPZ1234C1Z6 -> 24••••••••••1Z6 (synthetic)."""
    return g[:2] + "•" * 10 + g[12:]


def public_gstin(g: str | None) -> str | None:
    """The GSTIN as any public surface may show it."""
    if not g:
        return g
    return mask(g) if is_personal(g) else g


def gst_state(g: str | None) -> str | None:
    return STATE_CODES.get(g[:2]) if g else None


def norm_state(s: str | None) -> str | None:
    if not s:
        return None
    k = re.sub(r"\s+", " ", s.replace("&", " and ").strip().lower())
    return _STATE_ALIASES.get(k, k)


def check(g: str, record_states: set[str] | list[str] | None) -> dict:
    """Every rung-0 fact about one GSTIN, against the states its record is located in."""
    g = (g or "").strip().upper()
    code = g[:2]
    states = {norm_state(s) for s in (record_states or []) if norm_state(s)}
    named = gst_state(g)
    if code in NOT_A_PLACE or not named or not states:
        match = None                      # unknown, not a mismatch
    else:
        match = norm_state(named) in states
    return {
        "gstin": g,
        "well_formed": bool(GSTIN_RE.match(g)),
        "checksum_ok": checksum_ok(g),
        "state_code": code or None,
        "gst_state": named,
        "record_states": sorted(states),
        "state_match": match,
        "pan_type": pan_type(g),
        "is_personal": is_personal(g),
        "is_proprietorship": is_proprietorship(g),
        "registration_no": g[12] if len(g) == 15 else None,
    }


# ---------------------------------------------------------------- stage: gstin_check
def _gather() -> dict[str, dict]:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import lib  # noqa: E402

    with lib.pg() as c, c.cursor() as cur:
        cur.execute("""
            SELECT x.entity_id, r.gstin, r.pincode, r.state, p.state AS pin_state
            FROM raw_company r
            JOIN crosswalk x ON x.source_id = r.source_id AND x.source_key = r.source_key
             AND x.entity_type = 'company' AND x.superseded_by IS NULL
            LEFT JOIN ref_pincode p ON p.pincode = r.pincode
            WHERE x.entity_id IN (
              SELECT x2.entity_id FROM raw_company r2
              JOIN crosswalk x2 ON x2.source_id = r2.source_id AND x2.source_key = r2.source_key
               AND x2.entity_type = 'company' AND x2.superseded_by IS NULL
              WHERE r2.gstin IS NOT NULL AND r2.gstin <> '')""")
        rows = cur.fetchall()
    ent: dict[str, dict] = defaultdict(lambda: {"gstins": set(), "states": set()})
    for eid, gstin, _pin, state, pin_state in rows:
        e = ent[eid]
        if gstin:
            e["gstins"].add(gstin.strip().upper())
        for s in (pin_state, state):      # the pincode's state first; the row's own as well
            if s:
                e["states"].add(s)
    return ent


def main() -> int:
    ap = argparse.ArgumentParser(description="Rung-0 GSTIN checks -> gstin_check")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import lib  # noqa: E402

    results = []
    for eid, e in _gather().items():
        for g in sorted(e["gstins"]):
            r = check(g, e["states"])
            r["entity_id"] = eid
            results.append(r)

    if not a.dry_run and results:
        with lib.pg() as c, c.cursor() as cur:
            cur.executemany("""
                INSERT INTO gstin_check (gstin, entity_id, well_formed, checksum_ok,
                  state_code, gst_state, record_states, state_match, pan_type, is_personal,
                  is_proprietorship, registration_no, checked_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
                ON CONFLICT (gstin, entity_id) DO UPDATE SET
                  well_formed=EXCLUDED.well_formed, checksum_ok=EXCLUDED.checksum_ok,
                  state_code=EXCLUDED.state_code, gst_state=EXCLUDED.gst_state,
                  record_states=EXCLUDED.record_states, state_match=EXCLUDED.state_match,
                  pan_type=EXCLUDED.pan_type, is_personal=EXCLUDED.is_personal,
                  is_proprietorship=EXCLUDED.is_proprietorship,
                  registration_no=EXCLUDED.registration_no, checked_at=EXCLUDED.checked_at
            """, [(r["gstin"], r["entity_id"], r["well_formed"], r["checksum_ok"],
                   r["state_code"], r["gst_state"], r["record_states"], r["state_match"],
                   r["pan_type"], r["is_personal"], r["is_proprietorship"],
                   r["registration_no"]) for r in results])

    n = len(results)
    bad_sum = [r for r in results if not r["checksum_ok"]]
    mism = [r for r in results if r["state_match"] is False]
    unknown = sum(1 for r in results if r["state_match"] is None)
    personal = sum(1 for r in results if r["is_personal"])
    types = defaultdict(int)
    for r in results:
        types[PAN_TYPES.get(r["pan_type"], r["pan_type"] or "?")] += 1
    print(f"  rung 0: {n} GSTINs checked across {len({r['entity_id'] for r in results})} companies")
    print(f"    checksum failures         {len(bad_sum)}")
    print(f"    state code ≠ record state {len(mism)}   (state unknown: {unknown})")
    print(f"    personal PAN (masked)     {personal}")
    print(f"    holder types              {dict(sorted(types.items(), key=lambda kv: -kv[1]))}")
    for r in mism:
        print(f"      {public_gstin(r['gstin'])}  GST says {r['gst_state']}, "
              f"record says {', '.join(r['record_states'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
