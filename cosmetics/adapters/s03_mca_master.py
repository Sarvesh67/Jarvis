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

Two modes, because the API has a hard 10,000-record window per distinct query (the same
Elasticsearch ceiling as CosIng) and 4.06M rows cannot be paged through:

  --mode names  (default, the useful one) — reverse-lookup: for every company we already
      hold whose name carries a corporate suffix, ask MCA for that exact name. This is
      the only route to a VERIFIED CIN, and it also returns the registered office, which
      is the single largest source of addresses for the CHEMEXCIL half of the graph
      (2,379 companies that arrive with a name and a website and nothing else).

  --mode crawl — partitioned discovery, capped at 10k rows per (state, status) bucket.
      Useful for the smaller wedge states, arbitrary and truncated for Gujarat and
      Maharashtra, so it discovers rather than enumerates.

MATCHING IS THE DANGEROUS PART. "PRITAM INTERNATIONAL PRIVATE LIMITED" resolves in MCA to
a West Bengal company; ours is a Haridwar cosmetics manufacturer with the same name. An
exact name match across 4M companies is a CANDIDATE, not an identity. Rows are therefore
landed with their own CIN as the key and a corroboration verdict, and the merge decision
is left to Stage 3, which already refuses to auto-merge on a name alone.

Rate limits: the documented shared data.gov.in key caps every response at 10 rows and is
globally exhausted. Put your own free key in cosmetics/.env as DATA_GOV_IN_KEY.
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
    """Roles from the NIC embedded in the CIN — which is the activity declared AT
    INCORPORATION and is never updated afterwards.

    HIMACHAL FURNITURES PVT LTD carries NIC 20239 ("other perfumes and toilet
    preparations") because that is what it registered as; it makes furniture. The code is
    read correctly, so the fix is not to drop it but to stop it wearing the same label as
    Udyam's CURRENT activity declaration. `_at_incorporation` keeps the signal (it is a
    genuine lead) while making sure no query can mistake it for present-day activity.
    """
    if not nic:
        return []
    p4 = nic[:4]
    if p4 in NIC_MANUFACTURE:
        return ["manufacturer_candidate_at_incorporation"]
    if p4 in NIC_WHOLESALE:
        return ["distributor_at_incorporation"]
    if p4 in NIC_RETAIL:
        return ["retailer_at_incorporation"]
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


# MCA stores one exact string per company and it is not the one other sources use:
# "GALAXY SURFACTANTS LTD" resolves, "GALAXY SURFACTANTS LIMITED" does not. The filter is
# exact-match only, so each company is tried against a small ordered set of suffix
# variants and the first hit wins.
# ordered by how often MCA actually spells it that way, so most companies resolve in
# one or two requests rather than eight
SUFFIX_VARIANTS = ["PRIVATE LIMITED", "LIMITED", "LTD", "PVT LTD", "LLP",
                   "PRIVATE LTD", "INDIA PRIVATE LIMITED", ""]
CORPORATE_HINT = re.compile(r"\b(PRIVATE LIMITED|PVT\.? ?LTD|LIMITED|LTD|LLP)\b", re.I)


SUFFIX_STRIP = re.compile(
    r"[\s,.]*\b(PRIVATE\s+LIMITED|PVT\.?\s*LTD\.?|PRIVATE\s+LTD\.?|P\.?\s*LTD\.?|"
    r"LIMITED|LTD\.?|LLP|L\.?L\.?P\.?)\s*\.?$", re.I)


def stem(legal_name: str) -> str:
    """The company's words with any corporate suffix removed — NOT norm_name.

    norm_name applies §7.1's abbreviations (INDUSTRIES -> INDS, INTERNATIONAL -> INTL),
    which are exactly the words MCA spells out in full, so building lookup variants from
    it guarantees a miss. Punctuation is normalised because MCA writes "SPAK ORGOCHEM
    (INDIA)PVT.LTD." with no space before the suffix.
    """
    v = re.sub(r"\s+", " ", (legal_name or "").upper().replace(".", ". ")).strip()
    v = re.sub(r"[,]+", " ", v)
    prev = None
    while v != prev:                      # "X PVT. LTD." strips in one pass, others repeat
        prev = v
        v = SUFFIX_STRIP.sub("", v).strip()
    return re.sub(r"\s+", " ", v).strip(" -&")


def name_variants(legal_name: str, norm_name: str | None) -> list[str]:
    out, seen = [], set()

    def add(v: str) -> None:
        v = re.sub(r"\s+", " ", (v or "")).strip().upper()
        if v and len(v) > 2 and v not in seen:
            seen.add(v)
            out.append(v)

    add(legal_name)                        # exact string as some other source spelled it
    base = stem(legal_name)
    for suf in SUFFIX_VARIANTS:
        add(f"{base} {suf}" if suf else base)
    return out[:8]


def corroborate(rec: dict, cand: dict) -> tuple[str, float]:
    """Does this MCA record plausibly describe the company we asked about?

    State is the discriminator that is actually available on both sides. Pincode is
    better when we have one. Neither present -> `unverified`, which Stage 3 will not
    auto-merge on a name alone.
    """
    mca_state = (rec.get("registered_state") or "").strip().upper()
    our_state = (cand.get("state") or "").strip().upper()
    addr = rec.get("registered_office_address") or ""
    mca_pins = PIN_RE.findall(addr)
    our_pin = (cand.get("pincode") or "").strip()

    if our_pin and our_pin in mca_pins:
        return "pincode_match", 0.98
    if our_state and mca_state:
        return ("state_match", 0.90) if our_state == mca_state else ("state_conflict", 0.15)
    return "unverified", 0.50


def candidates(limit: int, only_corporate: bool) -> list[dict]:
    """Companies worth asking MCA about. Proprietorships and partnerships are skipped —
    MCA cannot see them at all, so a lookup is a wasted request, not a miss."""
    where = ["r.source_id <> 's03_mca'", "r.legal_name IS NOT NULL"]
    if only_corporate:
        # filter in SQL, not after LIMIT, or the cap is spent on rows we then discard
        where.append(r"r.legal_name ~* '\m(PRIVATE LIMITED|PVT\.? ?LTD|LIMITED|LTD|LLP)\M'")
    sql = f"""
        SELECT DISTINCT ON (r.norm_name)
               r.legal_name, r.norm_name, r.state, r.pincode, r.source_id
        FROM raw_company r
        WHERE {' AND '.join(where)}
        ORDER BY r.norm_name, r.tier
        LIMIT {limit}
    """
    with lib.pg() as c, c.cursor() as cur:
        cur.execute(sql)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]


def lookup_names(f: lib.Fetcher, cands: list[dict], delay: float) -> tuple[list[dict], dict]:
    seen_cin: set[str] = set()
    rows, adverse = [], []
    stats = {"asked": 0, "requests": 0, "hit": 0, "pincode_match": 0, "state_match": 0,
             "state_conflict": 0, "unverified": 0, "miss": 0}
    ckpt = lib.CACHE / "s03_names_done.json"
    done = set(json.loads(ckpt.read_text())) if ckpt.exists() else set()

    for i, cand in enumerate(cands, 1):
        key = cand["norm_name"] or cand["legal_name"]
        if key in done:
            continue
        stats["asked"] += 1
        rec = None
        for variant in name_variants(cand["legal_name"], cand["norm_name"]):
            stats["requests"] += 1
            try:
                r = f.get(API, params={"api-key": lib.DATA_GOV_KEY, "format": "json",
                                       "limit": 5, "filters[company_name]": variant})
                recs = r.json().get("records") or []
            except Exception:
                recs = []
            if recs:
                rec = recs[0]
                break
        done.add(key)
        if not rec:
            stats["miss"] += 1
        else:
            stats["hit"] += 1
            verdict, conf = corroborate(rec, cand)
            stats[verdict] = stats.get(verdict, 0) + 1
            cin = (rec.get("corporate_identification_number") or "").strip().upper()
            if cin and cin not in seen_cin:
                seen_cin.add(cin)
                nic_match, _, roles = is_cosmetics(cin, rec.get("company_name") or "")
                row = to_row(rec, nic_match, roles)
                payload = json.loads(row["payload"])
                payload.update({"matched_variant_of": cand["legal_name"],
                                "match_verdict": verdict, "match_confidence": conf,
                                "asked_from_source": cand["source_id"]})
                row["payload"] = lib.jsonb(payload)
                rows.append(row)
                status = (rec.get("company_status") or "").strip()
                if status and status.lower() not in ("active",):
                    adverse.append((cin, status))
        if i % 25 == 0:
            ckpt.write_text(json.dumps(sorted(done)))
            print(f"    [{i}/{len(cands)}] {stats['hit']} hits / {stats['requests']} reqs "
                  f"| pincode {stats['pincode_match']} state {stats['state_match']} "
                  f"conflict {stats['state_conflict']}", flush=True)
    ckpt.write_text(json.dumps(sorted(done)))
    stats["adverse"] = adverse
    return rows, stats


# MCA's own vocabularies, used to split a state bucket below the 10k window.
# Override with --status/--class if MCA adds values.
STATUSES = ["Active", "Strike Off", "Amalgamated", "Dissolved", "Under Liquidation",
            "Converted to LLP", "Under Process of Striking Off", "Liquidated",
            "Dormant under section 455", "Not available for eFiling",
            "Converted to LLP and dissolved", "Captured"]
CLASSES = ["Private", "Public", "One Person Company", "Private(One Person Company)"]
# third split level, used only when status+class still overflows the window. Maharashtra
# and Gujarat are the only wedge states large enough to need it.
ROCS = ["RoC-Mumbai", "RoC-Pune", "RoC-Ahmedabad", "RoC-Delhi", "RoC-Chandigarh",
        "RoC-Kanpur", "RoC-Jaipur", "RoC-Shimla", "RoC-Dehradun", "RoC-Bangalore",
        "RoC-Chennai", "RoC-Hyderabad", "RoC-Kolkata", "RoC-Ernakulam", "RoC-Goa",
        "RoC-Patna", "RoC-Cuttack", "RoC-Guwahati", "RoC-Gwalior", "RoC-Coimbatore",
        "RoC-Vijayawada", "RoC-Jammu", "RoC-Shillong", "RoC-Puducherry"]
WINDOW = 10000          # hard Elasticsearch result window on this API


def _count(f: lib.Fetcher, filters: dict) -> int | None:
    """Bucket size, or None if the API would not say. None means 'assume it overflows'
    and split further — guessing small would silently truncate the bucket."""
    params = {"api-key": lib.DATA_GOV_KEY, "format": "json", "limit": 1}
    params.update({f"filters[{k}]": v for k, v in filters.items()})
    try:
        return int(f.get(API, params=params).json().get("total") or 0)
    except Exception as exc:
        print(f"    ! count failed for {filters}: {str(exc)[:90]}", flush=True)
        return None


def partition(f: lib.Fetcher, state: str, budget: dict) -> list[tuple[dict, int]]:
    """Split one state into buckets that each fit inside the 10,000-record window.

    The API caps every distinct query at 10k rows however you page it, so a state with
    200k companies cannot be enumerated as one query. Splitting on status, then class,
    brings almost every bucket under the ceiling; the few that remain are reported as
    TRUNCATED rather than silently half-read.
    """
    out: list[tuple[dict, int]] = []
    base = {"registered_state": state}
    total = _count(f, base); budget["requests"] += 1
    if total is not None and total <= WINDOW:
        return [(base, total)]
    for st in STATUSES:
        lvl1 = {**base, "company_status": st}
        n1 = _count(f, lvl1); budget["requests"] += 1
        if n1 == 0:
            continue
        if n1 is not None and n1 <= WINDOW:
            out.append((lvl1, n1))
            continue
        for cls in CLASSES:
            lvl2 = {**lvl1, "company_class": cls}
            n2 = _count(f, lvl2); budget["requests"] += 1
            if n2 == 0:
                continue
            if n2 is not None and n2 <= WINDOW:
                out.append((lvl2, n2))
                continue
            # still over the ceiling: split by registrar. Costs one count per ROC, so it
            # is only worth doing for the buckets that actually overflow.
            split_total = 0
            for roc in ROCS:
                lvl3 = {**lvl2, "registrar_of_companies": roc}
                n3 = _count(f, lvl3); budget["requests"] += 1
                if n3:
                    out.append((lvl3, n3 if n3 is not None else WINDOW))
                    split_total += min(n3, WINDOW)
                if n2 is not None and split_total >= n2:
                    break
    return out


def crawl_adaptive(f: lib.Fetcher, states: list[str], page: int, budget: dict,
                   our_names: set[str]) -> list[dict]:
    """Pull whole buckets and cut the cosmetics slice LOCALLY.

    One request returns 1,000 companies, so a bulk pull is roughly a thousand times more
    quota-efficient than asking about one company at a time — and because every row is
    inspected anyway, matching MCA against the companies we already hold (`our_names`)
    is free rather than a second pass of per-name requests.
    """
    ckpt_path = lib.CACHE / "s03_buckets.json"
    done = json.loads(ckpt_path.read_text()) if ckpt_path.exists() else {}
    rows: list[dict] = []
    kept = 0
    for state in states:
        for filters, total in partition(f, state, budget):
            key = json.dumps(filters, sort_keys=True)
            start = done.get(key, 0)
            capped = min(total if total is not None else WINDOW, WINDOW)
            if start >= capped:
                continue
            if total is not None and total > WINDOW:
                print(f"    ! TRUNCATED {filters} — {total} rows, window is {WINDOW}",
                      flush=True)
            got = 0
            for off in range(start, capped, page):
                if budget["requests"] >= budget["max"]:
                    print("    request budget reached; resume with the same command")
                    if rows:
                        lib.upsert("raw_company", rows, "source_id, source_key")
                        kept += len(rows)
                    ckpt_path.write_text(json.dumps(done))
                    return kept
                params = {"api-key": lib.DATA_GOV_KEY, "format": "json",
                          "limit": page, "offset": off}
                params.update({f"filters[{k}]": v for k, v in filters.items()})
                try:
                    recs = f.get(API, params=params).json().get("records") or []
                except Exception as exc:
                    # one bad bucket must not end the crawl; store what we have and move on
                    print(f"    ! {filters} offset {off}: {str(exc)[:110]}", flush=True)
                    if rows:
                        lib.upsert("raw_company", rows, "source_id, source_key")
                        kept += len(rows)
                        rows = []
                    ckpt_path.write_text(json.dumps(done))
                    break
                budget["requests"] += 1
                if not recs:
                    break
                for rec in recs:
                    cin = (rec.get("corporate_identification_number") or "").strip().upper()
                    name = (rec.get("company_name") or "").strip()
                    nic_match, _, roles = is_cosmetics(cin, name)
                    # keep it if MCA says cosmetics, OR if it is a company we already hold
                    known = stem(name) in our_names
                    if nic_match or known or roles:
                        row = to_row(rec, nic_match, roles)
                        pl = json.loads(row["payload"])
                        pl["matched_existing_name"] = known
                        row["payload"] = lib.jsonb(pl)
                        rows.append(row)
                got += len(recs)
                done[key] = off + len(recs)
                if got and got % 5000 == 0:
                    print(f"    {filters.get('registered_state')} "
                          f"{filters.get('company_status','*')}: {done[key]}/{capped} "
                          f"-> {len(rows)} kept", flush=True)
            # Write BEFORE advancing the checkpoint on disk. A crash after the
            # checkpoint but before the upsert would mark the bucket scanned while
            # its rows were never stored, and the resume would skip them forever.
            if rows:
                lib.upsert("raw_company", rows, "source_id, source_key")
                kept += len(rows)
                rows = []
            ckpt_path.write_text(json.dumps(done))
            print(f"    {filters} done ({capped} scanned, {kept} kept in total)",
                  flush=True)
    if rows:
        lib.upsert("raw_company", rows, "source_id, source_key")
        kept += len(rows)
    ckpt_path.write_text(json.dumps(done))
    return kept


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["bulk", "names", "crawl"], default="bulk")
    ap.add_argument("--page", type=int, default=1000, help="rows per request")
    ap.add_argument("--max-requests", type=int, default=900,
                    help="quota guard; the checkpoint makes a stopped run resumable")
    ap.add_argument("--limit", type=int, default=8000, help="companies to look up")
    ap.add_argument("--all-names", action="store_true",
                    help="also ask about names with no corporate suffix (MCA cannot see them)")
    ap.add_argument("--states", nargs="*", default=WEDGE_STATES)
    ap.add_argument("--budget", type=int, default=400, help="max requests per state per run")
    ap.add_argument("--delay", type=float, default=0.4)
    ap.add_argument("--restart", action="store_true", help="ignore saved offsets")
    a = ap.parse_args()

    if lib.DATA_GOV_KEY_IS_SHARED:
        print("  ! using the published shared demo key: 10 rows/request and globally "
              "rate-limited. Set DATA_GOV_IN_KEY in cosmetics/.env.")

    if a.mode == "bulk":
        with lib.pg() as c, c.cursor() as cur:
            cur.execute("SELECT DISTINCT legal_name FROM raw_company "
                        "WHERE source_id <> 's03_mca' AND legal_name IS NOT NULL")
            our_names = {stem(r[0]) for r in cur.fetchall() if r[0]}
        print(f"  {len(our_names)} existing company names to match MCA rows against")
        f = lib.Fetcher(SOURCE, delay=a.delay, timeout=60.0,
                        max_attempts=3, backoff_base=8.0, backoff_max=60.0)
        budget = {"requests": 0, "max": a.max_requests}
        try:
            kept = crawl_adaptive(f, a.states, a.page, budget, our_names)
        finally:
            f.close()
        with lib.pg() as c, c.cursor() as cur:
            cur.execute("""SELECT count(*),
                             count(*) FILTER (WHERE (payload->>'nic_match')::bool),
                             count(*) FILTER (WHERE (payload->>'matched_existing_name')::bool)
                           FROM raw_company WHERE source_id='s03_mca'""")
            tot, nic, known = cur.fetchone()
        print(f"S3 done: {kept} written this run in {budget['requests']} requests "
              f"({tot} MCA rows in total)")
        print(f"  {nic} matched on the NIC embedded in the CIN, "
              f"{known} matched a company we already hold")
        return 0

    if a.mode == "names":
        cands = candidates(a.limit, not a.all_names)
        print(f"  {len(cands)} companies to look up "
              f"({'all names' if a.all_names else 'corporate-suffix names only — MCA holds no '
               'proprietorships or partnerships'})")
        # data.gov.in throttles rather than hard-blocks; a short capped backoff keeps
        # thousands of lookups moving instead of parking on a 900s wall
        f = lib.Fetcher(SOURCE, delay=a.delay, timeout=25.0,
                        max_attempts=3, backoff_base=4.0, backoff_max=30.0)
        try:
            rows, st = lookup_names(f, cands, a.delay)
        finally:
            f.close()
        n = lib.upsert("raw_company", rows, "source_id, source_key")
        adverse = st.pop("adverse", [])
        if adverse:
            with lib.pg() as c, c.cursor() as cur:
                cur.executemany(
                    "INSERT INTO adverse_signal (entity_id, signal_type, detail, "
                    "source_id, observed_at) VALUES (%s,%s,%s,%s, now())",
                    [(cin, "mca_status_not_active",
                      lib.jsonb({"company_status": stt}), SOURCE) for cin, stt in adverse])
        print(f"S3 done: {n} MCA companies written from {st['asked']} lookups "
              f"({st['requests']} requests)")
        print(f"  matched {st['hit']}, no MCA record {st['miss']}")
        print(f"  corroboration: pincode {st['pincode_match']}, state {st['state_match']}, "
              f"STATE CONFLICT {st['state_conflict']} (same name, different state — "
              f"left for Stage 3 to refuse), unverified {st['unverified']}")
        print(f"  adverse (status not Active): {len(adverse)}")
        return 0

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
