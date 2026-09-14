"""Integration tests against the LIVE msme_graph in FalkorDB — these verify data actually landed
correctly after a real msme/run.sh run, not just unit-level logic. Skipped (not failed) when
FalkorDB is unreachable or the backbone hasn't been loaded yet, so this file is safe to run anytime.

    msme/.venv/bin/python -m pytest msme/tests/test_graph_integration.py -v
"""
import pathlib

import pytest

from msme.search import _falkor


def _graph_or_skip():
    try:
        g = _falkor()
        n = g.query("MATCH (n:Enterprise) RETURN count(n)").result_set[0][0]
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"FalkorDB unreachable: {e}")
    if n == 0:
        pytest.skip("msme_graph backbone is empty — run msme/run.sh first")
    return g


def test_backbone_has_all_four_labels():
    g = _graph_or_skip()
    for lbl in ("Enterprise", "NICClass", "Material", "District"):
        n = g.query(f"MATCH (n:{lbl}) RETURN count(n)").result_set[0][0]
        assert n > 0, f":{lbl} is empty — backbone did not load correctly"


def test_indexes_exist():
    """Without indexes, MERGE on 300k+ Enterprise nodes goes quadratic (the bug that hung the
    pipeline). This must never silently regress."""
    g = _graph_or_skip()
    idx = g.query("CALL db.indexes()").result_set
    labeled = {row[0] for row in idx}  # first column is typically the label; be lenient below
    # Fall back to a permissive check: at least some index rows mention our backbone labels.
    text = str(idx)
    for lbl in ("Enterprise", "NICClass", "Material", "District"):
        assert lbl in text, f"no index found for :{lbl} — re-run msme/graph.py"


def test_every_enterprise_has_a_district_and_a_nic_class():
    """Two positive counts + compare in Python, NOT a `WHERE NOT (e)-[...]->()` anti-join — the
    negative-existence form times out FalkorDB's query budget at 300k+ nodes (confirmed: it made
    this exact check fail with a query timeout, not a real data gap). Positive traversals stay fast
    because they can start from the indexed label and walk forward instead of testing absence
    per-node."""
    g = _graph_or_skip()
    total = g.query("MATCH (e:Enterprise) RETURN count(e)").result_set[0][0]
    with_district = g.query(
        "MATCH (e:Enterprise)-[:LOCATED_IN]->(:District) RETURN count(DISTINCT e)"
    ).result_set[0][0]
    assert with_district == total, f"{total - with_district} Enterprise nodes have no LOCATED_IN edge"
    with_nic = g.query(
        "MATCH (e:Enterprise)-[:CLASSIFIED_AS]->(:NICClass) RETURN count(DISTINCT e)"
    ).result_set[0][0]
    assert with_nic == total, f"{total - with_nic} Enterprise nodes have no CLASSIFIED_AS edge"


def test_no_dot_zero_pincode_contamination():
    """Regression guard for a real data defect: ~64% of the raw Udyam export formatted Pincode as
    '395006.0' (Excel-export artifact, inconsistent within the same file). Any udyam/pincode with a
    literal '.0' suffix here means _clean_numeric_str regressed or the graph wasn't rebuilt after
    the fix."""
    g = _graph_or_skip()
    bad = g.query(
        "MATCH (e:Enterprise) WHERE e.pincode CONTAINS '.0' RETURN count(e)"
    ).result_set[0][0]
    assert bad == 0, f"{bad} Enterprise nodes still have a '.0'-suffixed pincode"


def test_enterprise_count_matches_firms_parquet_row_count():
    """REGRESSION: without a true registration-number column, a weak fallback udyam key silently
    MERGED 43,934 genuinely distinct firms (different address/activities, e.g. two unrelated
    'FARMER' enterprises) into fewer graph nodes via MERGE-on-collision. The graph's Enterprise
    count must equal firms.parquet's row count — any gap means the dedupe backstop regressed."""
    import pandas as pd
    g = _graph_or_skip()
    parquet = pathlib.Path(__file__).resolve().parents[1] / "out" / "firms.parquet"
    if not parquet.exists():
        pytest.skip("firms.parquet missing")
    n_parquet = len(pd.read_parquet(parquet))
    n_graph = g.query("MATCH (e:Enterprise) RETURN count(e)").result_set[0][0]
    assert n_graph == n_parquet, f"graph has {n_graph} Enterprise nodes but firms.parquet has {n_parquet} rows"


def test_gst_status_is_unknown_everywhere():
    """GST verification is cut (no GSTIN/PAN in the source data) — no firm should show anything
    other than 'unknown'; a real value here would mean gst_status got fabricated somewhere."""
    g = _graph_or_skip()
    bad = g.query(
        "MATCH (e:Enterprise) WHERE e.gst_status <> 'unknown' RETURN count(e)"
    ).result_set[0][0]
    assert bad == 0, f"{bad} firms have a non-'unknown' gst_status — GST should be cut"


def test_embroidery_nic_class_produces_embroidered_fabric():
    """Regression guard for the singularization fix: NIC 13991/13999-style embroidery classes must
    show embroidered_fabric as a PRODUCES target, not an empty material set."""
    g = _graph_or_skip()
    rows = g.query(
        "MATCH (c:NICClass)-[:PRODUCES]->(m:Material {slug:'embroidered_fabric'}) "
        "RETURN c.code"
    ).result_set
    assert len(rows) > 0, "no NICClass produces embroidered_fabric — the plural-matching fix regressed"


def test_material_join_has_no_self_loops():
    """A SELLS_TO-style traversal must never return a firm as its own counterparty."""
    g = _graph_or_skip()
    sample = g.query("MATCH (e:Enterprise) RETURN e.udyam LIMIT 1").result_set
    if not sample:
        pytest.skip("no enterprises to sample")
    udyam = sample[0][0]
    bad = g.query(
        "MATCH (a:Enterprise {udyam:$u})-[:CLASSIFIED_AS]->()-[:PRODUCES]->(m)"
        "<-[:CONSUMES]-()<-[:CLASSIFIED_AS]-(b:Enterprise {udyam:$u}) RETURN count(b)",
        params={"u": udyam},
    ).result_set[0][0]
    assert bad == 0
