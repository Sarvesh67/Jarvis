"""Catalog snapshots and drift detection.

The catalog (`catalog/`) carries two kinds of statement about the data:

  * **judgement** — what a source is, what it may be used to claim, how it lies to you.
    Hand-written. Only a person can write it and only a person should change it.
  * **measurement** — row counts, payload sizes, coverage percentages, freshness.
    Machine-read from the live stores. A person writing these by hand is how a catalog
    starts lying.

This module owns the measurements. It does **not** rewrite the cards — rewriting YAML
destroys the comments that carry the judgement, and a card that silently updates itself
cannot be reviewed. Instead it writes a dated snapshot and reports where the cards have
drifted away from the live stores, so the correction is a deliberate edit.

    python -m pipeline.snapshot              # write today's snapshot
    python -m pipeline.snapshot --drift      # compare cards against live, exit 1 on drift
    python -m pipeline.snapshot --print      # snapshot to stdout, write nothing
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import yaml

from lib import ROOT, neo4j_driver, pg

CATALOG = ROOT / "catalog"
SOURCES = CATALOG / "sources"
SNAPSHOTS = CATALOG / "snapshots"
# Evidence reads flat files. Neo4j is not one of its connectors and the catalog cards are
# YAML, so both are flattened to CSV here rather than taught to the site.
SITE_DATA = ROOT / "site" / "sources"

# A measured field may sit this far from the card before it counts as drift.
# Counts move when the pipeline is replayed; a card is not wrong for being a day stale.
DRIFT_TOLERANCE = 0.02


# ------------------------------------------------------------------ measurement
def graph_counts() -> dict:
    """Live node and relationship counts from Neo4j."""
    with neo4j_driver() as drv, drv.session() as s:
        nodes = {
            r["label"]: r["n"]
            for r in s.run(
                "MATCH (n) WITH labels(n)[0] AS label, count(*) AS n "
                "WHERE label IS NOT NULL RETURN label, n ORDER BY n DESC"
            )
        }
        edges = {
            r["rel"]: r["n"]
            for r in s.run(
                "MATCH ()-[r]->() RETURN type(r) AS rel, count(*) AS n ORDER BY n DESC"
            )
        }
        trust = {
            str(r["lvl"]): r["n"]
            for r in s.run(
                "MATCH (c:Company) RETURN c.trust_level AS lvl, count(*) AS n ORDER BY lvl"
            )
        }
    return {"nodes": nodes, "edges": edges, "company_trust_level": trust}


def postgres_counts() -> dict:
    """Row counts, per-source contribution, and landing freshness from Postgres."""
    with pg() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY n_live_tup DESC"
        )
        tables = {r[0]: r[1] for r in cur.fetchall()}

        cur.execute(
            "SELECT source_id, count(*) FROM raw_company GROUP BY source_id ORDER BY 2 DESC"
        )
        raw_company = {r[0]: r[1] for r in cur.fetchall()}

        cur.execute(
            "SELECT source_id, count(*), min(fetched_at)::date, max(fetched_at)::date "
            "FROM fetch_manifest GROUP BY source_id ORDER BY 2 DESC"
        )
        manifest = {
            r[0]: {"payloads": r[1], "first_seen": str(r[2]), "last_seen": str(r[3])}
            for r in cur.fetchall()
        }
    return {"tables": tables, "raw_company": raw_company, "fetch_manifest": manifest}


def raw_bytes() -> dict[str, int]:
    """On-disk size of each landed source directory."""
    out: dict[str, int] = {}
    raw = ROOT / "raw"
    if not raw.is_dir():
        return out
    for d in sorted(p for p in raw.iterdir() if p.is_dir()):
        out[d.name] = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
    return out


def snapshot() -> dict:
    return {
        "taken_on": str(date.today()),
        "graph": graph_counts(),
        "postgres": postgres_counts(),
        "raw_bytes": raw_bytes(),
    }


# ------------------------------------------------------------------ drift
def _cards() -> dict[str, dict]:
    return {
        p.stem: yaml.safe_load(p.read_text())
        for p in sorted(SOURCES.glob("*.yaml"))
    }


def _off(card_val, live_val) -> bool:
    """True when a card figure has drifted meaningfully from the live one."""
    if card_val is None or live_val is None:
        return False
    if not isinstance(card_val, (int, float)) or not isinstance(live_val, (int, float)):
        return card_val != live_val
    if live_val == 0:
        return card_val != 0
    return abs(card_val - live_val) / live_val > DRIFT_TOLERANCE


PUBLISHER_KINDS = {"registry", "directory", "self", "reference"}


def drift(snap: dict) -> list[str]:
    """Every place a source card disagrees with the live stores.

    Cards name the store keys they should be measured against, in a `keys:` block.
    Guessing the key from the source id does not work: `s17` would prefix-match
    `s17b_udyam_api` and silently compare the Gujarat card against the national figure.
    A `keys` entry that is null means "this source has no row here" and is not checked —
    S14b, for instance, re-reads S14's landed HTML and owns no bytes of its own.
    """
    problems: list[str] = []
    raw_company = snap["postgres"]["raw_company"]
    manifest = snap["postgres"]["fetch_manifest"]
    sizes = snap["raw_bytes"]

    for stem, card in _cards().items():
        acq = card.get("acquisition") or {}
        vol = card.get("volume") or {}
        keys = card.get("keys") or {}

        if "keys" not in card:
            problems.append(f"{stem}: no `keys:` block — cannot be checked against the stores")
            continue

        # Trust counts corroboration by publisher, so a card without one would let its
        # rows be counted as independent of a source they were copied from (TRUST_PLAN §6.2).
        pub = card.get("publisher") or {}
        if not pub.get("family"):
            problems.append(f"{stem}: publisher.family is missing — trust cannot count it")
        if pub.get("kind") not in PUBLISHER_KINDS:
            problems.append(f"{stem}: publisher.kind={pub.get('kind')!r} is not one of "
                            f"{sorted(PUBLISHER_KINDS)}")

        # raw_rows against this source's contribution to raw_company
        k = keys.get("raw_company")
        if k:
            live = raw_company.get(k)
            if live is None:
                problems.append(f"{stem}: keys.raw_company={k!r} is not a source_id in raw_company")
            elif _off(vol.get("raw_rows"), live):
                problems.append(
                    f"{stem}: volume.raw_rows={vol.get('raw_rows')} but raw_company[{k}]={live}"
                )

        # payloads and freshness against fetch_manifest
        k = keys.get("fetch_manifest")
        if k:
            m = manifest.get(k)
            if m is None:
                problems.append(f"{stem}: keys.fetch_manifest={k!r} is not in fetch_manifest")
            else:
                if _off(acq.get("payloads"), m["payloads"]):
                    problems.append(
                        f"{stem}: acquisition.payloads={acq.get('payloads')} "
                        f"but fetch_manifest={m['payloads']}"
                    )
                for f in ("first_seen", "last_seen"):
                    if acq.get(f) and str(acq[f]) != m[f]:
                        problems.append(
                            f"{stem}: acquisition.{f}={acq[f]} but fetch_manifest={m[f]}"
                        )

        # bytes against the landed directory
        k = keys.get("raw_dir")
        if k:
            live = sizes.get(k)
            if live is None:
                problems.append(f"{stem}: keys.raw_dir={k!r} is not a directory under raw/")
            elif _off(acq.get("bytes"), live):
                problems.append(
                    f"{stem}: acquisition.bytes={acq.get('bytes')} but raw/{k}/ is {live}"
                )

    return problems


# ------------------------------------------------------------------ site feed
# Derived edges rank and suggest; they never answer a compliance question (hard rule 3).
# The site must show this next to the counts or it invites exactly the misreading the
# rule exists to prevent.
DERIVED_EDGES = {"LOCATED_IN", "SERVES_CATEGORY", "SUBSTITUTE_FOR", "SUPPLIES_PACKAGING",
                 "LIKELY_MAKES"}


def _csv(path: Path, header: list[str], rows: list[list]) -> None:
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def emit_site_data(snap: dict) -> list[Path]:
    """Flatten the snapshot and the catalog cards into CSV for the Evidence site."""
    written: list[Path] = []

    d = SITE_DATA / "catalog"
    g = snap["graph"]
    _csv(d / "nodes.csv", ["label", "n"], [[k, v] for k, v in g["nodes"].items()])
    _csv(d / "edges.csv", ["rel", "n", "derived"],
         [[k, v, k in DERIVED_EDGES] for k, v in g["edges"].items()])
    _csv(d / "trust.csv", ["trust_level", "companies"],
         [[k, v] for k, v in sorted(g["company_trust_level"].items())])
    _csv(d / "tables.csv", ["table_name", "rows"],
         [[k, v] for k, v in snap["postgres"]["tables"].items()])

    rows = []
    for stem, card in _cards().items():
        acq = card.get("acquisition") or {}
        vol = card.get("volume") or {}
        cov = card.get("coverage") or {}
        rows.append([
            card.get("id"), card.get("name"), card.get("tier"), card.get("status"),
            card.get("jurisdiction"), vol.get("raw_rows") or 0,
            acq.get("payloads") or 0, acq.get("bytes") or 0,
            cov.get("pct"), len(card.get("defects") or []),
            len(card.get("do_not") or []),
            # Literal "never", not an empty cell: an empty value in an otherwise-date
            # column is sniffed as a DATE and rendered as 1970-01-01, which reads as
            # "landed in 1970" rather than "never landed". The string forces VARCHAR.
            str(acq.get("last_seen") or "never"),
            stem,
        ])
    rows.sort(key=lambda r: (r[2] or 99, -(r[5] or 0)))
    _csv(d / "sources.csv",
         ["id", "name", "tier", "status", "jurisdiction", "raw_rows", "payloads",
          "bytes", "coverage_pct", "defects", "guardrails", "last_seen", "card"],
         rows)
    written.append(d)
    return written


# ------------------------------------------------------------------ cli
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--drift", action="store_true",
                    help="compare cards against live stores; exit 1 if they disagree")
    ap.add_argument("--print", dest="to_stdout", action="store_true",
                    help="print the snapshot, write nothing")
    args = ap.parse_args()

    snap = snapshot()

    if args.to_stdout:
        print(json.dumps(snap, indent=2))
        return 0

    if args.drift:
        problems = drift(snap)
        if not problems:
            print("catalog is in step with the live stores")
            return 0
        print(f"{len(problems)} card(s) drifted from the live stores:\n", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        print("\nEdit the card. Do not edit the data to match the card.", file=sys.stderr)
        return 1

    d = SNAPSHOTS / snap["taken_on"]
    d.mkdir(parents=True, exist_ok=True)
    (d / "snapshot.json").write_text(json.dumps(snap, indent=2) + "\n")
    (SNAPSHOTS / "latest.json").write_text(json.dumps(snap, indent=2) + "\n")

    n = sum(snap["graph"]["nodes"].values())
    e = sum(snap["graph"]["edges"].values())
    print(f"catalog/snapshots/{snap['taken_on']}/snapshot.json  —  {n:,} nodes, {e:,} edges")

    if (ROOT / "site").is_dir():
        for p in emit_site_data(snap):
            print(f"  fed {p.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
