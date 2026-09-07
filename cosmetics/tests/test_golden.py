"""pytest suite over the golden queries and the build's hard invariants (spec §15.6).

Runs against the live Neo4j graph. Skips cleanly when the graph is not up, so the suite
can live in CI alongside tests that do not need it.
"""
from __future__ import annotations

import re
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


# --------------------------------------------------------------------------- router
def _templates():
    import yaml as _y
    return _y.safe_load(
        (Path(__file__).resolve().parents[1] / "pipeline" / "query_templates.yaml").read_text())


@pytest.mark.parametrize("t", _templates(), ids=[t["id"] for t in _templates()])
def test_template_declares_its_evidence_tier(t):
    assert t["tier_floor"] in (1, 2, 3, 4), t["id"]


@pytest.mark.parametrize("t", [t for t in _templates() if t.get("flow") == "compliance"],
                         ids=[t["id"] for t in _templates() if t.get("flow") == "compliance"])
def test_compliance_templates_never_rest_on_inference(t):
    """Hard rule 3: tier 4 never answers a licence, compliance or restriction question."""
    assert t["tier_floor"] == 1, f"{t['id']} answers compliance from tier {t['tier_floor']}"


@pytest.mark.parametrize("t", _templates(), ids=[t["id"] for t in _templates()])
def test_template_params_are_all_used(t):
    for name in (t.get("params") or {}):
        assert f"${name}" in t["cypher"], f"{t['id']} declares {name} but never binds it"


@pytest.mark.parametrize("t", _templates(), ids=[t["id"] for t in _templates()])
def test_rule_touching_templates_pin_a_jurisdiction(t):
    """Hard rule 2/4: a template that reaches RegulatoryRule must say which regime."""
    if "RegulatoryRule" in t["cypher"]:
        assert "jurisdiction" in t["cypher"], (
            f"{t['id']} traverses RegulatoryRule without pinning jurisdiction")


@pytest.mark.parametrize("t", _templates(), ids=[t["id"] for t in _templates()])
def test_template_cypher_runs(session, t):
    # $limit is injected by the router for every template, not declared per-template
    params = {name: None for name in (t.get("params") or {})}
    list(session.run(t["cypher"], limit=5, **params))   # raises on syntax/schema error


@pytest.mark.parametrize("t", _templates(), ids=[t["id"] for t in _templates()])
def test_every_template_is_bounded(t):
    """A template must never return an unbounded result set to an agent."""
    assert "LIMIT $limit" in t["cypher"] or "count(" in t["cypher"], t["id"]


def test_router_resolves_vocabulary_deterministically(session):
    """A cluster or category is resolved by graph lookup, never generated by a model."""
    from pipeline.router import _vocab, resolve
    v = _vocab(session)
    assert resolve("cluster_id", "Baddi", v, session)[0] == "HP_BBN"
    assert resolve("cluster_id", "Surat", v, session)[0] == "GJ_AHMEDABAD_VAPI"
    assert resolve("packaging_type", "bottles", v, session)[0] == "plastic_container"
    assert resolve("inci_name", "niacinamide", v, session)[0] == "NIACINAMIDE"
    assert resolve("cluster_id", "Atlantis", v, session)[0] is None


def test_router_keyword_fallback_needs_no_model():
    from pipeline.router import route_by_keyword, TEMPLATES
    d = route_by_keyword("who holds a COS-9 loan licence")
    assert d["template"] in TEMPLATES


def test_every_empty_result_has_a_stated_reason():
    """Thin answers must be explained, never returned as a bare zero."""
    from pipeline.router import coverage_note, TEMPLATES
    for tid in TEMPLATES:
        assert coverage_note(tid).strip()


def test_two_distinct_cins_never_auto_merge_regardless_of_name_similarity():
    """Regression: Nirma's five sister companies (NIRMAN/NAMAN/NAVIN/NIRCHEM/NEO Soaps
    and Detergents Pvt Ltd), one registered address, five distinct real CINs, were
    collapsed into one entity because their names score 93 on token_set_ratio AND
    token_sort_ratio (genuinely close spellings) plus an exact address match. CIN is
    deterministic identity (spec key hierarchy rank 2); no fuzzy score may override it
    when BOTH sides already carry their own CIN.
    """
    from pipeline.resolve import score_pair, AUTO_MERGE
    l = {"norm_name": "NIRMAN SOAPS DETERGENTS", "address_raw": "NIRMA HOUSE ASHRAM ROAD",
         "pincode": "380009", "cin": "U24240GJ1991PTC016591"}
    r = {"norm_name": "NAMAN SOAPS DETERGENTS", "address_raw": "NIRMA HOUSE ASHRAM ROAD",
         "pincode": "380009", "cin": "U24249GJ1991PTC016584"}
    # the score itself may still be high (that's a name-similarity fact) — the veto lives
    # in resolve_companies' candidate loop, not in score_pair, so this documents the
    # score alone is NOT sufficient signal and the caller must check CIN separately
    s, feats = score_pair(l, r)
    assert l["cin"] != r["cin"]
    assert s >= AUTO_MERGE, (
        "if this ever drops below AUTO_MERGE the CIN veto becomes untested dead code; "
        f"got {s}, features {feats}")


def test_token_set_ratio_containment_inflation_is_corrected():
    """"MEGHMANI" vs "MEGHMANI DYES INTERMEDIATES" scores 100 on raw token_set_ratio
    (subset containment) but the two are different companies confirmed by distinct
    CINs. min(token_set_ratio, token_sort_ratio) must catch this."""
    from pipeline.resolve import name_similarity
    sim, generic = name_similarity("MEGHMANI ORGANICS", "MEGHMANI DYES INTERMEDIATES")
    assert sim < 0.70, f"containment inflation not fixed: {sim}"


def test_cluster_assignment_requires_the_state_to_agree():
    """District names are NOT unique across India. Raigarh (Chhattisgarh) is a different
    district from Raigad (Maharashtra), and the cluster map listed the former as a
    spelling variant of the latter — which placed Chhattisgarh firms inside the
    Palghar-Tarapur wedge cluster. Aurangabad (Maharashtra/Bihar), Bilaspur
    (Himachal/Chhattisgarh) and Hamirpur (Himachal/Uttar Pradesh) are the same trap.
    """
    from pipeline.normalize import build_cluster_index, resolve_cluster
    bd, bp, cs = build_cluster_index()
    assert resolve_cluster("Raigarh", "496001", "Chhattisgarh", bd, bp, cs) is None
    assert resolve_cluster("Raigad", "402109", "Maharashtra", bd, bp, cs) == "MH_PALGHAR_TARAPUR"
    assert resolve_cluster("Aurangabad", "824101", "Bihar", bd, bp, cs) is None
    assert resolve_cluster("Aurangabad", "431001", "Maharashtra", bd, bp, cs) == "MH_PUNE"
    # an unknown state is not evidence of a mismatch, so the district match still stands
    assert resolve_cluster("Solan", None, None, bd, bp, cs) == "HP_BBN"


def test_nabl_cosmetics_scope_is_the_register_not_a_keyword_guess():
    """A CENTRAL FORENSIC SCIENCE LABORATORY was flagged as a cosmetics testing lab
    because the keyword list included the bare word CHEMICAL. Scope must come from NABL's
    actual Group/Activity vocabulary."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "adapters"))
    from s11_nabl import COSMETIC_GROUPS, ADJACENT_GROUPS
    assert "CHEMICAL" not in COSMETIC_GROUPS
    assert any("COSMETICS" in g for g in COSMETIC_GROUPS)
    # pharma is adjacent capability, never counted as cosmetics accreditation
    assert all("PHARMACEUTICAL" not in g for g in COSMETIC_GROUPS)
    assert any("PHARMACEUTICAL" in g for g in ADJACENT_GROUPS)


@pytest.mark.parametrize("t", _templates(), ids=[t["id"] for t in _templates()])
def test_no_where_directly_after_optional_match(t):
    """A WHERE placed immediately after OPTIONAL MATCH joins that optional pattern and
    filters nothing. `third_party_manufacturers` silently ignored its cluster constraint
    that way and answered a Baddi question with nationwide rows. Use WITH ... WHERE."""
    assert not re.search(r"OPTIONAL MATCH[^\n]*\n\s*WHERE", t["cypher"]), t["id"]


@pytest.mark.parametrize("t", [t for t in _templates() if "RESTRICTED_BY" in t["cypher"]],
                         ids=[t["id"] for t in _templates() if "RESTRICTED_BY" in t["cypher"]])
def test_restriction_claims_exclude_blanket_group_notes(t):
    """CosIng attaches a handful of blanket notes ("ban substances that deplete the ozone
    layer") to thousands of ingredients. Traversing RESTRICTED_BY without excluding them
    reported AQUA and SODIUM CHLORIDE as EU-restricted. Only substance-specific annex
    entries may support "this ingredient is restricted"."""
    assert "applies_to_group" in t["cypher"], (
        f"{t['id']} asserts a restriction without excluding blanket group notes")


def test_blanket_group_notes_are_few_and_flagged():
    """Guard the assumption the filter rests on: group notes are a tiny, flagged set."""
    try:
        drv = lib.neo4j_driver()
        with drv.session() as s:
            grouped = s.run("MATCH (r:RegulatoryRule) WHERE coalesce(r.applies_to_group,false) "
                            "RETURN count(*) AS n").single()["n"]
            specific = s.run("MATCH (r:RegulatoryRule) WHERE NOT coalesce(r.applies_to_group,false) "
                             "RETURN count(*) AS n").single()["n"]
        drv.close()
    except Exception as e:                                    # pragma: no cover
        pytest.skip(f"Neo4j not reachable: {e}")
    if specific == 0:
        pytest.skip("rules not loaded")
    assert grouped < specific * 0.05, (
        f"{grouped} group notes vs {specific} specific — the blanket-note assumption broke")
