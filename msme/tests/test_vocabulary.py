"""Unit tests for the material vocabulary matcher (msme.nic.Vocabulary.snap).

    msme/.venv/bin/python -m pytest msme/tests/test_vocabulary.py -v

Covers the two regressions found in production data: (1) a substring matcher producing false
positives ('crude petroleum' -> dyes via 'dye' inside 'petroleum'-adjacent text), fixed by requiring
whole-word overlap; (2) the word-boundary fix then rejecting legitimate plurals ('embroidered
fabrics' vs the singular 'embroidered fabric' in the vocab), fixed by light singularization.
"""
from msme.nic import Vocabulary, _singular


def test_exact_match():
    v = Vocabulary()
    assert v.snap("lace") == "lace"
    assert v.snap("Cotton Yarn") == "cotton_yarn"  # case-insensitive


def test_synonym_match():
    v = Vocabulary()
    assert v.snap("cotton thread") == "cotton_yarn"
    assert v.snap("zari") == "zari_thread"


def test_plural_matches_singular_vocab_entry():
    v = Vocabulary()
    assert v.snap("embroidered fabrics") == "embroidered_fabric"
    assert v.snap("cotton yarns") == "cotton_yarn"
    assert v.snap("dress materials") == "dress_material"


def test_singular_matches_plural_synonym():
    v = Vocabulary()
    assert v.snap("garments") == "readymade_garment"  # synonym list has "readymade garment"


def test_false_positives_stay_rejected():
    """These are the exact production false positives that motivated the word-boundary rewrite —
    a naive substring/fuzzy matcher matched them incorrectly."""
    v = Vocabulary()
    assert v.snap("crude petroleum") is None
    assert v.snap("sand or gravel") is None
    assert v.snap("glass") is None   # double-s guard: must not singularize to "glas"
    assert v.snap("brass") is None


def test_unmapped_term_recorded_as_pending():
    v = Vocabulary()
    assert v.snap("exotic spice blends") is None
    assert "exotic spice blends" in v.pending


def test_empty_term_returns_none_without_pending():
    v = Vocabulary()
    assert v.snap("") is None
    assert v.snap("   ") is None
    assert v.pending == {}


class TestSingular:
    def test_strips_trailing_s(self):
        assert _singular("fabrics") == "fabric"
        assert _singular("garments") == "garment"
        assert _singular("yarns") == "yarn"

    def test_ies_to_y(self):
        assert _singular("categories") == "category"

    def test_protects_double_s(self):
        assert _singular("glass") == "glass"
        assert _singular("brass") == "brass"

    def test_protects_short_words(self):
        assert _singular("gas") == "gas"   # len 3, at the boundary — must not become "ga"
        assert _singular("dye") == "dye"   # no trailing s at all
