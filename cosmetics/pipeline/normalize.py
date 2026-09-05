"""Stage 2 — normalization.  Runs over raw_company and raw_facility in place.

Spec §7. Three jobs:
  7.1 company names -> norm_name (legal_name is NEVER overwritten)
  7.2 addresses     -> pincode -> district/state -> cluster_id, no full address parsing
  7.3 identifiers   -> validate, and derive PAN from GSTIN (chars 3-12).
      CIN does NOT encode PAN — the v1 spec error. No CIN -> PAN derivation exists here.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib  # noqa: E402

# --- 7.1 -------------------------------------------------------------------------
SUFFIXES = ["PRIVATE LIMITED", "PVT LTD", "PVT. LTD.", "PRIVATE LTD", "LIMITED", "LTD",
            "LLP", "LIMITED LIABILITY PARTNERSHIP", "AND COMPANY", "& COMPANY", "& CO",
            "AND CO", "CO.", "INCORPORATED", "INC", "OPC", "HUF"]
ABBREV = {"INDUSTRIES": "INDS", "INTERNATIONAL": "INTL", "LABORATORIES": "LABS",
          "LABORATORY": "LABS", "PHARMACEUTICALS": "PHARMA", "ENTERPRISES": "ENT",
          "MANUFACTURING": "MFG", "PRODUCTS": "PROD", "CHEMICALS": "CHEM"}
_SUFFIXES = sorted(SUFFIXES, key=len, reverse=True)   # longest-match first


def norm_name(name: str | None) -> str | None:
    if not name:
        return None
    s = name.upper()
    s = re.sub(r"[^A-Z0-9&\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    changed = True
    while changed:                       # repeat until stable
        changed = False
        for suf in _SUFFIXES:
            suf_clean = re.sub(r"[^A-Z0-9&\s]", " ", suf)
            suf_clean = re.sub(r"\s+", " ", suf_clean).strip()
            if s.endswith(" " + suf_clean) or s == suf_clean:
                s = s[: len(s) - len(suf_clean)].strip()
                changed = True
    s = " ".join(ABBREV.get(w, w) for w in s.split())
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


# --- 7.2 -------------------------------------------------------------------------
PIN_RE = re.compile(r"\b[1-9][0-9]{5}\b")


def extract_pincode(address: str | None) -> str | None:
    """Indian addresses end with the pincode, so take the LAST match."""
    if not address:
        return None
    m = PIN_RE.findall(address)
    return m[-1] if m else None


def build_cluster_index() -> tuple[dict[str, str], list[tuple[str, str]]]:
    by_district: dict[str, str] = {}
    by_prefix: list[tuple[str, str]] = []
    for cid, c in lib.clusters().items():
        for d in c.get("districts") or []:
            by_district[d.strip().lower()] = cid
        for p in c.get("pincode_prefixes") or []:
            by_prefix.append((str(p), cid))
    by_prefix.sort(key=lambda x: -len(x[0]))    # most specific prefix wins
    return by_district, by_prefix


def resolve_cluster(district: str | None, pincode: str | None,
                    by_district: dict, by_prefix: list) -> str | None:
    if district:
        cid = by_district.get(district.strip().lower())
        if cid:
            return cid
    if pincode:
        for prefix, cid in by_prefix:
            if pincode.startswith(prefix):
                return cid
    return None


# --- 7.3 -------------------------------------------------------------------------
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[A-Z0-9]{1}Z[A-Z0-9]{1}$")
CIN_RE = re.compile(r"^[LU][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}$")
PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
UDYAM_RE = re.compile(r"^UDYAM-[A-Z]{2}-[0-9]{2}-[0-9]{7}$")


def pan_from_gstin(gstin: str | None) -> str | None:
    """GSTIN chars 3-12 are the PAN. Valid derivation. (CIN -> PAN is NOT.)"""
    if gstin and GSTIN_RE.match(gstin):
        pan = gstin[2:12]
        return pan if PAN_RE.match(pan) else None
    return None


def clean_id(value: str | None, pattern: re.Pattern) -> str | None:
    if not value:
        return None
    v = re.sub(r"\s+", "", value).upper()
    return v if pattern.match(v) else None


# --- driver ----------------------------------------------------------------------
def normalize_table(table: str, name_col: str, pins: dict, by_district: dict,
                    by_prefix: list, dry: bool) -> dict:
    stats = {"rows": 0, "named": 0, "pinned": 0, "geo": 0, "clustered": 0, "pan": 0}
    with lib.pg() as c, c.cursor() as cur:
        cur.execute(f"SELECT id, {name_col}, address_raw, pincode, state, district FROM {table}")
        rows = cur.fetchall()
        updates = []
        for rid, raw_name, address, pincode, state, district in rows:
            stats["rows"] += 1
            nn = norm_name(raw_name)
            if nn:
                stats["named"] += 1
            pin = pincode or extract_pincode(address)
            if pin:
                stats["pinned"] += 1
            ref = pins.get(pin) if pin else None
            d = (ref or {}).get("district") or district
            st = (ref or {}).get("state") or state
            geo = "high" if ref else ("medium" if pin else ("low" if st else "none"))
            if ref:
                stats["geo"] += 1
            cid = resolve_cluster(d, pin, by_district, by_prefix)
            if cid:
                stats["clustered"] += 1
            updates.append((nn, pin, d, st, cid, geo, rid))
        if not dry:
            cur.executemany(
                f"UPDATE {table} SET norm_name=%s, pincode=%s, district=%s, state=%s, "
                f"cluster_id=%s, geo_confidence=%s WHERE id=%s", updates)
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    with lib.pg() as c, c.cursor() as cur:
        cur.execute("SELECT pincode, district, state FROM ref_pincode")
        pins = {r[0]: {"district": r[1], "state": r[2]} for r in cur.fetchall()}
    by_district, by_prefix = build_cluster_index()
    print(f"  {len(pins)} pincodes, {len(by_district)} district aliases, "
          f"{len(by_prefix)} pincode prefixes")

    for table, col in (("raw_company", "legal_name"), ("raw_facility", "firm_name")):
        s = normalize_table(table, col, pins, by_district, by_prefix, a.dry_run)
        pct = lambda n: f"{100*n/max(s['rows'],1):.1f}%"
        print(f"  {table}: {s['rows']} rows | norm_name {pct(s['named'])} | "
              f"pincode {pct(s['pinned'])} | geo-resolved {pct(s['geo'])} | "
              f"cluster {pct(s['clustered'])}")

    # identifier hygiene: derive PAN from GSTIN where a GSTIN exists
    if not a.dry_run:
        with lib.pg() as c, c.cursor() as cur:
            cur.execute("SELECT id, gstin FROM raw_company WHERE gstin IS NOT NULL AND gstin <> ''")
            ups = [(pan_from_gstin(g), i) for i, g in cur.fetchall()]
            ups = [(p, i) for p, i in ups if p]
            if ups:
                cur.executemany("UPDATE raw_company SET pan=%s WHERE id=%s", ups)
            print(f"  PAN derived from GSTIN for {len(ups)} companies")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
