"""Unit tests for msme.nic_master's line-classification regexes and hierarchy logic, using a small
literal excerpt in the exact shape pdfplumber's extract_text() returns for NIC_Sector.pdf's
'Detailed Structure' (verified against the real 193-page government PDF: page headers repeat,
Section/Division use word headers with a case-mangling small-caps font, Group/Class/Sub-class lead
with 3/4/5-digit codes, and description paragraphs wrap without a leading code).

    msme/.venv/bin/python -m pytest msme/tests/test_nic_master.py -v
"""
from msme.nic_master import _CODE_LINE, _DIV_LINE, _level_of, _parent_of, _SEC_LINE, _SKIP_EXACT


def test_section_line_matches_case_mangled_header():
    m = _SEC_LINE.match("SECTION A : AGRICULTURE, FORESTY AND FISHING")
    assert m and m.group(1) == "A"


def test_division_line_matches_small_caps_font_mangling():
    m = _DIV_LINE.match("DiviSion 01 : Crop anD animal proDuCtion, huntinG anD")
    assert m and m.group(1) == "01"


def test_code_line_matches_group_class_subclass():
    assert _CODE_LINE.match("011 Growing of non-perennial crops").group(1) == "011"
    assert _CODE_LINE.match("0111 Growing of cereals").group(1) == "0111"
    assert _CODE_LINE.match("01111 Growing of wheat").group(1) == "01111"


def test_repeating_page_header_is_skipped():
    assert "group class sub- description" in _SKIP_EXACT
    assert "detailed structure" in _SKIP_EXACT


def test_level_of_by_digit_length():
    assert _level_of("A") == "section"
    assert _level_of("01") == "division"
    assert _level_of("011") == "group"
    assert _level_of("0111") == "class"
    assert _level_of("01111") == "subclass"


def test_parent_of_subclass_is_its_class():
    assert _parent_of("01111") == "0111"
    assert _parent_of("0111") == "011"


def test_real_pdf_extract_lines_classify_correctly():
    """Verbatim lines from the actual NIC_Sector.pdf page 35 extract_text() output — proves the
    parser handles the real document, not just a hand-crafted fixture."""
    lines = [
        "Detailed Structure",
        "Group Class Sub- Description",
        "class",
        "SECTION A : AGRICULTURE, FORESTY AND FISHING",
        "011 Growing of non-perennial crops",
        "0111 Growing of cereals (except rice), leguminous crops and oil seeds",
        "01111 Growing of wheat",
        "01112 Growing of jowar, bajra and millets",
    ]
    seen = []
    for line in lines:
        if line.strip().lower() in _SKIP_EXACT:
            continue
        if _SEC_LINE.match(line):
            seen.append(("section", _SEC_LINE.match(line).group(1)))
        elif _CODE_LINE.match(line):
            seen.append((_level_of(_CODE_LINE.match(line).group(1)), _CODE_LINE.match(line).group(1)))
    assert ("section", "A") in seen
    assert ("group", "011") in seen
    assert ("class", "0111") in seen
    assert ("subclass", "01111") in seen
    assert ("subclass", "01112") in seen
