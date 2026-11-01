"""GST verification plumbing (TRUST_PLAN §4, §10.3) — on the stub provider.

The rule above all: a stub record verifies nothing. Every other test here checks one gate
of the order rung 0 -> cache -> budget -> provider, or the name-search link rule.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import lib  # noqa: E402
from pipeline.gstin import checksum_ok  # noqa: E402
from pipeline.normalize import norm_name  # noqa: E402
from trust import evidence, grade  # noqa: E402
from trust.gst import cache, link  # noqa: E402
from trust.gst.adapter import ProviderError, Taxpayer, check_char  # noqa: E402
from trust.gst.providers.stub import StubProvider  # noqa: E402
from trust.gst.router import Router, disagreement  # noqa: E402


def test_check_char_agrees_with_rung0():
    for first14 in ("27AAACG1234A1Z", "24ABCDE1234F1Z", "07AAAPZ1234C1Z"):
        assert checksum_ok(first14 + check_char(first14))


def test_stub_is_deterministic_and_valid():
    s = StubProvider()
    assert s.verify("24AAACB1234C1ZQ") == s.verify("24AAACB1234C1ZQ")
    for c in s.search_by_name("ACME COSMETICS", "24"):
        assert checksum_ok(c.gstin) and c.gstin.startswith("24")
    assert s.provider_class == "stub"


class Flaky:
    name, provider_class, cost_verify_inr, cost_search_inr = "flaky", "live_gstn", 1.0, 1.0

    def __init__(self):
        self.calls = 0

    def verify(self, gstin):
        self.calls += 1
        raise ProviderError("timeout")

    def search_by_name(self, name, state_code):
        raise ProviderError("timeout")


def test_router_falls_back_and_breaker_opens():
    now = [0.0]
    flaky, stub = Flaky(), StubProvider()
    r = Router([flaky, stub], failures=3, cooldown=60, shadow_rate=0, clock=lambda: now[0])
    for _ in range(3):
        assert r.verify("24AAACB1234C1ZQ").provider is stub      # fallback answers
    assert flaky.calls == 3
    r.verify("24AAACB1234C1ZQ")
    assert flaky.calls == 3                                         # breaker open: skipped
    now[0] = 61
    r.verify("24AAACB1234C1ZQ")
    assert flaky.calls == 4                                         # half-open again
    with pytest.raises(ProviderError):
        Router([Flaky()], shadow_rate=0).verify("24AAACB1234C1ZQ")


def test_shadow_sample_reports_disagreement():
    a = Taxpayer("24AAACB1234C1ZQ", lgnm="X", sts="Active", pradr={"pncd": "380001"})
    b = Taxpayer("24AAACB1234C1ZQ", lgnm="X", sts="Cancelled", pradr={"pncd": "380002"})
    assert disagreement(a, b) == ["sts", "pradr.pncd"]
    assert disagreement(a, a) == [] and disagreement(None, None) == []
    r = Router([StubProvider(), StubProvider()], shadow_rate=1.0)
    assert len(r.verify("24AAACB1234C1ZQ").shadow) == 1


def test_freshness_by_trigger():
    now = datetime(2026, 10, 31, tzinfo=timezone.utc)
    rec = {"status": "Active", "verified_at": now - timedelta(days=10)}
    assert cache.fresh(rec, "render", now)          # 30 d for a card
    assert not cache.fresh(rec, "detail", now)      # 7 d on intent
    assert not cache.fresh({**rec, "verified_at": now - timedelta(hours=30)}, "rfq", now)
    assert not cache.fresh({**rec, "verified_at": now}, "claim", now)   # claim always checks
    assert cache.fresh({"status": "not_found", "verified_at": now - timedelta(days=20)}, "rfq", now)


@pytest.mark.parametrize("cand,decision", [
    (Taxpayer("x", lgnm="ACME COSMETICS PVT LTD", pradr={"pncd": "380001"}), "auto_link"),
    (Taxpayer("x", tradeNam="Acme Cosmetics", pradr={"pncd": "380001"}), "auto_link"),
    (Taxpayer("x", lgnm="ACME COSMETICS", pradr={"pncd": "110001"}), "review"),
    (Taxpayer("x", lgnm="ACME COSMETICS ENTERPRISES", pradr={"pncd": "380001"}), "review"),
    (Taxpayer("x", lgnm="OTHER FIRM", pradr={"pncd": "110001"}), "reject"),
])
def test_link_rule(cand, decision):
    g = "24AAACA1234C1Z"
    cand.gstin = g + check_char(g)
    assert link.decide(cand, {norm_name("Acme Cosmetics Private Limited")}, {"380001"},
                       norm_name)[0] == decision


def test_link_rule_rejects_a_bad_checksum():
    assert link.decide(Taxpayer("24AAACA1234C1Z0", lgnm="ACME"), {"ACME"}, set(), norm_name)[0] == "reject"


def _entity(gst: list[dict]) -> dict:
    return {"records": [], "adverse": [], "licences": [], "mca_adverse": None, "gst": gst}


def _gst(cls, status="Active", days=1, name=True):
    return {"id": 1, "provider_class": cls, "provider": "x", "status": status,
            "verified_at": datetime.combine(date(2026, 10, 31) - timedelta(days=days),
                                            datetime.min.time()),
            "name_match": name, "state_match": True}


def test_stub_never_verifies():
    rows = evidence.rows_for("e", _entity([_gst("stub")]), date(2026, 10, 31))
    assert rows == []
    assert grade.grade(rows, date(2026, 10, 31))["code"] == "G0"


def test_live_check_grades_and_decays():
    today = date(2026, 10, 31)
    fresh = evidence.rows_for("e", _entity([_gst("live_gstn")]), today)
    assert grade.grade(fresh, today)["code"] == "G4"
    old = evidence.rows_for("e", _entity([_gst("live_gstn", days=45)]), today)
    assert grade.grade(old, today) == {"code": "G1", "publishers": ["gstn"],
                                       "label": "Registered (GST)"}
    gone = evidence.rows_for("e", _entity([_gst("live_gstn", status="Cancelled")]), today)
    g = grade.grade(gone, today)
    assert g["code"] == "flagged" and "could not be confirmed active" in g["label"]
    differs = evidence.rows_for("e", _entity([_gst("live_gstn", name=False)]), today)
    assert grade.grade(differs, today)["code"] == "G0"     # a mismatch verifies nothing


# ------------------------------------------------------------------ stores

@pytest.fixture(scope="module")
def cur():
    try:
        conn = lib.pg()
    except Exception as e:                                     # pragma: no cover
        pytest.skip(f"Postgres not reachable: {e}")
    with conn, conn.cursor() as c:
        yield c


def test_no_graded_evidence_from_a_non_live_record(cur):
    cur.execute("""SELECT count(*) FROM trust_evidence v JOIN gst_record g ON g.id::text = v.source_key
                   WHERE v.source_id = 's16_gstin' AND g.provider_class <> 'live_gstn'""")
    assert cur.fetchone()[0] == 0


def test_stub_candidates_never_reach_review_or_crosswalk(cur):
    cur.execute("""SELECT count(*) FROM review_queue q JOIN gstin_candidate c ON c.gstin = q.right_key
                   WHERE c.provider_class = 'stub'""")
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT count(*) FROM crosswalk WHERE source_id = 's16_gstin'")
    assert cur.fetchone()[0] == 0
