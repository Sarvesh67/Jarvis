"""Review queue CLI — the human half of Stage 3.

The spec is explicit that for the MSME long tail, `norm_name + pincode` is the PRIMARY
resolution path and the review queue must be designed and staffed accordingly, not
treated as an exception hopper. This is the tool that staffs it.

Decisions are applied the append-only way (hard rule 2): merging supersedes the losing
crosswalk row with a new one pointing at the surviving entity_id. Nothing is deleted, and
`superseded_by` records which decision replaced which.

    python -m pipeline.review --list           # what is pending, worst-ambiguity first
    python -m pipeline.review --show 42        # the full feature vector for one pair
    python -m pipeline.review --merge 42       # same company  -> supersede + relink
    python -m pipeline.review --distinct 42    # different     -> record and close
    python -m pipeline.review --auto-distinct-below 0.80
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib  # noqa: E402


def _rows(sql: str, params: tuple = ()) -> list[dict]:
    with lib.pg() as c, c.cursor() as cur:
        cur.execute(sql, params)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]


def _split(key: str) -> tuple[str, str]:
    source_id, _, source_key = key.partition("|")
    return source_id, source_key


def _name_for(key: str) -> str:
    sid, skey = _split(key)
    r = _rows("SELECT legal_name, norm_name, pincode, state FROM raw_company "
              "WHERE source_id=%s AND source_key=%s", (sid, skey))
    if not r:
        return f"[{sid}] {skey}"
    x = r[0]
    loc = " / ".join(v for v in (x["pincode"], x["state"]) if v)
    return f"[{sid}] {x['legal_name']}" + (f"  ({loc})" if loc else "")


def cmd_list(limit: int, entity_type: str | None) -> None:
    where = "status='pending'" + (" AND entity_type=%s" if entity_type else "")
    params = (entity_type,) if entity_type else ()
    rows = _rows(f"SELECT id, entity_type, left_key, right_key, score, features "
                 f"FROM review_queue WHERE {where} ORDER BY score ASC LIMIT {limit}", params)
    print(f"  {len(rows)} pending (most ambiguous first)\n")
    for r in rows:
        f = r["features"] or {}
        why = f.get("reason") or ", ".join(f"{k}={v}" for k, v in f.items() if k != "reason")
        print(f"  #{r['id']:<5} {r['entity_type']:<9} score {float(r['score']):.3f}  {why}")
        print(f"        L: {_name_for(r['left_key'])}")
        print(f"        R: {_name_for(r['right_key'])}\n")


def cmd_show(rid: int) -> None:
    rows = _rows("SELECT * FROM review_queue WHERE id=%s", (rid,))
    if not rows:
        print(f"  no review item #{rid}")
        return
    r = rows[0]
    print(json.dumps({k: str(v) for k, v in r.items()}, indent=2))
    for side in ("left_key", "right_key"):
        sid, skey = _split(r[side])
        detail = _rows("SELECT source_id, legal_name, norm_name, cin, gstin, address_raw, "
                       "pincode, district, state, website, roles_claimed FROM raw_company "
                       "WHERE source_id=%s AND source_key=%s", (sid, skey))
        print(f"\n  {side}:")
        for k, v in (detail[0] if detail else {}).items():
            if v:
                print(f"    {k:14s} {v}")


def apply_merge(rid: int, decided_by: str) -> bool:
    rows = _rows("SELECT * FROM review_queue WHERE id=%s AND status='pending'", (rid,))
    if not rows:
        print(f"  #{rid}: not pending")
        return False
    r = rows[0]
    lsid, lkey = _split(r["left_key"])
    rsid, rkey = _split(r["right_key"])
    et = r["entity_type"] if r["entity_type"] != "operates" else "facility"

    live = _rows("SELECT id, source_id, source_key, entity_id FROM crosswalk "
                 "WHERE entity_type=%s AND superseded_by IS NULL "
                 "AND ((source_id=%s AND source_key=%s) OR (source_id=%s AND source_key=%s))",
                 (et, lsid, lkey, rsid, rkey))
    if len(live) < 2:
        print(f"  #{rid}: both sides must have a live crosswalk row (found {len(live)})")
        return False
    survivor = min(live, key=lambda x: x["id"])["entity_id"]
    with lib.pg() as c, c.cursor() as cur:
        for row in live:
            if row["entity_id"] == survivor:
                continue
            cur.execute(
                "INSERT INTO crosswalk (entity_type, source_id, source_key, entity_id, "
                "method, score) VALUES (%s,%s,%s,%s,'manual',%s) RETURNING id",
                (et, row["source_id"], row["source_key"], survivor, r["score"]))
            new_id = cur.fetchone()[0]
            cur.execute("UPDATE crosswalk SET superseded_by=%s WHERE id=%s",
                        (new_id, row["id"]))
        cur.execute("UPDATE review_queue SET status='merge', decided_by=%s, decided_at=now() "
                    "WHERE id=%s", (decided_by, rid))
    print(f"  #{rid}: merged into {survivor}")
    return True


def apply_distinct(rid: int, decided_by: str) -> bool:
    with lib.pg() as c, c.cursor() as cur:
        cur.execute("UPDATE review_queue SET status='distinct', decided_by=%s, "
                    "decided_at=now() WHERE id=%s AND status='pending'", (rid,))
    print(f"  #{rid}: recorded as distinct")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--entity-type")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--show", type=int)
    ap.add_argument("--merge", type=int)
    ap.add_argument("--distinct", type=int)
    ap.add_argument("--auto-distinct-below", type=float,
                    help="close everything under this score as distinct")
    ap.add_argument("--by", default="operator")
    a = ap.parse_args()

    if a.show:
        cmd_show(a.show)
    elif a.merge:
        apply_merge(a.merge, a.by)
    elif a.distinct:
        apply_distinct(a.distinct, a.by)
    elif a.auto_distinct_below is not None:
        ids = [r["id"] for r in _rows("SELECT id FROM review_queue WHERE status='pending' "
                                      "AND score < %s", (a.auto_distinct_below,))]
        with lib.pg() as c, c.cursor() as cur:
            cur.executemany("UPDATE review_queue SET status='distinct', decided_by=%s, "
                            "decided_at=now() WHERE id=%s", [(a.by, i) for i in ids])
        print(f"  closed {len(ids)} item(s) below {a.auto_distinct_below} as distinct")
    else:
        cmd_list(a.limit, a.entity_type)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
