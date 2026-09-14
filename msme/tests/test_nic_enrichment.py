"""Unit tests for msme.nic.enrich_nic: curated-vs-stub precedence, cache re-snap (the mechanism that
lets a vocabulary fix recover materials with ZERO new LLM calls), and unary-not-pairwise scope.

    msme/.venv/bin/python -m pytest msme/tests/test_nic_enrichment.py -v
"""
from msme.nic import Vocabulary, enrich_nic


def _vocab():
    return Vocabulary()


def test_curated_entry_wins_and_never_calls_http():
    """A real curated mapping (has materials) short-circuits before any cache/LLM path — passing
    http=None must not raise, proving no network call is attempted."""
    nic_map = {"nic": {"13111": {"label": "Spinning", "outputs": ["cotton yarn"],
                                  "inputs": ["raw cotton"]}}}
    r = enrich_nic("13111", "irrelevant desc", nic_map, _vocab(), {}, http=None)
    assert r["source"] == "curated"
    assert r["outputs"] == ["cotton_yarn"]
    assert r["inputs"] == ["raw_cotton"]


def test_master_label_stub_falls_through_to_cache_not_treated_as_curated():
    """A nic_map entry merged from the NIC master (label only, no materials) must NOT return an
    empty 'curated' result — it must fall through to cache/LLM so materials still get inferred.
    This was the exact bug: without the fix, ALL master-derived codes would report source=curated
    with zero materials and never enrich."""
    nic_map = {"nic": {"01111": {"label": "Growing of wheat", "outputs": [], "inputs": []}}}
    cache = {"01111": {"label": "Growing of wheat", "outputs": ["x"], "inputs": [],
                        "source": "llm", "outputs_raw": ["some grain"], "inputs_raw": []}}
    r = enrich_nic("01111", "Growing of wheat", nic_map, _vocab(), cache, http=None)
    assert r["source"] == "llm"          # NOT "curated" — fell through as intended
    assert r["label"] == "Growing of wheat"


def test_cache_hit_reraps_raw_against_current_vocabulary_no_http():
    """The core of the free-recovery mechanism: a cached raw material that the OLD vocabulary
    couldn't snap must resolve correctly once the vocabulary is fixed/extended — without any new
    HTTP call (http=None here would raise/hang if the code path tried to reach the network)."""
    cache = {
        "13999": {
            "label": "Other textile finishing n.e.c.",
            "outputs": [],  # what the OLD (buggy) matcher produced
            "inputs": [],
            "source": "llm",
            "outputs_raw": ["embroidered fabrics"],   # plural — old matcher rejected this
            "inputs_raw": ["dyes", "raw silk"],
        }
    }
    r = enrich_nic("13999", "", {"nic": {}}, _vocab(), cache, http=None)
    assert r["outputs"] == ["embroidered_fabric"]
    assert sorted(r["inputs"]) == ["dyes", "raw_silk"]
    assert r["source"] == "llm"


def test_legacy_cache_entry_without_raw_is_returned_as_is():
    """A pre-upgrade cache entry (no outputs_raw/inputs_raw) can't be re-snapped — it must be
    returned unchanged rather than erroring, so old caches don't break the pipeline."""
    cache = {"99999": {"label": "legacy", "outputs": ["saree"], "inputs": [], "source": "llm"}}
    r = enrich_nic("99999", "", {"nic": {}}, _vocab(), cache, http=None)
    assert r == cache["99999"]


def test_unknown_code_no_http_yields_empty_not_an_exception():
    """With no curated entry, no cache entry, and http=None (as enrich_all uses when the gateway
    is down), the function must fail open to an empty record rather than raise."""
    r = enrich_nic("00000", "some activity", {"nic": {}}, _vocab(), {}, http=None)
    assert r["source"] == "empty"
    assert r["outputs"] == []
    assert r["inputs"] == []
