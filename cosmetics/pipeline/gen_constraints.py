"""Generate ontology/constraints.cypher from ontology.yaml, then apply it.

constraints.cypher is GENERATED — never hand-edit it. The ontology is the single
source of truth for the schema (spec §1).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib  # noqa: E402

OUT = lib.ONTOLOGY / "constraints.cypher"


def generate() -> str:
    onto = lib.load_yaml("ontology.yaml")
    lines = ["// GENERATED from ontology.yaml by pipeline/gen_constraints.py — do not edit.", ""]
    for label, spec in onto["nodes"].items():
        key = spec.get("key") or []
        if not key:
            continue
        cname = f"{label.lower()}_key"
        props = ", ".join(f"n.{k}" for k in key)
        req = f"({props}) IS UNIQUE" if len(key) > 1 else f"{props} IS UNIQUE"
        lines.append(f"CREATE CONSTRAINT {cname} IF NOT EXISTS "
                     f"FOR (n:{label}) REQUIRE {req};")
        for idx in spec.get("indexes") or []:
            lines.append(f"CREATE INDEX {label.lower()}_{idx} IF NOT EXISTS "
                         f"FOR (n:{label}) ON (n.{idx});")
        vi = spec.get("vector_index")
        if vi:
            lines.append(
                f"CREATE VECTOR INDEX {label.lower()}_{vi['property']} IF NOT EXISTS\n"
                f"  FOR (n:{label}) ON (n.{vi['property']})\n"
                f"  OPTIONS {{indexConfig: {{`vector.dimensions`: {vi['dimensions']}, "
                f"`vector.similarity_function`: '{vi['similarity']}'}}}};")
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    cypher = generate()
    OUT.write_text(cypher)
    stmts = [s.strip() for s in cypher.split(";") if s.strip() and not s.strip().startswith("//")]
    print(f"  wrote {OUT.relative_to(lib.ROOT)} — {len(stmts)} statements")
    if a.apply:
        drv = lib.neo4j_driver()
        with drv.session() as s:
            for stmt in stmts:
                body = "\n".join(l for l in stmt.splitlines() if not l.strip().startswith("//"))
                if body.strip():
                    s.run(body)
        drv.close()
        print("  applied to Neo4j")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
