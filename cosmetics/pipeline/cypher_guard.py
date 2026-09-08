"""Validate and execute Cypher the AGENT wrote.

This is the path that gives the agent real agency: it decides what it needs and fetches
exactly that, including aggregation and ranking no template anticipates. The whitelist
cannot express those questions, and pretending otherwise capped the product.

The guarantee changes shape rather than disappearing. It stops being structural ("no
generated query can exist") and becomes conditional on the CLAIM being made:

  * a regulatory assertion — is this legal, who is licensed, what standard applies — may
    only come from a reviewed template, because a query no human read cannot carry that
    weight however well it validates;
  * an exploratory answer may come from generated Cypher, mechanically constrained,
    always returned alongside the query that produced it so it can be audited.

Every rejection explains WHY and names the offending fragment, so the agent repairs its
query instead of dead-ending or quietly answering from memory.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib  # noqa: E402

MAX_LIMIT = 1000
DEFAULT_LIMIT = 100
TIMEOUT_S = 20

# Anything that could change the graph, read the filesystem, or reach outside the
# database. Matched on word boundaries against a comment-stripped query.
FORBIDDEN = {
    "CREATE": "creates data", "MERGE": "creates data", "DELETE": "deletes data",
    "DETACH": "deletes data", "SET": "modifies data", "REMOVE": "modifies data",
    "DROP": "drops schema", "LOAD": "reads external data (LOAD CSV)",
    "FOREACH": "can perform writes", "CALL{": "subquery writes are not distinguishable here",
}
# Procedure namespaces that can write, mutate config, or leak the environment.
FORBIDDEN_CALLS = re.compile(
    r"\bCALL\s+(apoc\.(?!coll\.|text\.|convert\.)|dbms\.|db\.create|db\.index\.\w+\.(create|drop)|"
    r"db\.awaitIndex|gds\.)", re.I)

DERIVED_EDGES = {"SUBSTITUTE_FOR", "SERVES_CATEGORY", "IN_CLUSTER_WITH",
                 "LIKELY_MANUFACTURES_FOR", "CAN_FILL"}

# Words that mean the user is asking whether something is permitted, not exploring.
COMPLIANCE_WORDS = re.compile(
    r"\b(legal|allowed|permitted|prohibit\w*|banned|restrict\w*|licen[cs]\w*|complian\w*|"
    r"regulat\w*|COS-?[589]|mandator\w*|standard|must\b|require\w*)\b", re.I)


@dataclass
class Verdict:
    ok: bool
    cypher: str = ""
    reason: str = ""
    offending: str = ""
    fix: str = ""
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = {"ok": self.ok, "cypher": self.cypher}
        if not self.ok:
            d |= {"rejected_because": self.reason, "offending": self.offending,
                  "how_to_fix": self.fix}
        if self.notes:
            d["notes"] = self.notes
        return d


def _strip(q: str) -> str:
    """Comments and string literals removed, so keywords inside them cannot trip or evade
    the checks (a company named 'CREATE LABS' must not look like a write)."""
    q = re.sub(r"//[^\n]*", " ", q)
    q = re.sub(r"/\*.*?\*/", " ", q, flags=re.S)
    q = re.sub(r"'(?:[^'\\]|\\.)*'", "''", q)
    q = re.sub(r'"(?:[^"\\]|\\.)*"', '""', q)
    return q


def is_compliance_question(question: str | None) -> bool:
    return bool(question and COMPLIANCE_WORDS.search(question))


def validate(cypher: str, *, question: str | None = None,
             max_limit: int = MAX_LIMIT) -> Verdict:
    raw = (cypher or "").strip().rstrip(";")
    if not raw:
        return Verdict(False, reason="empty query", fix="Provide a Cypher query.")
    body = _strip(raw)

    if ";" in body.strip().rstrip(";"):
        return Verdict(False, raw, "multiple statements are not allowed", ";",
                       "Send one statement.")

    up = body.upper()
    for kw, why in FORBIDDEN.items():
        pattern = re.escape(kw) if kw.endswith("{") else rf"\b{re.escape(kw)}\b"
        if re.search(pattern, up):
            return Verdict(False, raw, f"query is not read-only — `{kw}` {why}", kw,
                           "This path is read-only. Use MATCH/OPTIONAL MATCH/WITH/RETURN. "
                           "Changing the graph is done by the pipeline, never by a query.")
    m = FORBIDDEN_CALLS.search(body)
    if m:
        return Verdict(False, raw, "procedure namespace is not permitted", m.group(0),
                       "Only read procedures such as db.index.vector.queryNodes are allowed.")

    notes: list[str] = []

    # Hard rule 2 — an EU annex must never answer an India question, and vice versa.
    if re.search(r"\bRegulatoryRule\b", body) and not re.search(r"jurisdiction", body, re.I):
        return Verdict(
            False, raw, "touches RegulatoryRule without constraining jurisdiction",
            "RegulatoryRule",
            "Add a jurisdiction filter, e.g. {jurisdiction:'IN'} or "
            "WHERE r.jurisdiction = 'EU'. EU annex rules have no force in India, so a "
            "query that mixes them produces a confidently wrong compliance answer.")

    # Hard rule 3 — inference may rank and suggest, never decide permission.
    if is_compliance_question(question):
        used = sorted(e for e in DERIVED_EDGES if re.search(rf"\b{e}\b", up))
        if used:
            return Verdict(
                False, raw, "a compliance question may not rest on derived (tier 4) edges",
                ", ".join(used),
                "These edges are inferred, not observed. For a licence or restriction "
                "question traverse Facility-[:LICENSED_UNDER]->Licence or "
                "Ingredient-[:RESTRICTED_BY]->RegulatoryRule, or use the reviewed template.")
        notes.append("Compliance-shaped question answered by generated Cypher: present "
                     "this as exploratory and confirm regulatory claims via a template.")

    # Bounded, always.
    lim = re.search(r"\bLIMIT\s+(\d+)\s*$", body, re.I)
    if lim:
        n = int(lim.group(1))
        if n > max_limit:
            raw = re.sub(r"(\bLIMIT\s+)\d+\s*$", rf"\g<1>{max_limit}", raw, flags=re.I)
            notes.append(f"LIMIT {n} clamped to {max_limit}.")
    elif re.search(r"\bRETURN\b", up):
        # An aggregate collapses to a handful of rows; anything else could stream the graph.
        if re.search(r"\b(COUNT|SUM|AVG|MIN|MAX|COLLECT)\s*\(", up):
            notes.append("No LIMIT, but the query aggregates so the result set is bounded.")
        else:
            raw = f"{raw}\nLIMIT {DEFAULT_LIMIT}"
            notes.append(f"No LIMIT given; appended LIMIT {DEFAULT_LIMIT}.")
    return Verdict(True, raw, notes=notes)


def run(cypher: str, *, question: str | None = None, max_limit: int = MAX_LIMIT) -> dict:
    """Validate then execute. Read-only twice over: the guard above, and a session that
    the server itself will not let write."""
    v = validate(cypher, question=question, max_limit=max_limit)
    if not v.ok:
        return {"answered_by": "generated_cypher", "ok": False, **v.as_dict(), "rows": []}

    from neo4j import READ_ACCESS

    drv = lib.neo4j_driver()
    try:
        with drv.session(default_access_mode=READ_ACCESS) as s:
            res = s.run(v.cypher, timeout=TIMEOUT_S)
            rows = [dict(r) for r in res]
    except Exception as exc:
        return {"answered_by": "generated_cypher", "ok": False, "cypher": v.cypher,
                "rejected_because": "query failed at execution",
                "offending": str(exc)[:300],
                "how_to_fix": "Check labels and property names against `--schema`.",
                "rows": []}
    finally:
        drv.close()
    return {"answered_by": "generated_cypher", "ok": True, "cypher": v.cypher,
            "rows": rows, "row_count": len(rows), "notes": v.notes,
            "trust": "EXPLORATORY — this query was generated, not reviewed. Regulatory "
                     "claims must be confirmed through a template."}
