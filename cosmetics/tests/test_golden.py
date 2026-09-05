"""pytest suite over the golden queries and the build's hard invariants (spec §15.6).

Runs against the live Neo4j graph. Skips cleanly when the graph is not up, so the suite
can live in CI alongside tests that do not need it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib  # noqa: E402

GOLDEN = Path(__file__).resolve().parents[1] / "validate" / "golden_queries.yaml"
SPECS = yaml.safe_load(GOLDEN.read_text())


@pytest.fixture(scope="session")
def session():
    try:
        drv = lib.neo4j_driver()
        with drv.session() as s:
            s.run("RETURN 1").single()
    except Exception as e:                                    # pragma: no cover
        pytest.skip(f"Neo4j not reachable: {e}")
    with drv.session() as s:
        yield s
    drv.close()


def _scalar(recs, key):
    return recs[0][key] if recs else 0


@pytest.mark.parametrize("spec", SPECS, ids=[s["id"] for s in SPECS])
def test_golden_query(session, spec):
    recs = list(session.run(spec["cypher"]))
    if "expect_zero" in spec:
        n = _scalar(recs, spec["expect_zero"])
        assert n == 0, f"{spec['id']}: {n} violation(s) — {spec['q']}"
        return
    n = len(recs)
    if n == 1 and len(recs[0].keys()) == 1 and isinstance(recs[0][0], int):
        n = recs[0][0]
    assert n >= spec.get("expect_min", 0), (
        f"{spec['id']}: {n} rows, expected >= {spec.get('expect_min', 0)}. "
        f"{spec.get('coverage_gap', '')}")
    for want in spec.get("expect_contains", []):
        flat = " ".join(str(v) for r in recs for v in r.values()).upper()
        assert want.upper() in flat, f"{spec['id']}: missing {want}"


def test_no_entity_carries_two_cins():
    """Spec §15.3 — a false merge is a hard fail, not a warning."""
    with lib.pg() as c, c.cursor() as cur:
        cur.execute("""
            SELECT x.entity_id, array_agg(DISTINCT r.cin)
            FROM crosswalk x JOIN raw_company r
              ON r.source_id = x.source_id AND r.source_key = x.source_key
            WHERE x.entity_type='company' AND x.superseded_by IS NULL AND r.cin IS NOT NULL
            GROUP BY x.entity_id HAVING count(DISTINCT r.cin) > 1
        """)
        bad = cur.fetchall()
    assert not bad, f"false merge: {bad[:5]}"


def test_crosswalk_is_append_only():
    """A superseded row must point at a row that exists and is newer than itself."""
    with lib.pg() as c, c.cursor() as cur:
        cur.execute("""SELECT count(*) FROM crosswalk a
                       LEFT JOIN crosswalk b ON b.id = a.superseded_by
                       WHERE a.superseded_by IS NOT NULL AND (b.id IS NULL OR b.id <= a.id)""")
        assert cur.fetchone()[0] == 0


def test_every_regulatory_rule_has_a_jurisdiction_in_postgres_too():
    """Hard rule 2 is enforced by a CHECK constraint, so this is belt and braces."""
    with lib.pg() as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM raw_rule WHERE jurisdiction NOT IN ('IN','EU')")
        assert cur.fetchone()[0] == 0


def test_no_person_data_leaked_into_company_rows():
    """Hard rule 4 / DPDP: people belong in the `person` table, nowhere else."""
    with lib.pg() as c, c.cursor() as cur:
        cur.execute("""SELECT count(*) FROM information_schema.columns
                       WHERE table_name='raw_company'
                         AND column_name IN ('contact_person','person_name','director')""")
        assert cur.fetchone()[0] == 0


def test_normalization_is_idempotent():
    """norm_name(norm_name(x)) == norm_name(x) — suffix stripping repeats until stable."""
    from pipeline.normalize import norm_name
    for raw in ["AARTI SURFACTANTS PRIVATE LIMITED", "Galaxy Surfactants Ltd.",
                "SPAK ORGOCHEM (INDIA)PVT.LTD.", "A B C Industries & Company Pvt Ltd",
                "Some Firm LLP", "Trial Labs Pvt. Ltd."]:
        once = norm_name(raw)
        assert once == norm_name(once), raw


def test_gstin_checksum_rejects_corruption():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "adapters"))
    from s14_company_sites import gstin_valid
    assert gstin_valid("27AAPFU0939F1ZV")
    assert not gstin_valid("27AAPFU0939F1ZX")
    assert not gstin_valid("27AAPFU0939F1Z")       # too short


def test_pan_is_derived_from_gstin_but_never_from_cin():
    """The v1 spec error, locked down: CIN does not encode PAN."""
    from pipeline.normalize import pan_from_gstin
    assert pan_from_gstin("27AAPFU0939F1ZV") == "AAPFU0939F"
    assert pan_from_gstin(None) is None
    import pipeline.normalize as n
    assert not hasattr(n, "pan_from_cin")


def test_generic_only_names_do_not_auto_merge():
    """"SUDAMA ENTERPRISE" vs "RAMU ENTERPRISE" must never reach the merge threshold."""
    from pipeline.resolve import score_pair, AUTO_MERGE
    l = {"norm_name": "SUDAMA ENT", "address_raw": "PLOT 5 SURAT", "pincode": "394107"}
    r = {"norm_name": "RAMU ENT", "address_raw": "PLOT 9 SURAT", "pincode": "394107"}
    s, feats = score_pair(l, r)
    assert s < AUTO_MERGE, feats


def test_observable_feature_renormalisation():
    """Two rows agreeing on name and address only must still be able to auto-merge."""
    from pipeline.resolve import score_pair, AUTO_MERGE
    l = {"norm_name": "AARTI SURFACTANTS", "address_raw": "MIDC Tarapur Boisar 401506"}
    r = {"norm_name": "AARTI SURFACTANTS", "address_raw": "MIDC Tarapur, Boisar 401506"}
    s, feats = score_pair(l, r)
    assert s >= AUTO_MERGE, feats


def test_directory_website_junk_is_rejected():
    """CHEMEXCIL members type free text into the website field and the site linkifies it."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "adapters"))
    from s09_chemexcil import clean_website
    from s14_company_sites import normalize_site
    for junk in ["-", "----------", "9837453889", "MMMMMMMMMMM", "XXXXXXXXXX", "n/a", ""]:
        assert clean_website(junk) is None, junk
        assert normalize_site(junk) is None, junk
    assert clean_website("mnchem.in") == "https://mnchem.in"
    assert normalize_site("https://WWW.Galaxysurfactants.COM/") == "https://www.galaxysurfactants.com"


def test_ingredient_needles_exclude_ordinary_english():
    """Ambiguous single-token INCI names must never become supply-claim needles."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "adapters"))
    from s14b_supplies import AMBIGUOUS
    for word in ("MILK", "SAND", "HONEY", "WATER", "TALC", "ROSE", "SOAP"):
        assert word in AMBIGUOUS


def test_site_identifiers_need_the_company_name_on_the_page():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "adapters"))
    from s14_company_sites import name_on_site
    assert name_on_site("GALAXY SURFACTANTS LIMITED", "WELCOME TO GALAXY SURFACTANTS, MUMBAI")
    assert not name_on_site("BOMBAY PERFUMERY PRIVATE LIMITED",
                            "AACIPL IS A LEADING CHEMICAL MANUFACTURER")
    assert not name_on_site("PRIVATE LIMITED INDIA CO", "ANYTHING AT ALL")
