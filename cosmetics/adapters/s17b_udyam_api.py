"""S17b — Udyam MSME registry, NATIONAL, via data.gov.in.  Tier 1.

Replaces the geographic limit of s17_udyam, which reads a Gujarat-only parquet extract
and therefore leaves Baddi, Haridwar and Palghar — three of the four wedge clusters —
with zero long-tail and zero packaging coverage.

Resource `8b68ae56-...` is the full registry: 43,938,718 records carrying State,
District, Pincode, EnterpriseName, CommunicationAddress, RegistrationDate and an
`Activities` array of NIC-5 codes. That last field is what makes it usable — it is the
same NIC vocabulary s17_udyam already classifies on, so the role logic is imported rather
than re-derived.

WHY PINCODE IS THE PARTITION KEY
The API caps every distinct query at 10,000 rows (the same Elasticsearch window as MCA and
CosIng), so 43.9M records cannot be paged. District is too coarse — Surat alone is 560,180
records, Thane 684,886. Pincode is the only field that partitions the registry into buckets
that mostly fit: Baddi (173205) is 6,280. Pincodes are globally unique, so no district
filter is needed alongside. Dense urban pincodes still overflow (Surat 395006 is 36,744)
and are reported as TRUNCATED rather than silently half-read.

WHY THE NIC FILTER IS LOCAL
There is no server-side activity filter: `filters[Activities]` matches the whole JSON blob
and returns nothing, and `q=` is ignored entirely (it returns the unfiltered 43.9M). So the
whole bucket is pulled and the cosmetics/packaging slice is cut here. The yield is low by
design — most MSMEs are not cosmetics — and that is the price of the registry having no
activity index.

NOTE ON IDENTITY: this dataset does NOT publish the Udyam registration number, so
`udyam_no` stays null and the source key is a hash of name+pincode+registration date+
address. Resolution treats these rows like any other name+pincode evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402
from s17_udyam import (CORE_COSMETIC, NIC_LABEL, PACKAGING, WANTED,  # noqa: E402
                       roles_for)

SOURCE = "s17b_udyam_api"
TIER = 1
RESOURCE = "8b68ae56-84cf-4728-a0a6-1be11028dea7"
API = f"https://api.data.gov.in/resource/{RESOURCE}"
WINDOW = 10000
CKPT = lib.CACHE / "s17b_pincodes.json"


def target_pincodes(scope: str, explicit: list[str] | None) -> list[dict]:
    """Pincodes to sweep, from ref_pincode joined to the cluster map."""
    if explicit:
        return [{"pincode": p, "district": None, "state": None} for p in explicit]
    clusters = lib.clusters()
    wanted_districts, wanted_prefixes = set(), []
    for cid, c in clusters.items():
        if scope == "wedge" and not c.get("wedge"):
            continue
        wanted_districts |= {d.lower() for d in (c.get("districts") or [])}
        wanted_prefixes += [str(p) for p in (c.get("pincode_prefixes") or [])]
    with lib.pg() as conn, conn.cursor() as cur:
        cur.execute("SELECT pincode, district, state FROM ref_pincode ORDER BY pincode")
        rows = [{"pincode": r[0], "district": r[1], "state": r[2]} for r in cur.fetchall()]
    out = []
    for r in rows:
        d = (r["district"] or "").lower()
        if d in wanted_districts or any(r["pincode"].startswith(p) for p in wanted_prefixes):
            out.append(r)
    return out


def nic_codes(activities_raw: str | None) -> tuple[list[str], list[str]]:
    """(codes, descriptions) from the Activities JSON array."""
    if not activities_raw:
        return [], []
    try:
        acts = json.loads(activities_raw)
    except Exception:
        return [], []
    codes, descs = [], []
    for a in acts if isinstance(acts, list) else []:
        c = str(a.get("NIC5DigitId") or "").strip()
        if c:
            codes.append(c)
            descs.append(str(a.get("Description") or "").strip())
    return codes, descs


def to_row(rec: dict) -> dict | None:
    codes, descs = nic_codes(rec.get("Activities"))
    keep = [c for c in codes if c in WANTED]
    if not keep:
        return None
    name = (rec.get("EnterpriseName") or "").strip()
    if not name:
        return None
    pin = (rec.get("Pincode") or "").strip()
    if pin.endswith(".0"):
        pin = pin[:-2]
    addr = (rec.get("CommunicationAddress") or "").strip()
    seed = f"{name}|{pin}|{rec.get('RegistrationDate')}|{addr[:80]}"
    return {
        "source_id": SOURCE,
        "source_key": hashlib.blake2b(seed.encode(), digest_size=10).hexdigest(),
        "raw_path": "", "retrieved_at": lib.now(), "tier": TIER,
        "legal_name": name,
        "address_raw": addr or None,
        "pincode": pin or None,
        "state": (rec.get("State") or "").strip().title() or None,
        "roles_claimed": roles_for(codes),
        "payload": lib.jsonb({
            "nic_codes": codes,
            "nic_matched": keep,
            "nic_descriptions": descs[:6],
            "nic_labels": [NIC_LABEL[c] for c in keep if c in NIC_LABEL],
            "core_cosmetic": bool(set(codes) & CORE_COSMETIC),
            "packaging_types": sorted({PACKAGING[c][0] for c in codes if c in PACKAGING}),
            "registration_date": rec.get("RegistrationDate"),
            "district_lgd": rec.get("District"),
            # Registry MEMBERSHIP is the fact, as in s17_udyam — trust.py reads this flag
            # for rung 1. Without it 119,120 registered firms sat at L0.
            "udyam_registered": True,
            "udyam_no_published": False,   # this dataset omits it (see module docstring)
        }),
    }


def mark_registered() -> int:
    """Stamp `udyam_registered` on rows written before to_row() set it.

    The sweep never landed its API responses under raw/ (raw_path is ""), so the rows
    cannot be rebuilt from disk, and re-sweeping 119k rows to set one constant is waste.
    This writes exactly what the corrected to_row() writes, and nothing else. Idempotent.
    """
    with lib.pg() as c, c.cursor() as cur:
        cur.execute("""UPDATE raw_company
                       SET payload = payload || '{"udyam_registered": true}'::jsonb
                       WHERE source_id = %s
                         AND (payload->>'udyam_registered') IS DISTINCT FROM 'true'""",
                    (SOURCE,))
        return cur.rowcount


def sweep(f: lib.Fetcher, pins: list[dict], page: int, budget: dict) -> dict:
    done = json.loads(CKPT.read_text()) if CKPT.exists() else {}
    stats = {"pincodes": 0, "scanned": 0, "kept": 0, "truncated": 0, "skipped": 0}
    for i, p in enumerate(pins, 1):
        pin = p["pincode"]
        if done.get(pin) == "done":
            stats["skipped"] += 1
            continue
        if budget["requests"] >= budget["max"]:
            print("    request budget reached; rerun the same command to resume",
                  flush=True)
            break
        q = f"{pin}.0"                       # the field is stored as a float string
        try:
            r = f.get(API, params={"api-key": lib.DATA_GOV_KEY, "format": "json",
                                   "limit": 1, "filters[Pincode]": q})
            total = int(r.json().get("total") or 0)
        except Exception as exc:
            print(f"    ! count {pin}: {str(exc)[:80]}", flush=True)
            continue
        budget["requests"] += 1
        if total == 0:
            done[pin] = "done"
            continue
        if total > WINDOW:
            stats["truncated"] += 1
            print(f"    ! TRUNCATED {pin} ({p.get('district')}) — {total} rows, "
                  f"window is {WINDOW}", flush=True)
        rows: list[dict] = []
        capped = min(total, WINDOW)
        for off in range(0, capped, page):
            if budget["requests"] >= budget["max"]:
                break
            try:
                resp = f.get(API, params={"api-key": lib.DATA_GOV_KEY, "format": "json",
                                          "limit": page, "offset": off,
                                          "filters[Pincode]": q})
                recs = resp.json().get("records") or []
            except Exception as exc:
                print(f"    ! {pin} offset {off}: {str(exc)[:80]}", flush=True)
                break
            budget["requests"] += 1
            if not recs:
                break
            stats["scanned"] += len(recs)
            for rec in recs:
                row = to_row(rec)
                if row:
                    rows.append(row)
        # write BEFORE marking the pincode done — the MCA crawl lost a whole state by
        # advancing its checkpoint ahead of the data
        if rows:
            lib.upsert("raw_company", rows, "source_id, source_key")
            stats["kept"] += len(rows)
        done[pin] = "done"
        stats["pincodes"] += 1
        CKPT.write_text(json.dumps(done))
        if i % 10 == 0 or rows:
            print(f"    [{i}/{len(pins)}] {pin} {p.get('district') or ''}: "
                  f"{total} rows -> {len(rows)} kept "
                  f"(total kept {stats['kept']}, {budget['requests']} reqs)", flush=True)
    CKPT.write_text(json.dumps(done))
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", choices=["wedge", "all-clusters"], default="wedge")
    ap.add_argument("--pincodes", nargs="*", help="explicit pincodes instead of a scope")
    ap.add_argument("--page", type=int, default=1000)
    ap.add_argument("--max-requests", type=int, default=3000)
    ap.add_argument("--delay", type=float, default=1.2)
    ap.add_argument("--restart", action="store_true")
    ap.add_argument("--mark-registered", action="store_true",
                    help="only stamp udyam_registered on rows already landed, then exit")
    a = ap.parse_args()

    if a.mark_registered:
        print(f"  udyam_registered stamped on {mark_registered()} existing row(s)")
        return 0
    if a.restart and CKPT.exists():
        CKPT.unlink()
    if lib.DATA_GOV_KEY_IS_SHARED:
        print("  ! shared demo key: 10 rows/request and globally rate-limited. "
              "Set DATA_GOV_IN_KEY in cosmetics/.env.")

    pins = target_pincodes(a.scope, a.pincodes)
    print(f"  {len(pins)} pincodes in scope ({a.scope})")
    f = lib.Fetcher(SOURCE, delay=a.delay, timeout=60.0,
                    max_attempts=3, backoff_base=8.0, backoff_max=60.0)
    t0 = time.time()
    try:
        st = sweep(f, pins, a.page, {"requests": 0, "max": a.max_requests})
    finally:
        f.close()
    with lib.pg() as c, c.cursor() as cur:
        cur.execute("SELECT count(*), "
                    "count(*) FILTER (WHERE (payload->>'core_cosmetic')::bool), "
                    "count(*) FILTER (WHERE jsonb_array_length(payload->'packaging_types')>0) "
                    "FROM raw_company WHERE source_id=%s", (SOURCE,))
        tot, core, pack = cur.fetchone()
    print(f"S17b done in {time.time()-t0:.0f}s: swept {st['pincodes']} pincodes "
          f"({st['skipped']} already done), scanned {st['scanned']} MSMEs, "
          f"kept {st['kept']} this run")
    print(f"  {tot} rows in total — {core} core-cosmetic, {pack} packaging converters")
    if st["truncated"]:
        print(f"  ! {st['truncated']} pincode(s) exceeded the {WINDOW}-row window and are "
              f"partial; they are the dense urban ones")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
