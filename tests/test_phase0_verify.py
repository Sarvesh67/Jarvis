"""Tests for skills/phase0-verify/scripts/phase0_verify.py.

Strategy: build a synthetic ~/financial-data/datasets/ with known files,
run main(), assert the report contents.
"""

from __future__ import annotations

import json


def _seed_datasets(home_dir, *, jsonl_rows=10, big_size=2_000_000, zero_byte=False):
    """Create a fake dataset tree under tmp/financial-data/datasets/."""
    ds = home_dir / "financial-data" / "datasets" / "huggingface"
    ds.mkdir(parents=True, exist_ok=True)

    # JSONL with known row count
    jsonl = ds / "data.jsonl"
    jsonl.write_text("\n".join(f'{{"i":{i}}}' for i in range(jsonl_rows)))

    # Binary-ish parquet placeholder
    par = ds / "data.parquet"
    par.write_bytes(b"x" * big_size)

    if zero_byte:
        (ds / "empty.txt").write_bytes(b"")


class TestVerifyClean:
    def test_clean_run_exits_zero(self, phase0_module, fake_home, capsys):
        _seed_datasets(fake_home, jsonl_rows=5)
        rc = phase0_module.main()
        assert rc == 0
        out = capsys.readouterr().out
        assert "Clean" in out

    def test_report_file_created(self, phase0_module, fake_home):
        _seed_datasets(fake_home, jsonl_rows=5)
        phase0_module.main()
        logs = fake_home / "financial-data" / "logs"
        reports = list(logs.glob("phase0-verify-*.md"))
        assert len(reports) == 1, f"expected 1 report, got {reports}"
        body = reports[0].read_text()
        assert "Files scanned" in body
        assert "data.jsonl" in body
        assert "data.parquet" in body

    def test_cache_persists(self, phase0_module, fake_home):
        _seed_datasets(fake_home, jsonl_rows=5)
        phase0_module.main()
        cache = fake_home / "financial-data" / "datasets" / ".hashes.json"
        assert cache.exists()
        cached = json.loads(cache.read_text())
        # 'data.jsonl' is at huggingface/data.jsonl relative to datasets/
        assert any("data.jsonl" in k for k in cached)

    def test_rerun_uses_cache(self, phase0_module, fake_home):
        _seed_datasets(fake_home, jsonl_rows=5)
        phase0_module.main()  # first
        cache = fake_home / "financial-data" / "datasets" / ".hashes.json"
        first_mtime_seen = json.loads(cache.read_text())

        # Re-run; cache should still reflect original content (no rehash needed).
        phase0_module.main()
        second = json.loads(cache.read_text())
        # Same hashes
        for k, v in first_mtime_seen.items():
            assert second[k]["sha256"] == v["sha256"]


class TestAnomalies:
    def test_zero_byte_file_flagged(self, phase0_module, fake_home, capsys):
        _seed_datasets(fake_home, jsonl_rows=3, zero_byte=True)
        rc = phase0_module.main()
        assert rc == 1  # anomaly detected
        err = capsys.readouterr().err
        assert "ZERO-BYTE" in err

    def test_row_drift_flagged(self, phase0_module, fake_home, capsys):
        # First run records 10 rows.
        _seed_datasets(fake_home, jsonl_rows=10)
        phase0_module.main()

        # Modify the JSONL file to have very different row count.
        jsonl = fake_home / "financial-data" / "datasets" / "huggingface" / "data.jsonl"
        jsonl.write_text("\n".join(f'{{"i":{i}}}' for i in range(20)))  # 100% drift

        rc = phase0_module.main()
        assert rc == 1
        err = capsys.readouterr().err
        assert "ROW-DRIFT" in err


class TestEmptyState:
    def test_no_datasets_dir_handled(self, phase0_module, fake_home, capsys):
        # Don't seed anything. main() should create logs dir and run cleanly.
        rc = phase0_module.main()
        # Either clean (0) or error (2) — but should not crash.
        assert rc in (0, 2)
        # Logs dir exists
        assert (fake_home / "financial-data" / "logs").exists()
