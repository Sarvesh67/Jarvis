"""S3 — MCA company master data (data.gov.in).  Tier 1.

The company spine. 4.06M companies/LLPs; the API filters on exact field values only
(no wildcard, no full-text), so the crawl is partitioned by `registered_state` and the
cosmetics slice is cut LOCALLY on two signals:

  1. the NIC code embedded in the CIN (chars 2-6). MCA CINs carry codes from several
     NIC vintages, so both are accepted: NIC-2008 2023x and its NIC-2004 predecessor
     2424x (the concordance in msme/data/nic2008.csv states 2023 <-> 2424 explicitly),
     plus 4649x/5139x wholesale and 4772x/5231x retail — tagged with role, NOT merged
     into manufacturers (spec §4/S3).
  2. a company-name keyword match, which catches units whose incorporation code was
     assigned to a parent chemical class. These are kept at tier 1 for identity but
     carry `nic_match: false` so downstream never treats the name as a licence claim.

Registered office becomes an ADDRESS ATTRIBUTE, never a Facility — a registered office
is not a factory (spec §6/S3 failure mode). No OPERATES edge originates here.

Rate limits: the documented shared data.gov.in key caps every response at 10 rows.
Put your own free key in cosmetics/.env as DATA_GOV_IN_KEY to lift that.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib  # noqa: E402

SOURCE = "s03_mca"
TIER = 1
RESOURCE = "ec58dab7-d891-4abb-936e-d5d274a6ce9b"
API = f"https://api.data.gov.in/resource/{RESOURCE}"
CKPT = lib.CACHE / "s03_offsets.json"

CIN_RE = re.compile(r"^[LU][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}$")
PIN_RE = re.compile(r"\b[1-9][0-9]{5}\b")

NIC_MANUFACTURE = ("2023", "2424")          # cosmetics/soap/perfume manufacture
NIC_WHOLESALE = ("4649", "5139")            # wholesale toiletry/perfumery/cosmetics
NIC_RETAIL = ("4772", "5231")               # retail perfumery/cosmetics
NAME_KEYWORDS = (
    "COSMETIC", "COSMO", "BEAUTY", "PERSONAL CARE", "SKIN CARE", "SKINCARE", "HAIRCARE",
    "HAIR CARE", "PERFUM", "FRAGRANC", "TOILETRIES", "TOILETRY", "AYURVED", "HERBAL",
    "SOAP", "SHAMPOO", "AROMA", "ESSENTIAL OIL", "DERMA", "SALON", "MAKEUP", "MAKE UP",
)

WEDGE_STATES = ["Himachal Pradesh", "Uttarakhand", "Gujarat", "Maharashtra"]


def nic_from_cin(cin: str) -> str | None:
    return cin[1:6] if cin and CIN_RE.match(cin) else None


def roles_for(nic: str | None) -> list[str]:
    if not nic:
        return []
    p4 = nic[:4]
    if p4 in NIC_MANUFACTURE:
        return ["manufacturer_candidate"]
    if p4 in NIC_WHOLESALE:
        return ["distributor"]
    if p4 in NIC_RETAIL:
        return ["retailer"]
    return []


def is_cosmetics(cin: str, name: str) -> tuple[bool, bool, list[str]]:
    """(keep, nic_match, roles)"""
    nic = nic_from_cin(cin or "")
    roles = roles_for(nic)
    if roles:
        return True, True, roles
    up = (name or "").upper()
    if any(k in up for k in NAME_KEYWORDS):
        return True, False, ["cosmetics_name_candidate"]
    return False, False, []


def _probe_page_size(f: lib.Fetcher) -> int:
    for size in (1000, 500, 100, 10):
        d = f.get(API, params={"api-key": lib.DATA_GOV_KEY, "format": "json",
                               "limit": size, "offset": 0}).json()
        got = len(d.get("records") or [])
        if got >= min(size, 10):
            if got >= size or size == 10:
                return got if got < size else size
    return 10


def to_row(rec: dict, nic_match: bool, roles: list[str]) -> dict:
    cin = (rec.get("corporate_identification_number") or "").strip().upper()
    addr = rec.get("registered_office_address") or ""
    pins = PIN_RE.findall(addr)
    return {
        "source_id": SOURCE, "source_key": cin, "raw_path": "",
        "retrieved_at": lib.now(), "tier": TIER,
        "legal_name": (rec.get("company_name") or "").strip() or None,
        "cin": cin or None,
        "constitution": _constitution(cin, rec.get("company_class")),
        "address_raw": addr or None,
        "pincode": pins[-1] if pins else None,
        "state": (rec.get("registered_state") or "").strip() or None,
        "roles_claimed": roles,
        "payload": lib.jsonb({
            "nic_from_cin": nic_from_cin(cin), "nic_match": nic_match,
            "company_status": rec.get("company_status"),
            "company_class": rec.get("company_class"),
            "company_category": rec.get("company_category"),
            "sub_category": rec.get("sub_category"),
            "authorized_capital": rec.get("authorized_capital"),
            "paidup_capital": rec.get("paidup_capital"),
            "date_of_registration": rec.get("date_of_registration"),
            "roc": rec.get("registrar_of_companies"),
            "principal_business_activity": rec.get("principal_business_activity"),
            "address_is_registered_office": True,   # NEVER a Facility (spec §6/S3)
        }),
    }


def _constitution(cin: str, cls: str | None) -> str | None:
    if not cin or len(cin) < 12:
        return None
    seg = cin[11:14]
    if seg in ("PLC",):
        return "public_ltd"
    if seg in ("PTC",):
        return "pvt_ltd"
    if seg in ("OPC",):
        return "opc"
    if seg in ("LLP", "FLC", "GAP", "NPL", "SGC", "ULL", "ULT"):
        return {"LLP": "llp"}.get(seg, (cls or "").lower() or None)
    return (cls or "").lower() or None


def crawl_state(f: lib.Fetcher, state: str, page_size: int, budget: int,
                offsets: dict) -> tuple[list[dict], int, int]:
    params = {"api-key": lib.DATA_GOV_KEY, "format": "json",
              "filters[registered_state]": state, "limit": page_size}
    d = f.get(API, params={**params, "offset": 0}).json()
    total = int(d.get("total") or 0)
    offset = int(offsets.get(state, 0))
    kept, seen, used = [], 0, 0
    while offset < total and used < budget:
        d = f.get(API, params={**params, "offset": offset}).json()
        recs = d.get("records") or []
        if not recs:
            break
        for rec in recs:
            seen += 1
            cin = (rec.get("corporate_identification_number") or "").strip().upper()
            name = rec.get("company_name") or ""
            keep, nic_match, roles = is_cosmetics(cin, name)
            if keep and cin:
                kept.append(to_row(rec, nic_match, roles))
        offset += len(recs)
        used += 1
        if used % 25 == 0:
            print(f"    {state}: offset {offset}/{total}, {len(kept)} kept "
                  f"({used}/{budget} reqs)", flush=True)
    offsets[state] = offset
    CKPT.write_text(json.dumps(offsets, indent=1))
    return kept, seen, total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--states", nargs="*", default=WEDGE_STATES)
    ap.add_argument("--budget", type=int, default=400, help="max requests per state per run")
    ap.add_argument("--delay", type=float, default=0.4)
    ap.add_argument("--restart", action="store_true", help="ignore saved offsets")
    a = ap.parse_args()

    offsets = {} if a.restart or not CKPT.exists() else json.loads(CKPT.read_text())
    f = lib.Fetcher(SOURCE, delay=a.delay)
    try:
        page_size = _probe_page_size(f)
        print(f"  effective page size: {page_size}"
              f"{'  (SHARED KEY — set DATA_GOV_IN_KEY in cosmetics/.env to lift)' if page_size <= 10 else ''}")
        all_rows: list[dict] = []
        for state in a.states:
            t0 = time.time()
            rows, seen, total = crawl_state(f, state, page_size, a.budget, offsets)
            all_rows.extend(rows)
            print(f"  {state}: scanned {seen}/{total} in {time.time()-t0:.0f}s -> "
                  f"{len(rows)} cosmetics-relevant (offset now {offsets[state]})", flush=True)
    finally:
        f.close()

    n = lib.upsert("raw_company", all_rows, "source_id, source_key")
    nic = sum(1 for r in all_rows if json.loads(r["payload"])["nic_match"])
    print(f"S3 done: {n} companies written ({nic} NIC-matched, {n-nic} name-matched)")
    print(f"  resume state: {json.dumps(offsets)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
