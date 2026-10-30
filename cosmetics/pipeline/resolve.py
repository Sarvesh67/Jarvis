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

# Minimum weight mass that must be OBSERVABLE before a fuzzy score may auto-merge.
# name(0.45) + address(0.25) = 0.70. Below this the only evidence is the name, and a
# renormalised name-only score of 1.0 is not the same thing as a 1.0 corroborated match:
# "SHREE ENTERPRISES" is thousands of unrelated firms. Sources like CHEMEXCIL publish a
# name and a website and nothing else, so without this guard every generic name in the
# directory collapses into one entity.
CORROBORATION_FLOOR = 0.70


def entity_id(prefix: str, seed: str) -> str:
    return prefix + hashlib.blake2b(seed.encode(), digest_size=8).hexdigest()[:12]


class UnnormalisedRows(RuntimeError):
    """Raised when Stage 3 is asked to resolve rows Stage 2 has not touched yet."""


def canonical_seed(cin, gstin, pan, norm, pincode) -> str:
    """The natural key an entity id is derived from.

    `norm` is NOT optional here, and the empty string is not an acceptable stand-in for
    it. `norm_name` is written by Stage 2, and adapters insert NULL — so running Stage 3
    before Stage 2 seeds every unnamed row with `"|<pincode>"`, which hashes to ONE
    entity per pincode and silently collapses every firm in that pincode into a single
    company. The crosswalk is append-only, so the collapse then survives every later run:
    the rows are already assigned, and re-resolution skips them.

    That happened here — 91,392 raw rows frozen inside 4,446 pincode-shaped entities,
    two thirds of the corpus, discovered only because a NABL query attributed a cosmetics
    accreditation to an entity holding 124 unrelated laboratories. See
    decisions/0016-an-entity-seed-may-never-be-empty.md.
    """
    ident = cin or gstin or pan
    if ident:
        return ident
    if not norm:
        raise UnnormalisedRows(
            "cannot seed an entity from an empty norm_name — run `make normalize` "
            "(Stage 2) before `make resolve` (Stage 3)")
    return f"{norm}|{pincode or ''}"


# Business-form and sector words that survive §7.1 suffix stripping. They carry no
# identifying information: "SUDAMA ENTERPRISE" and "RAMU ENTERPRISE" are two unrelated
# firms in one pincode, but token_set_ratio scores them 0.81 on the shared word alone.
# Name similarity is therefore computed on the DISTINCTIVE tokens, and a pair whose only
# common ground is generic vocabulary can never reach the auto-merge threshold.
GENERIC_TOKENS = {
    "ENT", "ENTERPRISE", "ENTERPRISES", "TRADERS", "TRADER", "TRADING", "INDS",
    "INDUSTRY", "INDUSTRIAL", "CORPORATION", "CORP", "AGENCIES", "AGENCY", "PROD",
    "CHEM", "INTL", "LABS", "PHARMA", "MFG", "EXPORT", "EXPORTS", "IMPORT", "IMPORTS",
    "IMPEX", "GROUP", "SALES", "MARKETING", "COSMETIC", "COSMETICS", "INDIA", "INDIAN",
    "BHARAT", "NEW", "THE", "AND", "OF", "CO", "COMPANY", "WORKS", "UDYOG", "TRADELINK",
    "ORGANICS", "SPECIALITY", "SPECIALTY", "SOLUTIONS", "TECHNOLOGIES", "VENTURES",
}


def distinctive(name: str | None) -> list[str]:
    return [t for t in (name or "").split() if t not in GENERIC_TOKENS and len(t) > 1]


def name_similarity(a: str | None, b: str | None) -> tuple[float, bool]:
    """(similarity, generic_only) — similarity over distinctive tokens where possible.

    token_set_ratio alone is DANGEROUS here: when one side's distinctive-token set is a
    subset of the other's, it scores ~100 regardless of how many extra tokens the larger
    side carries. "MEGHMANI" vs "MEGHMANI DYES INTERMEDIATES" scores 100 on
    token_set_ratio even though they are different legal entities (Meghmani Organics
    Limited and Meghmani Dyes and Intermediates Limited, confirmed by distinct CINs) —
    token_sort_ratio on the same pair scores 46. Taking the MINIMUM of the two closes
    that hole: a genuine match scores high on both, a subset-containment false match
    only scores high on one.
    """
    da, db = distinctive(a), distinctive(b)
    if da and db:
        sa, sb = " ".join(da), " ".join(db)
        sim = min(fuzz.token_set_ratio(sa, sb), fuzz.token_sort_ratio(sa, sb)) / 100.0
        return sim, False
    # one side is nothing but generic vocabulary: fall back, and flag it
    sim = min(fuzz.token_set_ratio(a or "", b or ""),
              fuzz.token_sort_ratio(a or "", b or "")) / 100.0
    return sim, True


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

    name, generic_only = name_similarity(l["norm_name"], r["norm_name"])
    feats.append(("name", 0.45, name))

    if l.get("address_raw") and r.get("address_raw"):
        feats.append(("address", 0.25, address_similarity(l["address_raw"], r["address_raw"])))

    l_phone, r_phone = _digits(l.get("phone")), _digits(r.get("phone"))
    l_mail, r_mail = (l.get("email") or "").lower(), (r.get("email") or "").lower()
    l_web, r_web = _domain(l.get("website")), _domain(r.get("website"))
    if (l_phone and r_phone) or (l_mail and r_mail) or (l_web and r_web):
        contact = 1.0 if ((l_phone and l_phone == r_phone) or (l_mail and l_mail == r_mail)
                          or (l_web and l_web == r_web)) else 0.0
        feats.append(("contact", 0.20, contact))

    # roles_claimed is an ADDITIVE claim vocabulary and each source stamps its own
    # (CHEMEXCIL says `exporter`, an exhibitor list says `cosmetics_active`, Udyam says
    # `manufacturer_candidate`). Two rows for the SAME company from two sources therefore
    # have disjoint role sets by construction, so scoring the absence of overlap as
    # evidence of difference penalises exactly the cross-source links we are trying to
    # find. Overlap is counted only when it EXISTS, as positive evidence; its absence is
    # not evidence at all.
    lc, rc = set(l.get("roles_claimed") or []), set(r.get("roles_claimed") or [])
    if lc & rc:
        feats.append(("category", 0.10, len(lc & rc) / len(lc | rc)))

    total_w = sum(w for _, w, _ in feats)
    s = sum(w * v for _, w, v in feats) / total_w if total_w else 0.0
    # A generic-only name is weak evidence, not zero evidence. Cap it ONLY when nothing
    # else corroborates: "S.P. Pharmaceuticals" vs "S P Pharmaceuticals" at the same
    # address is obviously one company even though every distinctive token is a single
    # letter, while "SHREE VERAY ENTERPRISE" vs "A K ENTERPRISE" in one pincode is not.
    if generic_only:
        corroboration = max((v for k, _, v in feats if k != "name"), default=0.0)
        if corroboration < 0.9:
            s = min(s, REVIEW_FLOOR)
    return s, ({k: round(v, 3) for k, _, v in feats}
               | {"weights_used": round(total_w, 2), "generic_only": generic_only})


# A website is contact evidence, at the weight a shared phone or email carries
# (decisions/0007, amended 2026-10-29). It is never enough on its own: group sites are
# shared by distinct legal entities (atul.co.in, ariesgroup.in, neogenchem.com each carry
# 3-4 different names in CHEMEXCIL), and marketplace or directory pages are shared by
# thousands of unrelated firms, so those hosts are not a key at all.
PLATFORM_HOSTS = {
    "indiamart.com", "tradeindia.com", "exportersindia.com", "justdial.com",
    "zaubacorp.com", "facebook.com", "instagram.com", "linkedin.com", "twitter.com",
    "x.com", "youtube.com", "google.com", "sites.google.com", "business.site",
    "blogspot.com", "wordpress.com", "wixsite.com", "weebly.com", "godaddysites.com",
    "amazon.in", "flipkart.com", "nykaa.com",
}


def _domain(url: str | None) -> str:
    host = re.sub(r"^https?://", "", (url or "").strip().lower()).split("/")[0]
    host = host.split(":")[0].removeprefix("www.")
    if "." not in host or any(host == p or host.endswith("." + p) for p in PLATFORM_HOSTS):
        return ""
    return host


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
    web = _domain(row.get("website"))
    if web:
        keys.append("w:" + web)
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

    # Two different things produce a row with no norm_name, and they need opposite
    # handling. A registry that publishes a firm called "-" is junk in one row; Stage 2
    # never having run is every row in the source. Tell them apart by the share, and
    # only the second is an error — the first is skipped, because a row with no name and
    # no identifier is not an entity and seeding one collapses a whole pincode.
    named_by_src: dict[str, int] = defaultdict(int)
    blank_by_src: dict[str, int] = defaultdict(int)
    for r in rows:
        (named_by_src if r["norm_name"] else blank_by_src)[r["source_id"]] += 1
    unnormalised = {src: n for src, n in blank_by_src.items()
                    if n > 20 and n > 0.01 * (n + named_by_src[src])}
    if unnormalised:
        raise UnnormalisedRows(
            "these sources have not been through Stage 2: "
            + ", ".join(f"{k} ({v} rows with no norm_name)"
                        for k, v in sorted(unnormalised.items()))
            + ". Run `make normalize` first — seeding them would collapse every firm "
              "in a pincode into one entity.")
    unnameable = sum(blank_by_src.values())
    if unnameable:
        print(f"  {unnameable} rows have no usable name and no identifier — not entities, "
              f"skipped")

    by_cin: dict[str, str] = {}
    by_gstin: dict[str, str] = {}
    by_pan: dict[str, str] = {}
    blocks: dict[str, list[dict]] = defaultdict(list)
    assigned: dict[tuple[str, str], str] = {}
    members: dict[str, list[dict]] = defaultdict(list)
    new_links: list[tuple] = []
    reviews: list[dict] = []
    stats = {"reused": 0, "exact": 0, "fuzzy": 0, "name_only": 0, "new": 0, "review": 0}

    # A norm_name seen at two or more distinct pincodes is a demonstrated collider and
    # may never be merged on the name alone, however exact the match.
    pin_by_name: dict[str, set] = defaultdict(set)
    for r in rows:
        if r["norm_name"] and r["pincode"]:
            pin_by_name[r["norm_name"]].add(r["pincode"])
    ambiguous_names = {n: True for n, pins in pin_by_name.items() if len(pins) > 1}
    print(f"  {len(ambiguous_names)} normalised names occur at more than one pincode "
          f"(never merged on name alone)")

    # deterministic keys resolve first so a fuzzy pass can never split them apart
    order = sorted(rows, key=lambda r: (0 if r["cin"] else 1 if r["gstin"] else 2, r["id"]))

    for row in order:
        key = (row["source_id"], row["source_key"])
        if not row["norm_name"] and not (row["cin"] or row["gstin"] or row["pan"]):
            continue      # nothing to identify it by; see the note above
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
                    # HARD VETO: CIN is deterministic identity (spec §8 key hierarchy,
                    # rank 2). If both rows already carry their OWN distinct CIN, no
                    # amount of name/address similarity may merge them — that would
                    # contradict identity evidence stronger than anything fuzzy scoring
                    # can produce. This is what stopped Nirma's five sister companies
                    # (NIRMAN/NAMAN/NAVIN/NIRCHEM/NEO Soaps and Detergents Pvt Ltd, one
                    # registered address, five distinct CINs) from being collapsed into
                    # one entity — their names score 93 on BOTH token_set_ratio and
                    # token_sort_ratio, so the metric fix alone cannot catch this case.
                    if cand.get("cin") and row.get("cin") and cand["cin"] != row["cin"]:
                        continue
                    s, feat = score_pair(row, cand)
                    if s > best:
                        best, best_row, best_feat = s, cand, feat
            thin = (best_feat.get("weights_used", 0.0) < CORROBORATION_FLOOR)
            name_only_ok = (
                thin
                and best_feat.get("name", 0.0) >= 0.999      # exact normalised name
                and len((row["norm_name"] or "").split()) >= 2
                and not ambiguous_names.get(row["norm_name"])  # not a known collider
            )
            if best_row is not None and best >= AUTO_MERGE and (not thin or name_only_ok):
                eid = assigned[(best_row["source_id"], best_row["source_key"])]
                method = "fuzzy" if not thin else "name_exact_distinctive"
                score = best
                stats["fuzzy" if not thin else "name_only"] += 1
            elif best_row is not None and best >= AUTO_MERGE and thin:
                # strong name match, no corroborating feature -> a human decides
                eid = entity_id("c_", canonical_seed(cin, gstin, pan, row["norm_name"], row["pincode"]))
                method, score = "provisional", best
                reviews.append({
                    "entity_type": "company",
                    "left_key": f"{row['source_id']}|{row['source_key']}"[:400],
                    "right_key": f"{best_row['source_id']}|{best_row['source_key']}"[:400],
                    "score": round(best, 4),
                    "features": lib.jsonb({**best_feat, "reason": "name-only evidence"}),
                })
                stats["review"] += 1
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
          f"(reused {stats['reused']}, exact-id {stats['exact']}, fuzzy {stats['fuzzy']}, "
          f"name-only {stats['name_only']}, new {stats['new']}, "
          f"queued for review {stats['review']})")

    # spec §15.3 false-merge detector: one entity must never carry two distinct CINs
    bad = [(eid, {m['cin'] for m in ms if m['cin']}) for eid, ms in members.items()
           if len({m["cin"] for m in ms if m["cin"]}) > 1]
    if bad:
        for eid, cins in bad[:10]:
            print(f"  !! FALSE MERGE {eid}: {sorted(cins)}")
        raise SystemExit(f"false-merge check FAILED for {len(bad)} entities")

    # A CIN conflict is the only false merge this check could see, and most of the
    # corpus has no CIN — which is why 4,446 pincode buckets holding 91,392 rows passed
    # it for months. An entity is also demonstrably wrong when it carries more distinct
    # normalised names than a company plausibly has: a firm may legitimately appear under
    # a handful of spellings across sources, but not under fifty.
    NAME_FANOUT_MAX = 12
    fanout = sorted(
        ((eid, {m["norm_name"] for m in ms if m["norm_name"]}) for eid, ms in members.items()),
        key=lambda t: -len(t[1]))
    wide = [(eid, ns) for eid, ns in fanout if len(ns) > NAME_FANOUT_MAX]
    if wide:
        for eid, ns in wide[:10]:
            print(f"  !! NAME COLLAPSE {eid}: {len(ns)} distinct names, "
                  f"e.g. {sorted(ns)[:3]}")
        raise SystemExit(
            f"false-merge check FAILED: {len(wide)} entities carry more than "
            f"{NAME_FANOUT_MAX} distinct normalised names")
    print(f"  false-merge check: PASS (widest entity carries "
          f"{len(fanout[0][1]) if fanout else 0} distinct names)")
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


def empty_seed_ids() -> dict[str, str | None]:
    """Entity ids that can only have come from a row with no name and no identifier.

    Reconstructed rather than guessed: for every pincode in the corpus, the id that
    `canonical_seed(None, None, None, "", pincode)` used to produce. An id in this set is
    a pincode bucket, not a company.
    """
    with lib.pg() as c, c.cursor() as cur:
        cur.execute("SELECT DISTINCT pincode FROM raw_company WHERE pincode IS NOT NULL")
        pins = [r[0] for r in cur.fetchall()]
    out = {entity_id("c_", f"|{p}"): p for p in pins}
    out[entity_id("c_", "|")] = None
    return out


def retire_bucketed(dry: bool) -> list[tuple[int, str, str]]:
    """Take every crosswalk row assigned to a pincode bucket out of the live set.

    The crosswalk is append-only (hard rule 6), so a wrong assignment is never deleted —
    it is superseded, and the chain stays readable. But a supersede must point at the row
    that replaced it, and that row does not exist yet: it is written by the resolution
    pass that follows. So this marks the row retired (pointing at itself, which is the
    only value that keeps the foreign key valid and the row out of every
    `superseded_by IS NULL` query) and returns the keys, and `relink_retired` below
    repairs the pointer once the replacement exists. Both halves run inside one
    `--reseed-empty`, so the self-pointer is never a state the pipeline ends in.
    """
    buckets = empty_seed_ids()
    with lib.pg() as c, c.cursor() as cur:
        cur.execute("SELECT id, entity_id, source_id, source_key FROM crosswalk "
                    "WHERE entity_type='company' AND superseded_by IS NULL")
        live = cur.fetchall()
        stale = [(cid, src, key) for cid, eid, src, key in live if eid in buckets]
        n_buckets = len({eid for _, eid, _, _ in live if eid in buckets})
        print(f"  {len(stale)} crosswalk rows sit in {n_buckets} pincode buckets")
        if dry or not stale:
            return []
        cur.executemany("UPDATE crosswalk SET superseded_by=id WHERE id=%s",
                        [(cid,) for cid, _, _ in stale])
    print(f"  retired {len(stale)} rows — re-resolved below")
    return stale


def relink_retired(stale: list[tuple[int, str, str]]) -> None:
    """Point each retired row at the row that replaced it, so the chain reads correctly."""
    if not stale:
        return
    with lib.pg() as c, c.cursor() as cur:
        cur.execute("SELECT source_id, source_key, id FROM crosswalk "
                    "WHERE entity_type='company' AND superseded_by IS NULL")
        live = {(r[0], r[1]): r[2] for r in cur.fetchall()}
        pairs = [(live[(src, key)], cid) for cid, src, key in stale
                 if (src, key) in live and live[(src, key)] > cid]
        cur.executemany("UPDATE crosswalk SET superseded_by=%s WHERE id=%s", pairs)
        # A row the resolver skipped as unnameable gets no replacement, so there is
        # nothing for its pointer to reference. Put it back the way it was rather than
        # leaving it pointing at itself: a supersede that names no successor is not a
        # state this table has, and the append-only invariant test rightly rejects it.
        # Its old id was a pincode bucket, but it is now that bucket's only member — an
        # ordinary singleton entity, which is exactly what an unresolvable row deserves.
        cur.execute("UPDATE crosswalk SET superseded_by=NULL "
                    "WHERE entity_type='company' AND superseded_by = id")
        orphans = cur.rowcount
    print(f"  relinked {len(pairs)} supersede pointers"
          + (f"; {orphans} unnameable rows kept their own entity" if orphans else ""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reseed-empty", action="store_true",
                    help="retire entity ids seeded from an empty norm_name, then "
                         "re-resolve those rows (see decisions/0016)")
    a = ap.parse_args()
    stale = retire_bucketed(a.dry_run) if a.reseed_empty else []
    cmap = resolve_companies(a.dry_run)
    if not a.dry_run:
        relink_retired(stale)
    fmap = resolve_facilities(cmap, a.dry_run)
    infer_operates(cmap, fmap, a.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
