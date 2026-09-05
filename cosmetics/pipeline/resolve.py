"""Stage 3 — entity resolution.  Spec §8.

Key hierarchy, in reliability order:
  1. licence_no + sla_state   (facilities — reliable)
  2. CIN                      (companies and LLPs ONLY — excludes every proprietorship
                               and partnership, which is most of the MSME long tail)
  3. GSTIN                    (enrichment, harvested opportunistically)
  4. norm_name + pincode      (everything else)

(4) is the PRIMARY path here, not a fallback: a large share of Indian contract
manufacturers, packaging converters and raw-material dealers have no CIN at all. The
review queue is sized and staffed accordingly rather than treated as an exception path.

crosswalk is APPEND-ONLY. Re-resolution inserts new rows and sets superseded_by; nothing
is ever deleted (hard rule 2).

The OPERATES edge exists in NO source and is inferred here by name+address matching
between the company spine (S3/S8/S9) and the licensed-facility sources (S1/S2). A wrong
OPERATES attaches a licence to the wrong company — the single most damaging error this
product can make — so it requires score >= 0.92 OR a shared GSTIN/pincode; everything
else goes to review (hard rule 12).
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from collections import defaultdict
from pathlib import Path

from rapidfuzz import fuzz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib  # noqa: E402

AUTO_MERGE = 0.92
REVIEW_FLOOR = 0.75
OPERATES_MIN = 0.92


def entity_id(prefix: str, seed: str) -> str:
    return prefix + hashlib.blake2b(seed.encode(), digest_size=8).hexdigest()[:12]


def canonical_seed(cin, gstin, pan, norm, pincode) -> str:
    return cin or gstin or pan or f"{norm or ''}|{pincode or ''}"


def address_similarity(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.0
    return fuzz.token_set_ratio(a[:200], b[:200]) / 100.0


def score_pair(l: dict, r: dict) -> tuple[float, dict]:
    """Spec §8 weights: 0.45 name, 0.25 address, 0.20 contact, 0.10 category.

    Weights are renormalised over the features that are actually OBSERVABLE on this
    pair. Sources differ in what they carry — a licence listing has no email and no
    self-declared role — and charging a pair for a feature neither side could ever
    supply is not evidence of difference, it is evidence of a thin source. Without
    this, a facility matched against its own company row from the same source tops
    out at 0.70 and the OPERATES join silently never fires.
    """
    feats: list[tuple[str, float, float]] = []   # (name, weight, value)

    name = fuzz.token_set_ratio(l["norm_name"] or "", r["norm_name"] or "") / 100.0
    feats.append(("name", 0.45, name))

    if l.get("address_raw") and r.get("address_raw"):
        feats.append(("address", 0.25, address_similarity(l["address_raw"], r["address_raw"])))

    l_phone, r_phone = _digits(l.get("phone")), _digits(r.get("phone"))
    l_mail, r_mail = (l.get("email") or "").lower(), (r.get("email") or "").lower()
    if (l_phone and r_phone) or (l_mail and r_mail):
        contact = 1.0 if ((l_phone and l_phone == r_phone) or (l_mail and l_mail == r_mail)) else 0.0
        feats.append(("contact", 0.20, contact))

    lc, rc = set(l.get("roles_claimed") or []), set(r.get("roles_claimed") or [])
    if lc and rc:
        feats.append(("category", 0.10, len(lc & rc) / len(lc | rc)))

    total_w = sum(w for _, w, _ in feats)
    s = sum(w * v for _, w, v in feats) / total_w if total_w else 0.0
    return s, {k: round(v, 3) for k, _, v in feats} | {"weights_used": round(total_w, 2)}


def _digits(v: str | None) -> str:
    d = re.sub(r"\D", "", v or "")
    return d[-10:] if len(d) >= 10 else ""


def block_keys(row: dict) -> list[str]:
    keys = []
    if row.get("pincode"):
        keys.append("p:" + row["pincode"])
    nn = row.get("norm_name") or ""
    toks = nn.split()[:3]
    if toks:
        keys.append("n:" + " ".join(toks))
    if row.get("phone"):
        keys.append("t:" + re.sub(r"\D", "", row["phone"])[-10:])
    return keys


def load_companies() -> list[dict]:
    cols = ("id, source_id, source_key, legal_name, norm_name, cin, gstin, pan, "
            "address_raw, pincode, district, state, phone, email, website, "
            "roles_claimed, tier, cluster_id")
    with lib.pg() as c, c.cursor() as cur:
        cur.execute(f"SELECT {cols} FROM raw_company")
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]


def existing_crosswalk(entity_type: str) -> dict[tuple[str, str], str]:
    with lib.pg() as c, c.cursor() as cur:
        cur.execute("SELECT source_id, source_key, entity_id FROM crosswalk "
                    "WHERE entity_type=%s AND superseded_by IS NULL", (entity_type,))
        return {(r[0], r[1]): r[2] for r in cur.fetchall()}


def resolve_companies(dry: bool) -> dict[tuple[str, str], str]:
    rows = load_companies()
    known = existing_crosswalk("company")
    print(f"  {len(rows)} raw_company rows, {len(known)} already crosswalked")

    by_cin: dict[str, str] = {}
    by_gstin: dict[str, str] = {}
    by_pan: dict[str, str] = {}
    blocks: dict[str, list[dict]] = defaultdict(list)
    assigned: dict[tuple[str, str], str] = {}
    members: dict[str, list[dict]] = defaultdict(list)
    new_links: list[tuple] = []
    reviews: list[dict] = []
    stats = {"reused": 0, "exact": 0, "fuzzy": 0, "new": 0, "review": 0}

    # deterministic keys resolve first so a fuzzy pass can never split them apart
    order = sorted(rows, key=lambda r: (0 if r["cin"] else 1 if r["gstin"] else 2, r["id"]))

    for row in order:
        key = (row["source_id"], row["source_key"])
        if key in known:
            assigned[key] = known[key]
            members[known[key]].append(row)
            stats["reused"] += 1
            continue

        eid = method = None
        score = 1.0
        cin, gstin, pan = row["cin"], row["gstin"], row["pan"]
        if cin and cin in by_cin:
            eid, method = by_cin[cin], "exact_cin"
        elif gstin and gstin in by_gstin:
            eid, method = by_gstin[gstin], "exact_gstin"
        elif pan and pan in by_pan:
            eid, method = by_pan[pan], "exact_pan"

        if eid is None:
            best, best_row, best_feat = 0.0, None, {}
            seen: set[int] = set()
            for bk in block_keys(row):
                for cand in blocks.get(bk, ()):
                    if id(cand) in seen:
                        continue
                    seen.add(id(cand))
                    s, feat = score_pair(row, cand)
                    if s > best:
                        best, best_row, best_feat = s, cand, feat
            if best_row is not None and best >= AUTO_MERGE:
                eid, method, score = assigned[(best_row["source_id"], best_row["source_key"])], "fuzzy", best
                stats["fuzzy"] += 1
            elif best_row is not None and best >= REVIEW_FLOOR:
                eid = entity_id("c_", canonical_seed(cin, gstin, pan, row["norm_name"], row["pincode"]))
                method, score = "provisional", best
                reviews.append({
                    "entity_type": "company",
                    "left_key": f"{row['source_id']}|{row['source_key']}"[:400],
                    "right_key": f"{best_row['source_id']}|{best_row['source_key']}"[:400],
                    "score": round(best, 4), "features": lib.jsonb(best_feat),
                })
                stats["review"] += 1
            else:
                eid = entity_id("c_", canonical_seed(cin, gstin, pan, row["norm_name"], row["pincode"]))
                method, score = "new", 0.0
                stats["new"] += 1
        else:
            stats["exact"] += 1

        assigned[key] = eid
        members[eid].append(row)
        if cin:
            by_cin.setdefault(cin, eid)
        if gstin:
            by_gstin.setdefault(gstin, eid)
        if pan:
            by_pan.setdefault(pan, eid)
        for bk in block_keys(row):
            blocks[bk].append(row)
        new_links.append(("company", row["source_id"], row["source_key"], eid,
                          method if method != "new" else "fuzzy" if False else method, score))

    if not dry:
        with lib.pg() as c, c.cursor() as cur:
            cur.executemany(
                "INSERT INTO crosswalk (entity_type, source_id, source_key, entity_id, method, score) "
                "VALUES (%s,%s,%s,%s,%s,%s)", new_links)
        lib.upsert("review_queue", reviews, "id") if False else None
        if reviews:
            with lib.pg() as c, c.cursor() as cur:
                cur.executemany(
                    "INSERT INTO review_queue (entity_type, left_key, right_key, score, features) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    [(r["entity_type"], r["left_key"], r["right_key"], r["score"], r["features"])
                     for r in reviews])

    print(f"  companies -> {len(set(assigned.values()))} entities "
          f"(reused {stats['reused']}, exact {stats['exact']}, fuzzy {stats['fuzzy']}, "
          f"new {stats['new']}, queued for review {stats['review']})")

    # spec §15.3 false-merge detector: one entity must never carry two distinct CINs
    bad = [(eid, {m['cin'] for m in ms if m['cin']}) for eid, ms in members.items()
           if len({m["cin"] for m in ms if m["cin"]}) > 1]
    if bad:
        for eid, cins in bad[:10]:
            print(f"  !! FALSE MERGE {eid}: {sorted(cins)}")
        raise SystemExit(f"false-merge check FAILED for {len(bad)} entities")
    print("  false-merge check: PASS")
    return assigned


def resolve_facilities(company_map: dict, dry: bool) -> dict:
    cols = ("id, source_id, source_key, firm_name, norm_name, address_raw, pincode, "
            "state, district, licence_no, licence_form, cluster_id, company_source_key")
    with lib.pg() as c, c.cursor() as cur:
        cur.execute(f"SELECT {cols} FROM raw_facility")
        names = [d[0] for d in cur.description]
        rows = [dict(zip(names, r)) for r in cur.fetchall()]
    known = existing_crosswalk("facility")
    links, assigned = [], {}
    for row in rows:
        key = (row["source_id"], row["source_key"])
        if key in known:
            assigned[key] = known[key]
            continue
        if row["licence_no"] and row["state"]:
            seed = f"{row['licence_no']}|{row['state']}"
            method = "exact_licence"
        else:
            seed = f"{row['norm_name']}|{row['pincode']}|{(row['address_raw'] or '')[:40]}"
            method = "fuzzy"
        eid = entity_id("f_", seed)
        assigned[key] = eid
        links.append(("facility", row["source_id"], row["source_key"], eid, method, 1.0))
    if not dry and links:
        with lib.pg() as c, c.cursor() as cur:
            cur.executemany(
                "INSERT INTO crosswalk (entity_type, source_id, source_key, entity_id, method, score) "
                "VALUES (%s,%s,%s,%s,%s,%s)", links)
    print(f"  facilities -> {len(set(assigned.values()))} entities from {len(rows)} rows")
    return assigned


def infer_operates(company_map: dict, facility_map: dict, dry: bool) -> None:
    """The hardest and most valuable join in the build (spec §8)."""
    with lib.pg() as c, c.cursor() as cur:
        cur.execute("SELECT source_id, source_key, norm_name, pincode, address_raw, gstin, "
                    "district, state FROM raw_company")
        cnames = [d[0] for d in cur.description]
        companies = [dict(zip(cnames, r)) for r in cur.fetchall()]
        cur.execute("SELECT source_id, source_key, norm_name, pincode, address_raw, "
                    "district, state, payload->>'phone' AS phone FROM raw_facility")
        fnames = [d[0] for d in cur.description]
        facilities = [dict(zip(fnames, r)) for r in cur.fetchall()]

    idx: dict[str, list[dict]] = defaultdict(list)
    for co in companies:
        for bk in block_keys(co):
            idx[bk].append(co)

    edges, reviews = [], []
    for fac in facilities:
        best, best_co, feat = 0.0, None, {}
        seen = set()
        for bk in block_keys(fac):
            for co in idx.get(bk, ()):
                k = (co["source_id"], co["source_key"])
                if k in seen:
                    continue
                seen.add(k)
                s, ft = score_pair(fac, co)
                if s > best:
                    best, best_co, feat = s, co, ft
        if best_co is None:
            continue
        cid = company_map.get((best_co["source_id"], best_co["source_key"]))
        fid = facility_map.get((fac["source_id"], fac["source_key"]))
        if not (cid and fid):
            continue
        shared_geo = bool(fac["pincode"]) and fac["pincode"] == best_co["pincode"]
        if best >= OPERATES_MIN or shared_geo:
            edges.append((cid, fid, round(best, 4),
                          "score" if best >= OPERATES_MIN else "shared_pincode"))
        elif best >= REVIEW_FLOOR:
            reviews.append(("operates",
                            f"{fac['source_id']}|{fac['source_key']}"[:400],
                            f"{best_co['source_id']}|{best_co['source_key']}"[:400],
                            round(best, 4), lib.jsonb(feat)))

    if not dry:
        with lib.pg() as c, c.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS operates_edge ("
                        "company_id TEXT, facility_id TEXT, score NUMERIC, basis TEXT, "
                        "PRIMARY KEY (company_id, facility_id))")
            cur.executemany("INSERT INTO operates_edge (company_id, facility_id, score, basis) "
                            "VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING", edges)
            if reviews:
                cur.executemany("INSERT INTO review_queue (entity_type, left_key, right_key, "
                                "score, features) VALUES (%s,%s,%s,%s,%s)", reviews)
    print(f"  OPERATES: {len(edges)} inferred (>= {OPERATES_MIN} or shared pincode), "
          f"{len(reviews)} queued for review")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    cmap = resolve_companies(a.dry_run)
    fmap = resolve_facilities(cmap, a.dry_run)
    infer_operates(cmap, fmap, a.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
