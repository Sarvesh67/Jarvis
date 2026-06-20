"""Shared pytest fixtures.

Wires up sys.path so we can `import server` from brain-stub/ and `import phase0_verify`
from skills/phase0-verify/scripts/. We also redirect HOME to a temp dir for any
tests that touch ~/financial-data/logs/.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BRAIN_STUB = ROOT / "brain-stub"
PHASE0 = ROOT / "skills" / "phase0-verify" / "scripts"
BENCH = ROOT / "bench"

# Make modules importable.
sys.path.insert(0, str(BRAIN_STUB))
sys.path.insert(0, str(PHASE0))
sys.path.insert(0, str(BENCH))


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Redirect $HOME so server.py and phase0_verify.py write under tmp_path.

    Forces re-import so the module-level paths re-resolve against the new HOME.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "financial-data" / "logs").mkdir(parents=True)
    (tmp_path / "financial-data" / "datasets").mkdir(parents=True)

    # Force re-import so paths re-evaluate.
    for modname in ("server", "phase0_verify"):
        if modname in sys.modules:
            del sys.modules[modname]
    return tmp_path


@pytest.fixture
def server_module(fake_home):
    """Import server.py against a redirected HOME. Needed so module-level
    LOG_DIR resolves to the temp path."""
    import server  # noqa: WPS433
    importlib.reload(server)
    return server


@pytest.fixture
def phase0_module(fake_home):
    import phase0_verify  # noqa: WPS433
    importlib.reload(phase0_verify)
    return phase0_verify
