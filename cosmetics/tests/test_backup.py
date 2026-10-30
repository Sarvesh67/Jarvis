"""The encrypted off-machine copy (pipeline/backup.py). Files and openssl only; no stores.

A synthetic backup root stands in for ~/Backups/lattix-cosmetics, a scratch folder for
iCloud Drive, and a throwaway passphrase file for the keychain item.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib  # noqa: E402
from pipeline import backup as B  # noqa: E402


def tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    base, dest = tmp_path / "base", tmp_path / "icloud"
    for rel, body in {"snapshots/2026-01-01T0000Z/MANIFEST.json": "{}",
                      "raw/s01/a.html": "a", "raw/s01/b.html": "b",
                      "msme/out/firms.parquet": "v1"}.items():
        (base / rel).parent.mkdir(parents=True, exist_ok=True)
        (base / rel).write_text(body)
    pf = tmp_path / "pass"
    pf.write_text("throwaway-test-passphrase\n")
    monkeypatch.setitem(lib.ENV, "COSMETICS_BACKUP_PASSFILE", str(pf))
    return base, dest, tmp_path


def test_offsite_copy_restores_exactly_and_sends_only_what_is_new(setup):
    base, dest, tmp = setup
    assert B.offsite(base, dest) == 0
    assert B.offsite(base, dest) == 0
    assert len(list(dest.glob("files-*.enc"))) == 1          # nothing new, nothing sent

    (base / "raw/s01/c.html").write_text("c")
    (base / "msme/out/firms.parquet").write_text("v2, longer")
    assert B.offsite(base, dest) == 0
    chunks = sorted(dest.glob("files-*.enc"))
    assert len(chunks) == 2
    state = json.loads((base / "offsite.json").read_text())[str(dest)]
    assert state["chunks"][chunks[-1].name]["files"] == 2    # only c.html and the parquet

    chunks[0].unlink()                                       # deleted there: sent again
    assert B.offsite(base, dest) == 0
    assert len(list(dest.glob("files-*.enc"))) == 2

    out = tmp / "restored"
    B.restore_offsite(dest, out, B.passphrase())
    want = {k: v for k, v in tree(base).items() if not k.startswith(("offsite", "snapshots"))}
    got = {k: v for k, v in tree(out).items() if not k.startswith("snapshots")}
    assert got == want                                        # the newest parquet wins
    assert (out / "snapshots/2026-01-01T0000Z/MANIFEST.json").read_text() == "{}"


def test_offsite_copy_refuses_another_passphrase(setup, monkeypatch):
    base, dest, tmp = setup
    assert B.offsite(base, dest) == 0
    other = tmp / "other"
    other.write_text("another-passphrase\n")
    monkeypatch.setitem(lib.ENV, "COSMETICS_BACKUP_PASSFILE", str(other))
    assert B.offsite(base, dest) == 1                         # one copy, one passphrase
    with pytest.raises(RuntimeError, match="passphrase"):
        B.restore_offsite(dest, tmp / "r", B.passphrase())


def test_offsite_copy_refuses_a_tampered_chunk(setup):
    base, dest, tmp = setup
    assert B.offsite(base, dest) == 0
    chunk = next(dest.glob("files-*.enc"))
    data = bytearray(chunk.read_bytes())
    data[100] ^= 1
    chunk.write_bytes(bytes(data))
    with pytest.raises(RuntimeError, match="checksum"):
        B.restore_offsite(dest, tmp / "r", B.passphrase())


def test_no_passphrase_means_no_copy(setup, monkeypatch):
    base, dest, tmp = setup
    empty = tmp / "empty"
    empty.write_text("")
    monkeypatch.setitem(lib.ENV, "COSMETICS_BACKUP_PASSFILE", str(empty))
    assert B.offsite(base, dest) == 1
    assert not dest.exists()
