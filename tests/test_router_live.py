"""Live integration test against the actual Claude Code bridge.

Skipped automatically if the bridge isn't reachable. Useful as the final smoke
test before declaring Stage 2 done.

Run:
    pytest tests/test_router_live.py -v
    pytest tests/test_router_live.py -v --runslow   # to actually run them
"""

from __future__ import annotations

import os
import socket
import time

import httpx
import pytest

BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 3456


def _bridge_up() -> bool:
    try:
        with socket.create_connection((BRIDGE_HOST, BRIDGE_PORT), timeout=1):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _bridge_up(),
    reason=f"Bridge not reachable on {BRIDGE_HOST}:{BRIDGE_PORT}",
)


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    """Start the actual server in-process and yield a base URL."""
    import importlib
    import sys

    # Redirect HOME to a temp dir so we don't pollute real logs.
    tmp = tmp_path_factory.mktemp("live")
    os.environ["HOME"] = str(tmp)
    (tmp / "financial-data" / "logs").mkdir(parents=True)

    if "server" in sys.modules:
        del sys.modules["server"]
    import server  # noqa: WPS433
    importlib.reload(server)

    import threading
    import uvicorn

    config = uvicorn.Config(server.app, host="127.0.0.1", port=8766, log_level="error")
    srv = uvicorn.Server(config)

    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()

    # Wait for it to come up.
    for _ in range(20):
        try:
            httpx.get("http://127.0.0.1:8766/healthz", timeout=0.5)
            break
        except httpx.HTTPError:
            time.sleep(0.1)
    else:
        pytest.fail("router didn't start")

    yield "http://127.0.0.1:8766"

    srv.should_exit = True


def test_short_request_routes_haiku_live(live_server):
    resp = httpx.post(
        f"{live_server}/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": "say pong"}]},
        timeout=30,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["model"] == "claude-haiku-4"  # heuristic should pick haiku
    assert body["choices"][0]["message"]["content"]


def test_opus_signal_routes_opus_live(live_server):
    # Opus through the CC bridge is slow — give it 3 minutes.
    resp = httpx.post(
        f"{live_server}/v1/chat/completions",
        json={
            "model": "auto",
            "messages": [{
                "role": "user",
                "content": "explain why anomalies happen in scrapers (one sentence).",
            }],
        },
        timeout=180,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["model"] == "claude-opus-4"


def test_explicit_tier_honored_live(live_server):
    resp = httpx.post(
        f"{live_server}/v1/chat/completions",
        json={
            "model": "claude-haiku-4",
            "messages": [{"role": "user", "content": "explain why this fails"}],  # would heuristic to opus
        },
        timeout=30,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["model"] == "claude-haiku-4"


def test_healthz_live(live_server):
    resp = httpx.get(f"{live_server}/healthz", timeout=5)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["bridge_url"].startswith("http://127.0.0.1:3456")
