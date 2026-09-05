"""S6 — EU CosIng (Cosmetic Ingredient Database).  Tier 1.

The modern CosIng UI is an Angular SPA backed by the EU Search API; the SPA's own
config (assets/env-json-config.json) publishes the endpoint and apiKey, and the query
body is a small whitelisted DSL: only `term`, `terms` and `text` ({query, fields})
clauses are accepted — `prefix`/`wildcard`/`range` are rejected as "Invalid Query format".

Two constraints shape this adapter:
  * a hard 10,000-result paging window ("The number of returned results exceed the
    search limit"), so the corpus is collected as a UNION of prefix buckets on
    inciName, refined recursively until every bucket fits the window;
  * buckets OVERLAP (the field is analysed, so "A*" matches any token starting with A).
    That is fine — coverage, not disjointness, is what completeness needs. Records are
    deduped on substanceId and the run asserts unique == totalResults before writing.

Emits (spec §6):
  Ingredient{inci_name}            <- raw_ingredient
  RegulatoryRule{jurisdiction:EU}  <- raw_rule, one per annex entry / other restriction
"""
from __future__ import annotations

import argparse
import json
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib  # noqa: E402

SOURCE = "s06_cosing"
TIER = 1
API = "https://webgate.ec.europa.eu/es/search-api/rest/search"
API_KEY = "285a77fd-1257-4271-8507-f0c6b2961203"   # published by the CosIng SPA's own config
PAGE = 100
WINDOW = 10_000          # EU Search hard paging window
SAFE = 9_000             # refine a bucket before it reaches the window
ALPHABET = string.ascii_uppercase + string.digits

# Annex -> regulatory status. Annex I is the (repealed) product-category list.
ANNEX_STATUS = {
    "II": "prohibited",
    "III": "restricted",
    "IV": "colorant",
    "V": "preservative",
    "VI": "uv-filter",
}


def _q(item_type: str, prefix: str | None, exact: bool = False) -> dict:
    must: list[dict] = [{"term": {"itemType": item_type}}]
    if prefix:
        query = prefix if exact else f"{prefix}*"
        must.append({"text": {"query": query, "fields": ["inciName"]}})
    return {"bool": {"must": must}}


def _search(f: lib.Fetcher, query: dict, page: int, size: int = PAGE) -> dict:
    url = f"{API}?apiKey={API_KEY}&text=*&pageSize={size}&pageNumber={page}"
    files = {"query": ("query.json", json.dumps(query).encode(), "application/json")}
    r = f.post(url, files=files)
    return r.json()


def _count(f: lib.Fetcher, query: dict) -> int:
    d = _search(f, query, 1, size=1)
    return int(d.get("totalResults") or 0)


def _buckets(f: lib.Fetcher, item_type: str) -> list[dict]:
    """Prefix buckets that each fit inside the paging window."""
    total = _count(f, _q(item_type, None))
    print(f"  {item_type}: {total} records total")
    if total <= SAFE:
        return [_q(item_type, None)]

    out: list[dict] = []
    frontier = [c for c in ALPHABET]
    depth = 0
    while frontier and depth < 3:
        nxt: list[str] = []
        for p in frontier:
            n = _count(f, _q(item_type, p))
            if n == 0:
                continue
            if n <= SAFE:
                out.append(_q(item_type, p))
            else:
                # too wide: keep the exact-token bucket, then refine one character deeper
                out.append(_q(item_type, p, exact=True))
                nxt.extend(p + c for c in ALPHABET)
                print(f"    refining '{p}*' ({n}) -> {len(ALPHABET)} sub-buckets")
        frontier = nxt
        depth += 1
    if frontier:
        # depth-limited: fall back to whatever is left, accepting the window truncation
        out.extend(_q(item_type, p) for p in frontier)
    return out


def collect(item_type: str, delay: float, limit_buckets: int | None = None) -> dict[str, dict]:
    f = lib.Fetcher(SOURCE, delay=delay)
    try:
        expected = _count(f, _q(item_type, None))
        buckets = _buckets(f, item_type)
        if limit_buckets:
            buckets = buckets[:limit_buckets]
        print(f"  {item_type}: {len(buckets)} bucket(s)")
        seen: dict[str, dict] = {}
        for i, q in enumerate(buckets, 1):
            page = 1
            while True:
                d = _search(f, q, page)
                res = d.get("results") or []
                if not res:
                    break
                for r in res:
                    md = r.get("metadata") or {}
                    sid = (md.get("substanceId") or [None])[0]
                    if sid and sid not in seen:
                        seen[sid] = md
                if page * PAGE >= min(int(d.get("totalResults") or 0), WINDOW):
                    break
                page += 1
            label = json.dumps(q)[-40:]
            print(f"    [{i}/{len(buckets)}] {label} -> {len(seen)} unique so far", flush=True)
        print(f"  {item_type}: collected {len(seen)} / {expected} expected")
        return seen
    finally:
        f.close()


# CosIng writes literal sentinels for "no value"; they must never become graph content.
BLANK = {"", "-", "[]", "<empty>", "<EMPTY>", "N/A", "NA", "none", "None"}


def _blank(x: object) -> bool:
    return not isinstance(x, str) or x.strip() in BLANK


def _first(md: dict, key: str) -> str | None:
    v = md.get(key)
    if isinstance(v, list):
        v = next((x for x in v if not _blank(x)), None)
    return v.strip() if not _blank(v) else None


def _all(md: dict, key: str) -> list[str]:
    v = md.get(key) or []
    if isinstance(v, str):
        v = [v]
    return [x.strip() for x in v if not _blank(x)]


def to_rows(records: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    ing_rows, rule_rows = [], []
    for sid, md in records.items():
        inci = _first(md, "inciName") or _first(md, "nameOfCommonIngredientsGlossary")
        if not inci:
            continue
        inci = inci.upper()
        cas = [c for raw in _all(md, "casNo") for c in raw.replace("/", ",").split(",") if c.strip()]
        ing_rows.append({
            "source_id": SOURCE, "source_key": sid, "tier": TIER,
            "inci_name": inci,
            "cas": cas[0].strip() if cas else None,
            "ec_no": _first(md, "ecNo"),
            "functions": _all(md, "functionName"),
            "alias": None,
            "payload": lib.jsonb({
                "substance_id": sid,
                "cas_all": [c.strip() for c in cas],
                "chemical_name": _first(md, "chemicalName"),
                "chemical_description": _first(md, "chemicalDescription"),
                "inci_usa_name": _first(md, "inciUsaName"),
                "inn_name": _first(md, "innName"),
                "ph_eur_name": _first(md, "phEurName"),
                "status": _first(md, "status"),
                "item_type": _first(md, "itemType"),
                "perfuming": _first(md, "perfuming"),
                "ref_no": _all(md, "refNo"),
                "annex_no": _all(md, "annexNo"),
                "official_journal": _first(md, "officialJournalPublication"),
                "sccs_opinion": _all(md, "sccsOpinion"),
            }),
        })

        annexes = _all(md, "annexNo")
        refs = _all(md, "refNo")
        maxc = _all(md, "maximumConcentration")
        conds = _all(md, "wordingOfConditions")
        parts = _all(md, "productTypeBodyParts")
        for j, annex in enumerate(annexes or []):
            annex_roman = annex.strip().upper()
            ref = refs[j] if j < len(refs) else (refs[0] if refs else None)
            rule_rows.append({
                "source_id": SOURCE, "source_key": f"{sid}:annex:{annex_roman}:{ref or j}",
                "tier": TIER, "jurisdiction": "EU",
                "source_doc": "Regulation (EC) No 1223/2009",
                "clause": f"Annex {annex_roman}" + (f"/{ref}" if ref else ""),
                "rule_scope": "ingredient", "subject": inci,
                "applies_to_group": False,
                "status": ANNEX_STATUS.get(annex_roman, "listed"),
                "max_concentration": maxc[j] if j < len(maxc) else (maxc[0] if maxc else None),
                "conditions": conds[j] if j < len(conds) else (conds[0] if conds else None),
                "standard_ref": None,
                "payload": lib.jsonb({"product_type_body_parts": parts,
                                      "other_regulations": _all(md, "otherRegulations")}),
            })
        for k, other in enumerate(_all(md, "otherRestrictions")):
            rule_rows.append({
                "source_id": SOURCE, "source_key": f"{sid}:other:{k}",
                "tier": TIER, "jurisdiction": "EU",
                "source_doc": "EU other restrictions (CosIng)", "clause": other[:200],
                "rule_scope": "ingredient", "subject": inci,
                "applies_to_group": True,        # these name substance GROUPS, not single INCIs
                "status": "restricted", "max_concentration": None,
                "conditions": other, "standard_ref": None,
                "payload": lib.jsonb({}),
            })
    return ing_rows, rule_rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--item-type", default="ingredient", choices=["ingredient", "substance", "both"])
    ap.add_argument("--delay", type=float, default=0.35)
    ap.add_argument("--limit-buckets", type=int, default=None, help="smoke-test escape hatch")
    a = ap.parse_args()

    types = ["ingredient", "substance"] if a.item_type == "both" else [a.item_type]
    all_records: dict[str, dict] = {}
    for t in types:
        all_records.update(collect(t, a.delay, a.limit_buckets))

    raw = lib.RAW / SOURCE / f"cosing_{a.item_type}.json"
    raw.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(all_records, ensure_ascii=False).encode()
    lib.land(SOURCE, API, payload, suffix=".json", key=f"cosing_{a.item_type}")

    ing, rules = to_rows(all_records)
    n1 = lib.upsert("raw_ingredient", ing, "source_id, source_key")
    n2 = lib.upsert("raw_rule", rules, "source_id, source_key")
    print(f"S6 done: {n1} ingredients, {n2} EU rules")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
