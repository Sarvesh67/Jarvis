"""Rung-0 GSTIN checks (pipeline/gstin.py). Pure functions; no stores needed.

Every GSTIN here is synthetic (holder `Z1234C`) except Reliance Industries' published
one, which is a company's and is the independent check that the checksum is right. Never
put a real proprietorship GSTIN in a test: its middle is a person's PAN.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import gstin as G  # noqa: E402

COMPANY = "27AAACR5055K1Z7"        # Reliance Industries, Maharashtra — published
PROPRIETOR = "24AAAPZ1234C1Z6"     # synthetic, PAN type P
HUF_LADAKH = "38AAAHZ1234C1ZE"     # synthetic, PAN type H, state 38
CENTRE = "99AAACZ1234C1ZG"         # synthetic, centre jurisdiction


def test_checksum_accepts_real_and_rejects_one_char_off():
    assert G.checksum_ok(COMPANY)
    assert G.checksum_ok(PROPRIETOR)
    assert not G.checksum_ok(COMPANY[:-1] + "8")
    assert not G.checksum_ok(COMPANY[:-1])
    assert not G.checksum_ok(None)


def test_holder_type_comes_from_the_pan():
    assert G.pan_type(COMPANY) == "C" and not G.is_personal(COMPANY)
    assert G.is_proprietorship(PROPRIETOR) and G.is_personal(PROPRIETOR)
    assert G.is_personal(HUF_LADAKH) and not G.is_proprietorship(HUF_LADAKH)
    assert G.is_personal_pan("AAAPZ1234C") and not G.is_personal_pan("AAACR5055K")


def test_only_personal_gstins_are_masked_and_the_pan_is_gone():
    shown = G.public_gstin(PROPRIETOR)
    assert shown == "24" + "•" * 10 + "1Z6"
    assert "AAAPZ1234C" not in shown
    assert G.public_gstin(COMPANY) == COMPANY
    assert G.public_gstin(None) is None


@pytest.mark.parametrize("a,b", [
    ("Jammu & Kashmir", "Jammu and Kashmir"),
    ("Pondicherry", "Puducherry"),
    ("Daman and Diu", "Dadra and Nagar Haveli and Daman and Diu"),
    ("  west   BENGAL ", "West Bengal"),
])
def test_state_spellings_normalise_together(a, b):
    assert G.norm_state(a) == G.norm_state(b)


def test_state_match_is_true_false_or_unknown():
    assert G.check(COMPANY, {"Maharashtra"})["state_match"] is True
    assert G.check(COMPANY, {"Gujarat"})["state_match"] is False
    # at least one of the record's states is enough — multi-state firms are normal
    assert G.check(COMPANY, {"Gujarat", "Maharashtra"})["state_match"] is True
    # no resolvable state, or a code that is not a place: unknown, never a mismatch
    assert G.check(COMPANY, set())["state_match"] is None
    assert G.check(CENTRE, {"Delhi"})["state_match"] is None
    # ref_pincode files Ladakh under Jammu & Kashmir
    assert G.check(HUF_LADAKH, {"Jammu & Kashmir"})["state_match"] is True


def test_check_normalises_case_and_reports_everything():
    r = G.check(PROPRIETOR.lower(), {"Gujarat"})
    assert r["gstin"] == PROPRIETOR
    assert r["well_formed"] and r["checksum_ok"]
    assert r["gst_state"] == "Gujarat" and r["registration_no"] == "1"
    assert r["is_personal"] and r["is_proprietorship"]
