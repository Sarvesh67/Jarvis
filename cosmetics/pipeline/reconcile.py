"""Stage 4 — reconciliation.  Spec §10.

For each (entity_id, property) with more than one distinct value:
  * read-time precedence is LOWEST tier wins, ties broken by most recent retrieved_at;
  * the winner is what Neo4j gets, plus `{property}_conflict` and `conflict_count`
    when same-tier values compete;
  * LOSERS ARE NEVER DELETED. Every competing value stays in property_claim and the
    API can surface it. In a compliance product, surfacing disagreement is a feature —
    a buyer who sees two addresses for one factory learns something true.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib  # noqa: E402

# properties lifted from raw_company onto the Company node
COMPANY_PROPS = ["legal_name", "norm_name", "cin", "gstin", "pan", "udyam_no", "iec",
                 "constitution", "website", "address_raw", "pincode", "district",
                 "state", "cluster_id", "geo_confidence"]
FACILITY_PROPS = ["firm_name", "norm_name", "address_raw", "pincode", "district",
                  "state", "cluster_id", "geo_confidence"]


def _rows(table: str, props: list[str], entity_type: str) -> list[dict]:
    cols = ", ".join(["r." + p for p in props])
    # only raw_company carries self-declared roles; facilities have none
    roles_col = "r.roles_claimed" if table == "raw_company" else "NULL::text[] AS roles_claimed"
    sql = (f"SELECT x.entity_id, r.source_id, r.tier, r.retrieved_at, {cols}, {roles_col} "
           f"FROM {table} r JOIN crosswalk x ON x.source_id = r.source_id "
           f"AND x.source_key = r.source_key AND x.entity_type = %s "
           f"AND x.superseded_by IS NULL")
    with lib.pg() as c, c.cursor() as cur:
        cur.execute(sql, (entity_type,))
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]


def reconcile(table: str, props: list[str], entity_type: str, persist: bool) -> dict[str, dict]:
    rows = _rows(table, props, entity_type)
    claims: dict[str, dict[str, list[tuple]]] = defaultdict(lambda: defaultdict(list))
    roles: dict[str, set] = defaultdict(set)
    sources: dict[str, set] = defaultdict(set)

    for r in rows:
        eid = r["entity_id"]
        sources[eid].add(r["source_id"])
        for role in (r.get("roles_claimed") or []):
            roles[eid].add(role)          # roles[] is ADDITIVE (hard rule 11)
        for p in props:
            v = r.get(p)
            if v in (None, ""):
                continue
            claims[eid][p].append((r["tier"], r["retrieved_at"], v, r["source_id"]))

    winners: dict[str, dict] = {}
    claim_rows: list[dict] = []
    conflicts = 0
    for eid, per_prop in claims.items():
        out: dict = {"company_id" if entity_type == "company" else "facility_id": eid}
        for p, vals in per_prop.items():
            distinct = {v for _, _, v, _ in vals}
            # lowest tier wins; ties -> most recent
            vals.sort(key=lambda t: (t[0], -t[1].timestamp()))
            best_tier, _, best_val, _ = vals[0]
            out[p] = best_val
            if len(distinct) > 1:
                same_tier = {v for t, _, v, _ in vals if t == best_tier}
                if len(same_tier) > 1:
                    out[f"{p}_conflict"] = True
                    out[f"{p}_conflict_count"] = len(distinct)
                    conflicts += 1
            for tier, ts, v, sid in vals:
                claim_rows.append({"entity_id": eid, "property": p,
                                   "value": json.dumps(v, default=str),
                                   "source_id": sid, "tier": tier, "retrieved_at": ts,
                                   "valid_from": None, "valid_to": None})
        out["roles"] = sorted(roles[eid])
        out["source_ids"] = sorted(sources[eid])
        out["source_count"] = len(sources[eid])
        out["min_tier"] = min(t for vs in per_prop.values() for t, _, _, _ in vs)
        winners[eid] = out

    if persist:
        with lib.pg() as c, c.cursor() as cur:
            cur.execute("DELETE FROM property_claim WHERE entity_id LIKE %s",
                        ("c_%" if entity_type == "company" else "f_%",))
        lib.upsert("property_claim", claim_rows, "id") if False else _insert_claims(claim_rows)
    print(f"  {entity_type}: {len(winners)} entities, {len(claim_rows)} property claims, "
          f"{conflicts} same-tier conflicts flagged")
    return winners


def _insert_claims(rows: list[dict]) -> None:
    if not rows:
        return
    cols = ["entity_id", "property", "value", "source_id", "tier", "retrieved_at",
            "valid_from", "valid_to"]
    with lib.pg() as c, c.cursor() as cur:
        cur.executemany(
            f"INSERT INTO property_claim ({', '.join(cols)}) "
            f"VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            [tuple(r[k] for k in cols) for r in rows])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    reconcile("raw_company", COMPANY_PROPS, "company", not a.dry_run)
    reconcile("raw_facility", FACILITY_PROPS, "facility", not a.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
