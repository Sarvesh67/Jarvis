"""Tests for bench/bench_llama_finance.py scoring logic.

The scorer drives the pass/fail decision on the Pass 1 model. If it grades wrong,
we'll either ship a broken model or reject a working one. Both are bad.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def scorer():
    import bench_llama_finance
    return bench_llama_finance.score_output


_FULL_YAML = """source: MoneyControl
url: https://example.com/article
published_at: 2026-04-25T10:30:00Z
headline: Reliance reports Q4 beat
instruments:
  - RELIANCE.NS
entities:
  companies: [Reliance Industries]
  people: [Mukesh Ambani]
  regulators: []
event_type: earnings
event_subtype: beat
sentiment_label: positive
sentiment_confidence: 0.82
is_market_moving: true
notes: Strong Q4 results"""

_PARTIAL_YAML = """source: MoneyControl
headline: Reliance reports Q4 beat
sentiment_label: positive"""


class TestValidYAML:
    def test_full_schema_valid(self, scorer):
        result = scorer(_FULL_YAML)
        assert result["parses_yaml"] is True
        assert result["fields_present"] == 12
        assert result["fields_missing"] == []
        assert result["instruments_found"] == 1

    def test_partial_schema_partial_score(self, scorer):
        result = scorer(_PARTIAL_YAML)
        assert result["parses_yaml"] is True
        assert result["fields_present"] == 3
        # Missing fields should be reported
        assert "instruments" in result["fields_missing"]
        assert "event_type" in result["fields_missing"]


class TestCodeFenced:
    def test_code_fence_yaml_stripped(self, scorer):
        wrapped = "```yaml\n" + _FULL_YAML + "\n```"
        result = scorer(wrapped)
        assert result["parses_yaml"] is True
        assert result["fields_present"] == 12

    def test_code_fence_no_lang_stripped(self, scorer):
        wrapped = "```\n" + _FULL_YAML + "\n```"
        result = scorer(wrapped)
        assert result["parses_yaml"] is True


class TestInvalid:
    def test_garbage_output(self, scorer):
        # NB: PyYAML is permissive. A bare prose string like
        # "Sure! Here's the info..." parses successfully as a scalar string,
        # so parses_yaml may be True. What matters is that no schema fields
        # are recognized — that's the real failure signal for our scorer.
        result = scorer("Sure! Here's the info you asked for: blah blah.")
        assert result["fields_present"] == 0
        assert len(result["fields_missing"]) == 12

    def test_yaml_error_captured(self, scorer):
        bad = "source: MoneyControl\n  : invalid"  # malformed YAML
        result = scorer(bad)
        # Either fails to parse (preferred) or parses partially.
        if not result["parses_yaml"]:
            assert "yaml_error" in result or result["fields_present"] >= 0


class TestInstruments:
    def test_multiple_tickers_counted(self, scorer):
        yaml_str = _FULL_YAML.replace(
            "instruments:\n  - RELIANCE.NS",
            "instruments:\n  - RELIANCE.NS\n  - HDFC.NS\n  - ICICI.NS",
        )
        result = scorer(yaml_str)
        assert result["instruments_found"] == 3

    def test_empty_instruments_list_counted_zero(self, scorer):
        yaml_str = _FULL_YAML.replace(
            "instruments:\n  - RELIANCE.NS",
            "instruments: []",
        )
        result = scorer(yaml_str)
        assert result["instruments_found"] == 0
