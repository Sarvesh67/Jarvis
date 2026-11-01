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
# TRUST_PLAN §6.5: rank by fit, then by the FACET that matters for the question (licence
# for "who can make", activity for suppliers). The grade and the old L-number never sort:
# `trust DESC` once put the 3.7% of firms at L4 on top of every list.
NEVER_SORTS = re.compile(r"\b(trust|trust_level|trust_grade|grade|grade_label|trust_rungs)\b")


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


# ------------------------------------------------------------------ T1: grade and facets

from trust import facets as F  # noqa: E402
from trust import grade as G  # noqa: E402

TODAY = date(2026, 10, 31)


def ev(facet, signal, family=None, tier=1, counts=True, observed=None, expiry=None, text="x",
       **value):
    return {"facet": facet, "signal_type": signal, "publisher_family": family, "tier": tier,
            "counts": counts, "observed_at": observed, "expiry_at": expiry,
            "public_text": text, "visibility": "public", "value": value}


UDYAM = ev("identity", "registry_entry", "udyam")
MCA = ev("identity", "registry_entry", "mca")
COUNCIL = ev("identity", "directory_listing", "chemexcil", tier=2)
FAIR = ev("identity", "directory_listing", "fair:HPCI India", tier=2)
SITE = ev("identity", "identifier_claimed", None, tier=3, counts=False)


@pytest.mark.parametrize("rows,code", [
    ([], "G0"),
    ([COUNCIL], "G0"),                       # a directory never establishes identity
    ([COUNCIL, FAIR], "G0"),                 # nor do two of them
    ([SITE, COUNCIL], "G0"),                 # nor the firm's own website
    ([UDYAM], "G1"),
    ([UDYAM, UDYAM], "G1"),                  # one registry, however many rows
    ([UDYAM, COUNCIL], "G2"),
    ([UDYAM, MCA, COUNCIL], "G3"),
    ([ev("adverse", "mca_struck_off", "mca", text="MCA status: Struck off, as of 2026-09-06 (x)"),
      COUNCIL], "flagged"),
    ([ev("adverse", "mca_struck_off", "mca"), UDYAM], "G1"),   # a live registry outranks it
])
def test_grade_rules(rows, code):
    assert G.grade(rows, TODAY)["code"] == code


def test_grade_is_monotonic():
    """Adding evidence never lowers a grade (TRUST_PLAN §2.8)."""
    pool = [UDYAM, MCA, COUNCIL, FAIR, SITE]
    rank = {c: i for i, c in enumerate(G.ORDER)}
    import itertools
    for n in range(len(pool)):
        for combo in itertools.combinations(pool, n):
            base = rank[G.grade(list(combo), TODAY)["code"]]
            for extra in pool:
                assert rank[G.grade(list(combo) + [extra], TODAY)["code"]] >= base


def test_gst_verified_is_g4_only_while_fresh():
    fresh = ev("identity", "gst_verified", "gstn", observed=date(2026, 10, 29))
    assert G.grade([fresh, COUNCIL], TODAY) == {
        "code": "G4", "publishers": ["chemexcil", "gstn"], "label": "GST active · checked 2 d ago"}
    stale = ev("identity", "gst_verified", "gstn", observed=date(2026, 9, 1))
    assert G.grade([stale], TODAY)["code"] == "G1"     # decays to the grade it would have


def test_expired_evidence_counts_for_nothing():
    lapsed = ev("identity", "registry_entry", "nabl", expiry=date(2026, 9, 10))
    assert G.grade([lapsed], TODAY)["code"] == "G0"
    old_fair = ev("activity", "exhibited", "fair:X", tier=2, expiry=date(2026, 1, 1))
    assert F.facet([old_fair], "activity", TODAY)["state"] == "unknown"


def test_facet_states():
    assert F.facet([], "licence", TODAY) == {"state": "unknown", "note": F.UNKNOWN["licence"]}
    site = ev("activity", "website_live", tier=3, text="Website live")
    mca = ev("activity", "mca_active", "mca", text="MCA status Active")
    assert F.facet([site], "activity", TODAY) == {"state": "claimed", "note": "Website live"}
    assert F.facet([site, mca], "activity", TODAY) == {"state": "verified", "note": "MCA status Active"}


def test_private_rows_never_become_lines():
    hidden = dict(ev("identity", "registry_lapsed", "mca"), visibility="private", public_text=None)
    assert F.public_lines([hidden, UDYAM]) == ["identity: x"]


def test_every_chip_has_evidence(cur):
    """Each facet state on a card traces to an evidence row (TRUST_PLAN §10.2)."""
    for facet in F.FACETS:
        n = _scalar(cur, """
            SELECT count(*) FROM trust_state t
            WHERE t.facets->%s->>'state' IN ('verified', 'claimed')
              AND NOT EXISTS (SELECT 1 FROM trust_evidence v WHERE v.entity_id = t.entity_id
                              AND v.facet = %s AND v.counts)""", facet, facet)
        assert n == 0, f"{n} {facet} chips with no evidence row behind them"
    n = _scalar(cur, """SELECT count(*) FROM trust_state t WHERE t.trust_grade NOT IN ('G0', 'flagged')
                        AND NOT EXISTS (SELECT 1 FROM trust_evidence v WHERE v.entity_id = t.entity_id
                                        AND v.facet = 'identity' AND v.counts
                                        AND v.signal_type IN ('registry_entry', 'gst_verified'))""")
    assert n == 0, f"{n} registered grades with no registry row behind them"


def test_flagged_never_beside_a_live_registry(cur):
    n = _scalar(cur, """SELECT count(*) FROM trust_state t WHERE t.trust_grade = 'flagged'
                        AND EXISTS (SELECT 1 FROM trust_evidence v WHERE v.entity_id = t.entity_id
                                    AND v.signal_type = 'registry_entry' AND v.counts)""")
    assert n == 0


def test_adverse_rows_name_live_entities(cur):
    """An adverse fact is about an ENTITY. S3 once keyed them by CIN (TRUST_PLAN §10.4)."""
    n = _scalar(cur, """SELECT count(*) FROM adverse_signal a WHERE NOT EXISTS
                        (SELECT 1 FROM trust_state t WHERE t.entity_id = a.entity_id)""")
    assert n == 0, f"{n} adverse rows name no entity"


# ------------------------------------------------------------------ why a firm is only Listed

from datetime import datetime as _dt  # noqa: E402

from trust import evidence as EV  # noqa: E402


def _rec(source_id, payload=None, in_force=False):
    return {"source_id": source_id, "source_key": "k", "payload": payload or {},
            "retrieved_at": _dt(2026, 9, 5), "cin": None, "gstin": None, "tier": 2,
            "family": families.family_of(source_id, payload or {}), "in_force": in_force}


def _ent(records, pincodes=()):
    return {"records": records, "pincodes": set(pincodes), "adverse": [], "licences": [],
            "mca_adverse": None, "gst": []}


def _gap(e):
    rows = EV.rows_for("e", e, TODAY)
    return [r["public_text"] for r in rows if r["signal_type"] == "registry_gap"]


def test_listed_firm_says_it_is_our_gap():
    [text] = _gap(_ent([_rec("s09_chemexcil")]))
    assert "gap in our data, not a finding about the firm" in text
    assert "gives no address" in text and "never on name alone" in text


def test_gap_names_the_lapsed_certificate():
    [text] = _gap(_ent([_rec("s11_nabl", {"certificate_valid_till": "10/09/2026"})], ["380001"]))
    assert "lapsed on 10/09/2026" in text


def test_registered_firm_has_no_gap_line():
    assert _gap(_ent([_rec("s17b_udyam_api", {"udyam_registered": True}, in_force=True)])) == []


def test_expired_activity_is_not_listed_under_activity():
    rows = EV.rows_for("e", _ent([_rec("s08_exhibitors", {"event": "Expo", "year": "2025"})]), TODAY)
    lines = F.public_lines(rows, TODAY)
    assert "identity: Exhibited at Expo 2025" in lines
    assert not any(l.startswith("activity:") for l in lines)
