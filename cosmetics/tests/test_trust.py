"""Trust layer invariants (TRUST_PLAN.md §6, §10).

The unit tests need nothing running. The store tests read Postgres and skip cleanly when
it is not up, like test_golden.py does for Neo4j.
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import lib  # noqa: E402
from trust import families  # noqa: E402

TEMPLATES = yaml.safe_load((ROOT / "pipeline" / "query_templates.yaml").read_text())
# Fields that describe our evidence about a firm. They may break ties after fit, never
# lead a ranking (TRUST_PLAN §6.5): an L4 council member once outranked every licensee.
TRUST_KEYS = re.compile(r"\b(trust\w*|grade\w*|licence_rank|activity_rank)\b")
NEVER_SORTS = re.compile(r"\b(trust|trust_level|trust_grade|grade|grade_rank)\b")


# ------------------------------------------------------------------ unit

def test_every_card_names_its_publisher():
    for p in sorted(families.CARDS.glob("*.yaml")):
        pub = (yaml.safe_load(p.read_text()) or {}).get("publisher") or {}
        assert pub.get("family"), f"{p.stem}: publisher.family missing"
        assert pub.get("kind") in {"registry", "directory", "self", "reference"}, p.stem


def test_one_registry_counts_once_and_own_site_never():
    assert families.family_of("s17_udyam", {}) == families.family_of("s17b_udyam_api", {})
    assert families.family_of("s14_sites", {}) is None
    assert families.family_of("s08_exhibitors", {"event": "HPCI India"}) == "fair:HPCI India"
    assert families.family_of("s08_exhibitors", {"event": "HPCI India"}) != \
        families.family_of("s08_exhibitors", {"event": "Cosmetics Ingredients Expo"})


def test_registry_in_force():
    assert families.registry_in_force("s03_mca", {"company_status": "Active"})
    for dead in ("Strike Off", "Dissolved", "Liquidated", "Amalgamated", "Converted to LLP"):
        assert not families.registry_in_force("s03_mca", {"company_status": dead}), dead
    today = date(2026, 10, 31)
    assert families.registry_in_force("s11_nabl", {"certificate_valid_till": "21/10/2028"}, today)
    assert not families.registry_in_force("s11_nabl", {"certificate_valid_till": "10/09/2026"}, today)
    assert families.registry_in_force("s17b_udyam_api", {})
    assert not families.registry_in_force("s09_chemexcil", {})       # a directory, not a registry


def _order_keys(cypher: str) -> list[str]:
    m = re.findall(r"ORDER BY (.+?) LIMIT", " ".join(cypher.split()))
    return [k.strip() for clause in m for k in clause.split(",")]


@pytest.mark.parametrize("t", TEMPLATES, ids=[t["id"] for t in TEMPLATES])
def test_trust_never_orders_results(t):
    keys = _order_keys(t["cypher"])
    if not keys:
        return
    assert not TRUST_KEYS.search(keys[0]), f"{t['id']}: ranks by evidence before fit: {keys}"
    assert not any(NEVER_SORTS.search(k) for k in keys), f"{t['id']}: sorts on trust: {keys}"


# ------------------------------------------------------------------ stores

@pytest.fixture(scope="module")
def cur():
    try:
        conn = lib.pg()
    except Exception as e:                                     # pragma: no cover
        pytest.skip(f"Postgres not reachable: {e}")
    with conn, conn.cursor() as c:
        yield c


def _scalar(cur, sql, *args):
    cur.execute(sql, args)
    return cur.fetchone()[0]


LIVE = """JOIN crosswalk x ON x.source_id = r.source_id AND x.source_key = r.source_key
           AND x.entity_type = 'company' AND x.superseded_by IS NULL"""


def test_no_inactive_cin_as_identity(cur):
    """A company MCA lists as struck off, dissolved or merged away gets no identity from it."""
    n = _scalar(cur, f"""
        SELECT count(*) FROM raw_company r {LIVE}
        JOIN trust_state t ON t.entity_id = x.entity_id
        WHERE r.source_id = 's03_mca' AND r.payload->>'company_status' <> 'Active'
          AND t.reasons::text LIKE '%%CIN ' || r.cin || '%%'""")
    assert n == 0, f"{n} entities show a CIN as identity that MCA lists as not Active"


def test_dead_company_is_flagged_or_reviewed(cur):
    n = _scalar(cur, f"""
        SELECT count(DISTINCT x.entity_id) FROM raw_company r {LIVE}
        WHERE r.source_id = 's03_mca'
          AND r.payload->>'company_status' IN ('Strike Off','Dissolved','Liquidated',
                                              'Converted to LLP and dissolved')
          AND NOT EXISTS (SELECT 1 FROM adverse_signal a WHERE a.entity_id = x.entity_id
                          AND a.signal_type LIKE 'mca_%%')
          AND NOT EXISTS (SELECT 1 FROM review_queue q WHERE q.left_key = 's03_mca|' || r.source_key
                          AND q.features->>'reason' = 'adverse_vs_live_registry')""")
    assert n == 0, f"{n} dead companies are neither flagged nor in review"


def _sources_with_rung4(cur) -> list[set]:
    cur.execute("""SELECT array_agg(DISTINCT x.source_id) FROM crosswalk x
                   JOIN trust_state t ON t.entity_id = x.entity_id
                   WHERE x.entity_type = 'company' AND x.superseded_by IS NULL
                     AND 4 = ANY(t.trust_rungs) GROUP BY x.entity_id""")
    return [set(r[0]) for r in cur.fetchall()]


def test_own_site_never_corroborates(cur):
    bad = [s for s in _sources_with_rung4(cur) if len(s - {"s14_sites"}) < 2
           and len({families.family_of(x, {}) for x in s} - {None}) < 2]
    assert not bad, f"{len(bad)} firms corroborated only by their own website, e.g. {bad[:3]}"


def test_one_registry_is_not_two_sources(cur):
    bad = [s for s in _sources_with_rung4(cur) if s <= {"s17_udyam", "s17b_udyam_api", "s14_sites"}]
    assert not bad, f"{len(bad)} firms corroborated by Udyam alone"
