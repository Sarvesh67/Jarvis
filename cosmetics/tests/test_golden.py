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


# ------------------------------------------------------- reasoning paths (GraphRAG + Cypher)
def test_vector_index_name_matches_the_one_actually_created():
    """Regression: the router queried 'chunk_embedding' while constraints.cypher created
    'docchunk_embedding', so every no-template question raised ProcedureCallFailed. The
    only escape from template dispatch was dead code and nothing caught it."""
    root = Path(__file__).resolve().parents[1]
    created = re.findall(r"CREATE VECTOR INDEX (\w+)",
                         (root / "ontology" / "constraints.cypher").read_text())
    used = re.findall(r"db\.index\.vector\.queryNodes\('(\w+)'",
                      (root / "pipeline" / "router.py").read_text()
                      + (root / "pipeline" / "context.py").read_text())
    assert used, "no vector index call found"
    assert set(used) <= set(created), f"queries {set(used)} but only {set(created)} exist"


def test_guard_rejects_every_kind_of_write():
    from pipeline.cypher_guard import validate
    for q in ["MATCH (c:Company) DELETE c",
              "MATCH (c:Company) SET c.x = 1 RETURN c",
              "CREATE (:Evil)",
              "MERGE (:Evil)",
              "MATCH (c:Company) REMOVE c.name RETURN c",
              "DROP INDEX foo",
              "LOAD CSV FROM 'file:///x' AS r RETURN r",
              "MATCH (c:Company) DETACH DELETE c"]:
        v = validate(q)
        assert not v.ok, q
        assert v.reason and v.fix, f"rejection must be actionable: {q}"


def test_guard_blocks_write_hidden_in_a_string_literal_is_not_a_false_positive():
    """A company literally named 'CREATE LABS' must not look like a write."""
    from pipeline.cypher_guard import validate
    v = validate("MATCH (c:Company) WHERE c.legal_name = 'CREATE LABS' RETURN c LIMIT 5")
    assert v.ok, v.reason


def test_guard_requires_jurisdiction_on_regulatory_rules():
    """Hard rule 2 — an EU annex must never answer an India question."""
    from pipeline.cypher_guard import validate
    bad = validate("MATCH (i:Ingredient)-[:RESTRICTED_BY]->(r:RegulatoryRule) RETURN r LIMIT 5")
    assert not bad.ok and "jurisdiction" in bad.reason
    ok = validate("MATCH (i:Ingredient)-[:RESTRICTED_BY]->(r:RegulatoryRule) "
                  "WHERE r.jurisdiction = 'IN' RETURN r LIMIT 5")
    assert ok.ok, ok.reason


def test_every_declared_derived_edge_is_registered_with_the_guard():
    """ontology.yaml is the single source of truth for what is derived.

    Parametrising the guard test over the guard's OWN list is circular — forget to
    register an edge and you simply get one fewer test case. This closes the loop: an
    edge declared derived in the ontology but missing from the guard is a tier-4 edge
    that will happily answer a compliance question, which is hard rule 3 broken.
    """
    import lib
    from pipeline.cypher_guard import DERIVED_EDGES as guard_edges
    from pipeline.context import DERIVED_EDGES as context_edges

    declared = {e["type"] for e in lib.load_yaml("ontology.yaml")["derived_edges"]}
    assert not (declared - set(guard_edges)), \
        f"declared derived but not guarded: {sorted(declared - set(guard_edges))}"
    assert not (declared - set(context_edges)), \
        f"declared derived but not held out of context: {sorted(declared - set(context_edges))}"


@pytest.mark.parametrize("rel", sorted(
    __import__("pipeline.cypher_guard", fromlist=["x"]).DERIVED_EDGES))
def test_guard_blocks_tier4_edges_on_compliance_questions(rel):
    """Hard rule 3 — inference ranks and suggests; it never decides permission.

    Parametrised over DERIVED_EDGES rather than naming one edge, so registering a new
    derived edge without the guard covering it fails here instead of shipping.
    """
    from pipeline.cypher_guard import validate
    q = f"MATCH (a)-[:{rel}]->(b) RETURN a, b LIMIT 5"
    assert not validate(q, question="is salicylic acid allowed in India").ok, \
        f"{rel} is in DERIVED_EDGES but answers a compliance question"
    assert validate(q, question="what could I use instead of salicylic acid").ok, \
        f"{rel} is blocked even for an exploratory question"


def test_guard_always_bounds_the_result():
    from pipeline.cypher_guard import validate, DEFAULT_LIMIT, MAX_LIMIT
    assert f"LIMIT {DEFAULT_LIMIT}" in validate("MATCH (c:Company) RETURN c").cypher
    assert f"LIMIT {MAX_LIMIT}" in validate("MATCH (c:Company) RETURN c LIMIT 99999").cypher
    # an aggregate is inherently bounded and must not be padded
    assert "LIMIT" not in validate("MATCH (c:Company) RETURN count(c)").cypher


def test_read_only_session_is_enforced_by_the_server(session):
    """Belt and braces: even if the text guard were bypassed, the server refuses."""
    from neo4j import READ_ACCESS
    drv = lib.neo4j_driver()
    try:
        with drv.session(default_access_mode=READ_ACCESS) as s:
            with pytest.raises(Exception):
                s.run("CREATE (:GuardProbe)").consume()
    finally:
        drv.close()


def test_schema_card_covers_everything_in_the_live_graph(session):
    """A stale schema is worse than none — the agent writes confident queries against
    labels that no longer exist."""
    from pipeline.schema_card import build
    card = build(session)
    live_labels = {r["l"] for r in session.run(
        "MATCH (n) UNWIND labels(n) AS l RETURN DISTINCT l")}
    live_rels = {r["t"] for r in session.run(
        "MATCH ()-[r]->() RETURN DISTINCT type(r) AS t")}
    assert live_labels <= {x["label"] for x in card["labels"]}
    assert live_rels <= {x["type"] for x in card["relationships"]}
    assert card["gotchas"] and card["not_in_graph"]


def test_graph_context_stays_inside_budget_even_from_a_hub(session):
    """Cluster(Ahmedabad-Vapi) has ~17k neighbours; one careless hop returns the graph."""
    from pipeline.context import from_question, MAX_NODES, MAX_EDGES
    r = from_question(session, "packaging converters in Ahmedabad")
    assert r["counts"]["nodes"] <= MAX_NODES
    assert r["counts"]["edges"] <= MAX_EDGES
    assert "SAMPLE" in r["trust"], "context must declare it is not a census"


def test_graph_context_excludes_derived_edges_by_default(session):
    from pipeline.context import from_question, DERIVED_EDGES
    r = from_question(session, "cosmetics manufacturers in Baddi")
    used = {e["type"] for e in r["edges"]}
    assert not (used & set(DERIVED_EDGES) - {"LOCATED_IN"}), used


def test_weak_template_match_falls_through_instead_of_being_forced():
    """The keyword router used to return its least-bad guess for ANY input, so
    "what is the general vibe of the industry" was routed to the EU-restricted-ingredients
    template and answered as though a regulatory question had been asked."""
    from pipeline.router import route_by_keyword, ROUTE_FLOOR
    assert route_by_keyword("what is the general vibe of the industry")["template"] is None
    for q in ["who holds a COS-9 loan licence", "cosmetics manufacturers in Baddi"]:
        d = route_by_keyword(q)
        assert d["template"] is not None and d["confidence"] >= ROUTE_FLOOR, q


def test_every_district_in_the_graph_is_resolvable_by_name(session):
    """Regression: normalize.py clusters a company by PINCODE PREFIX as well as by the
    district list in cluster_map.yaml, so a district can be correctly clustered in the data
    while never appearing in that hand-written list. 22 districts and 3,777 companies were
    in exactly that state — asking about Navsari, Daman or Nainital by name resolved to
    nothing. The vocabulary must therefore come from the data, not the map."""
    from pipeline.router import _vocab, resolve
    v = _vocab(session)
    districts = [r["d"] for r in session.run(
        """MATCH (c:Company) WHERE c.district IS NOT NULL AND c.cluster_id IS NOT NULL
           WITH c.district AS d, count(*) AS n WHERE n >= 10
           RETURN d ORDER BY n DESC LIMIT 40""")]
    unresolvable = [d for d in districts if resolve("district", d, v, session)[0] is None]
    assert not unresolvable, f"clustered but unresolvable by name: {unresolvable}"


def test_a_named_district_narrows_rather_than_returning_its_whole_cluster(session):
    """Answering "Navsari" with the entire Ahmedabad-Vapi belt is a wrong answer dressed as
    a generous one."""
    from pipeline.router import answer
    d = answer("", template="cosmetics_firms_in_cluster",
               params={"district": "Navsari"}, limit=500)
    c = answer("", template="cosmetics_firms_in_cluster",
               params={"cluster": "Ahmedabad"}, limit=500)
    assert d["rows"], "district filter returned nothing"
    assert len(d["rows"]) < len(c["rows"]), "district did not narrow the cluster"
    assert all((r["district"] or "").upper() == "NAVSARI" for r in d["rows"])


# ---------------------------------------------------------------- CDSCO loan licences
#
# The CDSCO register names two firms on every loan-licence row but does NOT say which
# one owns the premises: state FDAs fill the two name columns in opposite conventions
# (Pure&Cure->Dr.Reddy's puts the factory first, GSK->Encube puts it second, and P&G
# Health appears on BOTH sides in different rows). These tests pin the consequences.

def _s01():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "s01", Path(__file__).resolve().parents[1] / "adapters" / "s01_cdsco_sites.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.mark.parametrize("raw,expected", [
    ("-", None),
    ("M/s. Goa Antibiotics & Pharmaceuticals Ltd.", "Goa Antibiotics & Pharmaceuticals Ltd"),
    ("M/s. Dr. Reddy's laboratories limited, C/o M/s. Pure & Cure Healthcare Pvt. Ltd,",
     "Dr. Reddy's laboratories limited"),
    ("Merck Specialities Pvt Ltd C/o ATRA Pharmaceutical Ltd, Plot No H-19, MIDC, Waluj",
     "Merck Specialities Pvt Ltd"),
    ("Cipla Limited, L-139 to L-146, Verna Industrial Estate, Verna, Goa - 403722",
     "Cipla Limited"),
    ("C/o M/s.Pure & Cure Healthcare (P) Ltd.,", "Pure & Cure Healthcare (P) Ltd"),
    ("Testing123", None),
])
def test_loan_counterparty_is_a_firm_name_not_an_address(raw, expected):
    assert _s01()._counterparty(raw) == expected


def test_a_loan_row_never_claims_either_party_is_a_manufacturer():
    """Neither side of a loan licence may claim `manufacturer`: the register does not
    say which one owns the plant, so asserting it would be a coin flip stamped tier 1."""
    m = _s01()
    for regime in ("cosmetics", "drugs"):
        roles = m._roles(regime, True)
        assert "manufacturer" not in roles
        assert f"{regime}_manufacturer" not in roles
        assert "loan_licence_party" in roles
    assert "manufacturer" in m._roles("cosmetics", False)


def test_roles_are_regime_stamped_so_a_drug_licence_cannot_answer_cosmetics():
    m = _s01()
    assert "cosmetics_manufacturer" in m._roles("cosmetics", False)
    assert "cosmetics_manufacturer" not in m._roles("drugs", False)


@pytest.mark.parametrize("name", ["Testing123", "test", "abc", "xyz", "", "  "])
def test_portal_test_rows_are_rejected(name):
    assert _s01().is_junk_name(name)


@pytest.mark.parametrize("name", ["Cipla Limited", "Encube Ethicals Pvt. Ltd.",
                                  "Pure & Cure Healthcare"])
def test_real_firm_names_survive_the_junk_filter(name):
    assert not _s01().is_junk_name(name)


def test_loan_pairs_are_symmetric_and_never_claim_a_direction(session):
    rows = session.run("""
        MATCH (a:Company)-[e:LOAN_LICENCE_WITH]->(b:Company)
        RETURN count(e) AS n,
               count(CASE WHEN e.direction_known = false THEN 1 END) AS undirected
    """).single()
    if rows["n"] == 0:
        pytest.skip("no loan pairs loaded")
    assert rows["undirected"] == rows["n"], "every loan pair must carry direction_known=false"
    missing = session.run("""
        MATCH (a:Company)-[e:LOAN_LICENCE_WITH]->(b:Company)
        WHERE NOT (b)-[:LOAN_LICENCE_WITH {licence_no: e.licence_no}]->(a)
        RETURN count(*) AS n
    """).single()["n"]
    assert missing == 0, "pairing is symmetric: both directions must exist"


def test_no_company_is_its_own_loan_partner(session):
    n = session.run("MATCH (a:Company)-[:LOAN_LICENCE_WITH]->(a) RETURN count(*) AS n"
                    ).single()["n"]
    assert n == 0


def test_cosmetics_licence_template_filters_out_the_drug_register():
    """907 of 911 CDSCO rows are drug licences. A template that says 'licensed to
    manufacture cosmetics' must constrain regime or it answers with a pharma licence."""
    t = [x for x in _templates() if x["id"] == "manufacturers_for_category_in_cluster"][0]
    assert "regime" in t["cypher"], t["cypher"]


def test_normalize_threads_cluster_states_through():
    """resolve_cluster takes cluster_states; normalize_table must actually pass it,
    otherwise every run dies with NameError before a single row is written."""
    import inspect
    from pipeline import normalize
    assert "cluster_states" in inspect.signature(normalize.normalize_table).parameters
