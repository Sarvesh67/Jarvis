"""Filtered vendor search over the MSME registry — always returns a bounded, ranked shortlist.

    msme/.venv/bin/python -m msme.search --need "cotton yarn" --role supplier \
        --state gujarat --district surat --gst-verified --top 20

Runs entirely in DuckDB over msme/out/firms.parquet (no LLM, no graph) so it is cheap enough
to run on every query. The graph is only touched AFTER this narrows 500k -> top-K, when the
shortlist is promoted to Cognee for the agent to reason over.

The "reasonable number" guarantee has three levers:
  1. HARD FILTERS remove the vast majority up front (material role + geography + trust).
  2. TIERED GEO FALLBACK widens pincode -> district -> state -> national ONLY until `top` is met,
     so a dense cluster never floods and a sparse one still returns something.
  3. RANK + TOP-K caps the output and logs how many matched vs were dropped (no silent caps).
"""
from __future__ import annotations

import argparse
import json
import pathlib

import duckdb

from . import nic as N

BASE = pathlib.Path(__file__).resolve().parent
PARQUET = BASE / "out" / "firms.parquet"

GEO_TIERS = ["pincode", "district", "state", "national"]


def _resolve_material(need: str, vocab: N.Vocabulary) -> str | None:
    return vocab.snap(need)


def _role_predicate(role: str, material: str) -> str:
    """A supplier PRODUCES the material (it's in outputs); a buyer CONSUMES it (in inputs)."""
    col = "outputs" if role == "supplier" else "inputs"
    # list columns are stored as JSON strings -> substring match on the quoted slug is exact enough
    return f"{col} LIKE '%\"{material}\"%'"


def _geo_predicate(tier: str, state: str, district: str, pincode: str) -> str:
    if tier == "pincode" and pincode:
        return f"pincode = '{pincode}'"
    if tier == "district" and district:
        return f"lower(district) = lower('{district}')"
    if tier == "state" and state:
        return f"lower(state) = lower('{state}')"
    return "1=1"  # national


def search(need: str, role: str, *, state="", district="", pincode="",
           gst_verified=False, top=20, exclude_udyam=""):
    vocab = N.Vocabulary()
    material = _resolve_material(need, vocab)
    if not material:
        print(f"could not map need '{need}' to a known material; "
              f"add it to seeds/materials.json. Known: {list(vocab.materials)[:12]}...")
        return []

    con = duckdb.connect()
    base = [_role_predicate(role, material)]
    if gst_verified:
        base.append("lower(gst_status) = 'verified'")
    if exclude_udyam:
        base.append(f"udyam <> '{exclude_udyam}'")

    rows, used_tier = [], None
    for tier in GEO_TIERS:
        if tier == "pincode" and not pincode:
            continue
        if tier == "district" and not district:
            continue
        if tier == "state" and not state:
            continue
        where = " AND ".join(base + [_geo_predicate(tier, state, district, pincode)])
        q = f"""
            SELECT udyam, name, district, state, pincode, gst_status, reg_date, outputs, inputs
            FROM read_parquet('{PARQUET}')
            WHERE {where}
            ORDER BY (lower(gst_status)='verified') DESC, reg_date DESC
            LIMIT {top * 3}
        """
        rows = con.execute(q).fetchall()
        used_tier = tier
        if len(rows) >= top:
            break  # this tier already satisfies the target — don't widen further

    cols = [c[0] for c in con.description]
    matched = len(rows)
    result = [dict(zip(cols, r)) for r in rows[:top]]
    print(f"need='{need}' -> material='{material}', role={role}, geo_tier={used_tier}: "
          f"{matched} matched, returning top {len(result)}"
          + (f" (dropped {matched - len(result)})" if matched > len(result) else ""))
    return result


# ---------------------------------------------------------------- graph traversal (FalkorDB)
# SELLS_TO / BUYS_FROM are never stored — they are traversals over the compact PRODUCES/CONSUMES
# backbone (see graph.py). The DuckDB search above is the cheap pre-filter; these run over the graph.

def _falkor():
    from falkordb import FalkorDB
    return FalkorDB(host="127.0.0.1", port=6379).select_graph("msme_graph")


def graph_search(need: str, role: str, *, state="", district="", gst_verified=False, top=20):
    """Find firms that PRODUCE (supplier) or CONSUME (buyer) the requested material, via the graph."""
    vocab = N.Vocabulary()
    material = _resolve_material(need, vocab)
    if not material:
        print(f"could not map need '{need}' to a known material")
        return []
    rel = "PRODUCES" if role == "supplier" else "CONSUMES"
    where = ["m.slug = $mat"]
    if state:
        where.append("toLower(e.state) = toLower($state)")
    if district:
        where.append("toLower(e.district) = toLower($district)")
    if gst_verified:
        where.append("toLower(e.gst_status) = 'verified'")
    q = (f"MATCH (e:Enterprise)-[:CLASSIFIED_AS]->(:NICClass)-[:{rel}]->(m:Material) "
         f"WHERE {' AND '.join(where)} "
         f"RETURN DISTINCT e.udyam, e.name, e.district, e.state, e.gst_status "
         f"ORDER BY (toLower(e.gst_status)='verified') DESC LIMIT {top}")
    rows = _falkor().query(q, params={"mat": material, "state": state, "district": district}).result_set
    print(f"[graph] need='{need}' -> material='{material}', role={role}: {len(rows)} firm(s)")
    return [{"udyam": r[0], "name": r[1], "district": r[2], "state": r[3], "gst_status": r[4]}
            for r in rows]


def counterparties(udyam: str, role: str, *, top=20):
    """Given a firm, traverse to its buyers / suppliers / competitors through shared materials."""
    if role == "buyers":       # firms that CONSUME what this firm PRODUCES
        pat = "(a)-[:CLASSIFIED_AS]->()-[:PRODUCES]->(m)<-[:CONSUMES]-()<-[:CLASSIFIED_AS]-(b)"
    elif role == "suppliers":  # firms that PRODUCE what this firm CONSUMES
        pat = "(a)-[:CLASSIFIED_AS]->()-[:CONSUMES]->(m)<-[:PRODUCES]-()<-[:CLASSIFIED_AS]-(b)"
    else:                       # competitors: firms that PRODUCE the same material
        pat = "(a)-[:CLASSIFIED_AS]->()-[:PRODUCES]->(m)<-[:PRODUCES]-()<-[:CLASSIFIED_AS]-(b)"
    q = (f"MATCH (a:Enterprise {{udyam:$u}}), {pat} WHERE b.udyam <> $u "
         f"RETURN DISTINCT b.udyam, b.name, b.district, collect(DISTINCT m.slug) AS via "
         f"ORDER BY size(via) DESC LIMIT {top}")
    rows = _falkor().query(q, params={"u": udyam}).result_set
    print(f"[graph] {role} of {udyam}: {len(rows)} firm(s)")
    return [{"udyam": r[0], "name": r[1], "district": r[2], "via": r[3]} for r in rows]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--need", help="material or product you want (free text)")
    ap.add_argument("--role", choices=["supplier", "buyer"], default="supplier",
                    help="supplier = who can sell me this; buyer = who needs this")
    ap.add_argument("--state", default="")
    ap.add_argument("--district", default="")
    ap.add_argument("--pincode", default="")
    ap.add_argument("--gst-verified", action="store_true")
    ap.add_argument("--exclude", default="", help="udyam id to exclude (yourself)")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--graph", action="store_true", help="run the FalkorDB traversal, not DuckDB")
    ap.add_argument("--of", default="", help="udyam id: traverse THIS firm's counterparties (graph)")
    ap.add_argument("--rel", choices=["suppliers", "buyers", "competitors"], default="suppliers",
                    help="with --of: which counterparties to traverse")
    args = ap.parse_args()

    if args.of:                       # firm-centric graph traversal
        res = counterparties(args.of, args.rel, top=args.top)
    elif args.graph:                  # material+role graph search
        res = graph_search(args.need, args.role, state=args.state, district=args.district,
                           gst_verified=args.gst_verified, top=args.top)
    else:                             # default: DuckDB filtered search
        if not args.need:
            ap.error("--need is required (unless using --of)")
        res = search(args.need, args.role, state=args.state, district=args.district,
                     pincode=args.pincode, gst_verified=args.gst_verified,
                     top=args.top, exclude_udyam=args.exclude)
    print(json.dumps(res, indent=2, default=str))


if __name__ == "__main__":
    main()
