"""GST verification from the command line (TRUST_PLAN §10.3).

    COSMETICS_GST_PROVIDERS=stub .venv/bin/python -m trust.gst run-held
    .venv/bin/python -m trust.gst verify --gstin 24AAACB1234C1ZQ --trigger detail
    .venv/bin/python -m trust.gst search --entity c_ab12cd34
    .venv/bin/python -m trust.gst purge-stub

`run-held` is the end-to-end run: every GSTIN we hold goes through rung 0, the cache, the
budget and the provider, and a sample of firms without one goes through name search.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import lib  # noqa: E402
from trust.gst.service import Verifier, WAIT  # noqa: E402

HELD = """SELECT DISTINCT ON (r.gstin) x.entity_id, r.gstin FROM raw_company r
          JOIN crosswalk x ON x.source_id = r.source_id AND x.source_key = r.source_key
           AND x.entity_type = 'company' AND x.superseded_by IS NULL
          WHERE r.gstin IS NOT NULL ORDER BY r.gstin"""
# Firms a buyer might open with no GSTIN on record: registered, named, placed.
NO_GSTIN = """SELECT t.entity_id FROM trust_state t WHERE t.trust_grade IN ('G1', 'G2', 'G3')
              AND NOT EXISTS (SELECT 1 FROM raw_company r JOIN crosswalk x
                ON x.source_id = r.source_id AND x.source_key = r.source_key
               AND x.superseded_by IS NULL WHERE x.entity_id = t.entity_id AND r.gstin IS NOT NULL)
              ORDER BY md5(t.entity_id) LIMIT %s"""


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify"); v.add_argument("--gstin", required=True)
    v.add_argument("--entity"); v.add_argument("--trigger", default="detail", choices=sorted(WAIT))
    s = sub.add_parser("search"); s.add_argument("--entity", required=True)
    r = sub.add_parser("run-held"); r.add_argument("--trigger", default="batch", choices=sorted(WAIT))
    r.add_argument("--search", type=int, default=20, help="firms without a GSTIN to name-search")
    sub.add_parser("purge-stub")
    a = ap.parse_args()

    if a.cmd == "purge-stub":
        # The one deletion this layer allows: stub rows were never facts.
        with lib.pg() as c, c.cursor() as cur, c.transaction():
            for t in ("gst_record", "gstin_candidate", "verification_spend"):
                cur.execute(f"DELETE FROM {t} WHERE provider_class = 'stub'")
                print(f"  {t}: {cur.rowcount} stub rows removed")
        return 0

    ver = Verifier.from_env()
    if ver.router is None:
        print("  no provider configured (COSMETICS_GST_PROVIDERS): verification is not connected")
        return 1

    if a.cmd == "verify":
        eid = a.entity
        if not eid:
            with lib.pg() as c, c.cursor() as cur:
                cur.execute(HELD.replace("WHERE r.gstin IS NOT NULL", "WHERE r.gstin = %s"),
                            (a.gstin.upper(),))
                row = cur.fetchone()
            if not row:
                print("  that GSTIN is not on any record we hold; pass --entity")
                return 1
            eid = row[0]
        print(ver.on_trigger(eid, a.gstin, a.trigger))
        return 0

    if a.cmd == "search":
        for cand in ver.search(a.entity):
            print(f"  {cand['gstin']}  {cand['decision']:9s}  {cand['why']}")
        return 0

    with lib.pg() as c, c.cursor() as cur:
        cur.execute(HELD)
        held = cur.fetchall()
        cur.execute(NO_GSTIN, (a.search,))
        bare = [r[0] for r in cur.fetchall()]
    out = [ver.on_trigger(eid, g, a.trigger) for eid, g in held]
    print(f"  {len(held)} held GSTINs, trigger={a.trigger}:")
    for (st, why), n in sorted(Counter((o.status, o.reason) for o in out).items(),
                               key=lambda kv: -kv[1]):
        print(f"    {n:3d}  {st}" + (f"  ({why})" if why else ""))
    again = [ver.on_trigger(eid, g, a.trigger) for eid, g in held]
    print(f"  second pass served from cache: {sum(o.from_cache for o in again)}"
          f" of {sum(o.status in ('active', 'not_active', 'not_found') for o in again)}")
    dec = Counter()
    for eid in bare:
        dec.update(c["decision"] for c in ver.search(eid))
    print(f"  name search on {len(bare)} firms without a GSTIN: "
          + ", ".join(f"{k} {v}" for k, v in sorted(dec.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
