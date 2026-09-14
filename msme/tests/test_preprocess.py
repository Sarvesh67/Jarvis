"""Unit tests for msme.preprocess: flexible headers (the fix for real vs sample Udyam exports),
Activities JSON parsing, and firm construction with GST correctly cut (no GSTIN/PAN in the data).

    msme/.venv/bin/python -m pytest msme/tests/test_preprocess.py -v
"""
from msme.nic import Vocabulary
from msme.preprocess import _clean_numeric_str, _dedupe_udyam, _get, build_firm, parse_activities


def test_get_prefers_unspaced_real_header():
    row = {"EnterpriseName": "Real Export Co", "Enterprise Name": "Sample Co"}
    assert _get(row, "EnterpriseName", "Enterprise Name") == "Real Export Co"


def test_get_falls_back_to_spaced_sample_header():
    row = {"Enterprise Name": "Sample Co"}
    assert _get(row, "EnterpriseName", "Enterprise Name") == "Sample Co"


def test_get_skips_blank_and_missing_variants():
    row = {"EnterpriseName": "", "Enterprise Name": "Fallback Co"}
    assert _get(row, "EnterpriseName", "Enterprise Name") == "Fallback Co"
    assert _get(row, "Nonexistent") == ""


def test_parse_activities_matches_the_documented_udyam_format():
    raw = '[{"NIC5DigitId":"13991","Description":"Embroidery work and making of laces and fringes"}]'
    acts = parse_activities(raw)
    assert acts == [{"nic5": "13991", "desc": "Embroidery work and making of laces and fringes"}]


def test_parse_activities_multiple_codes_and_garbage_input():
    raw = '[{"NIC5DigitId":"13111","Description":"Spinning"},{"NIC5DigitId":"14101","Description":"Garments"}]'
    acts = parse_activities(raw)
    assert [a["nic5"] for a in acts] == ["13111", "14101"]
    assert parse_activities("not json") == []
    assert parse_activities("") == []


def test_build_firm_reads_real_unspaced_headers():
    row = {
        "State": "GUJARAT", "District": "SURAT", "Pincode": "395010",
        "RegistrationDate": "17/11/2025", "EnterpriseName": "Vaghasiya Embroidery",
        "Activities": '[{"NIC5DigitId":"13991","Description":"Embroidery work"}]',
    }
    nic_map = {"nic": {"13991": {"label": "Embroidery", "outputs": ["lace"], "inputs": ["dyes"]}}}
    f = build_firm(row, nic_map, Vocabulary(), {}, http=None)
    assert f["name"] == "Vaghasiya Embroidery"
    assert f["district"] == "SURAT"
    assert f["pincode"] == "395010"
    assert f["outputs"] == ["lace"]


def test_build_firm_gst_is_cut_stays_unknown():
    """GST verification was cut (no GSTIN/PAN in the real data) — gst_status must default to
    'unknown' and gstin must stay empty, never fabricated."""
    row = {"EnterpriseName": "X", "District": "SURAT", "State": "GUJARAT", "Pincode": "395010",
           "Activities": "[]"}
    f = build_firm(row, {"nic": {}}, Vocabulary(), {}, http=None)
    assert f["gst_status"] == "unknown"
    assert f["gstin"] == ""


def test_build_firm_udyam_id_falls_back_when_no_registration_number():
    row = {"EnterpriseName": "Some Firm", "Pincode": "395010", "Activities": "[]"}
    f = build_firm(row, {"nic": {}}, Vocabulary(), {}, http=None)
    assert f["udyam"].startswith("395010-some_firm")


class TestCleanNumericStr:
    """Regression coverage for a REAL data defect found in production: 345,061 of 539,453 rows in
    the actual Udyam export format Pincode as '395006.0' (an inconsistent Excel-export artifact —
    other rows in the SAME file are clean '395006'). Left unfixed, two firms at the identical
    physical pincode get different string keys, breaking exact pincode-tier search."""

    def test_strips_trailing_dot_zero(self):
        assert _clean_numeric_str("395010.0") == "395010"

    def test_leaves_clean_value_untouched(self):
        assert _clean_numeric_str("395010") == "395010"

    def test_leaves_non_numeric_value_untouched(self):
        assert _clean_numeric_str("N/A") == "N/A"

    def test_empty_stays_empty(self):
        assert _clean_numeric_str("") == ""


def test_build_firm_normalizes_dot_zero_pincode():
    """Same physical pincode, two source-row formats -> must produce the SAME clean pincode and
    therefore the same udyam-fallback key prefix."""
    row_dirty = {"EnterpriseName": "Firm A", "Pincode": "395010.0", "Activities": "[]"}
    row_clean = {"EnterpriseName": "Firm B", "Pincode": "395010", "Activities": "[]"}
    fa = build_firm(row_dirty, {"nic": {}}, Vocabulary(), {}, http=None)
    fb = build_firm(row_clean, {"nic": {}}, Vocabulary(), {}, http=None)
    assert fa["pincode"] == "395010"
    assert fb["pincode"] == "395010"
    assert fa["pincode"] == fb["pincode"]


def test_build_firm_udyam_key_includes_registration_date():
    """REGRESSION: pincode+name alone collided on 62,710/539,453 real rows — genuinely different
    enterprises sharing a common name at the same pincode (e.g. two unrelated 'FARMER' firms) were
    silently merged into one graph node. reg_date must be part of the fallback key."""
    row_a = {"EnterpriseName": "Farmer", "Pincode": "393135", "RegistrationDate": "07/09/2025",
             "Activities": "[]"}
    row_b = {"EnterpriseName": "Farmer", "Pincode": "393135", "RegistrationDate": "04/07/2025",
             "Activities": "[]"}
    fa = build_firm(row_a, {"nic": {}}, Vocabulary(), {}, http=None)
    fb = build_firm(row_b, {"nic": {}}, Vocabulary(), {}, http=None)
    assert fa["udyam"] != fb["udyam"]


class TestDedupeUdyam:
    """_dedupe_udyam is the hard backstop: even with reg_date added, 13,522/539,453 real rows still
    collided (no true unique-id column exists in this export). It must guarantee zero collisions,
    deterministically, and NEVER silently merge two firms even if every visible field is identical."""

    def test_no_collisions_is_a_no_op(self):
        firms = [{"udyam": "a"}, {"udyam": "b"}, {"udyam": "c"}]
        n = _dedupe_udyam(firms)
        assert n == 0
        assert [f["udyam"] for f in firms] == ["a", "b", "c"]

    def test_collision_gets_a_stable_numbered_suffix(self):
        firms = [{"udyam": "x"}, {"udyam": "x"}, {"udyam": "x"}]
        n = _dedupe_udyam(firms)
        assert n == 2
        assert [f["udyam"] for f in firms] == ["x", "x-2", "x-3"]

    def test_result_has_zero_duplicate_keys(self):
        firms = [{"udyam": "a"}, {"udyam": "b"}, {"udyam": "a"}, {"udyam": "a"}, {"udyam": "b"}]
        _dedupe_udyam(firms)
        keys = [f["udyam"] for f in firms]
        assert len(keys) == len(set(keys))

    def test_deterministic_same_input_order_same_output(self):
        firms1 = [{"udyam": "x"}, {"udyam": "x"}]
        firms2 = [{"udyam": "x"}, {"udyam": "x"}]
        _dedupe_udyam(firms1)
        _dedupe_udyam(firms2)
        assert [f["udyam"] for f in firms1] == [f["udyam"] for f in firms2]
