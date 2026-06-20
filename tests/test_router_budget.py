"""Tests for the daily token budget gate.

These tests write fake entries into brain-tokens.jsonl and verify
_today_token_spend() reads them correctly, and that the /v1/chat/completions
endpoint returns the canned budget-exhausted response when the cap is hit.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient


def _write_token_log(token_log_path, *, today_tokens: int, days_ago_tokens: int = 0) -> None:
    """Drop synthetic rows into the token log."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yesterday = "2020-01-01"  # any past date will do
    with token_log_path.open("a", encoding="utf-8") as f:
        if today_tokens:
            f.write(json.dumps({
                "date": today,
                "input_tokens": today_tokens // 2,
                "output_tokens": today_tokens - today_tokens // 2,
            }) + "\n")
        if days_ago_tokens:
            f.write(json.dumps({
                "date": yesterday,
                "input_tokens": days_ago_tokens,
                "output_tokens": 0,
            }) + "\n")


class TestSpendCalc:
    def test_no_log_returns_zero(self, server_module):
        assert server_module._today_token_spend() == 0

    def test_today_only_counted(self, server_module):
        _write_token_log(server_module.TOKEN_LOG, today_tokens=1234, days_ago_tokens=99999)
        assert server_module._today_token_spend() == 1234

    def test_multiple_entries_summed(self, server_module):
        _write_token_log(server_module.TOKEN_LOG, today_tokens=100)
        _write_token_log(server_module.TOKEN_LOG, today_tokens=200)
        assert server_module._today_token_spend() == 300

    def test_malformed_lines_ignored(self, server_module):
        with server_module.TOKEN_LOG.open("a") as f:
            f.write("this is not json\n")
            f.write('{"date":"2099-01-01","input_tokens":50}\n')
        _write_token_log(server_module.TOKEN_LOG, today_tokens=42)
        # Malformed lines skipped, future-dated lines skipped (not today)
        assert server_module._today_token_spend() == 42


class TestBudgetGate:
    def test_under_budget_forwards(self, server_module, monkeypatch):
        # Stub the bridge call so we don't actually hit it.
        async def fake_forward(payload):
            return {
                "id": "test-123",
                "object": "chat.completion",
                "model": payload["model"],
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            }

        monkeypatch.setattr(server_module, "_forward_non_stream", fake_forward)

        client = TestClient(server_module.app)
        resp = client.post("/v1/chat/completions", json={
            "model": "auto",
            "messages": [{"role": "user", "content": "ok"}],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["choices"][0]["message"]["content"] == "ok"

    def test_over_hard_cap_returns_canned(self, server_module, monkeypatch):
        # Push spend over the hard cap.
        _write_token_log(
            server_module.TOKEN_LOG,
            today_tokens=server_module.BUDGET_HARD + 1000,
        )

        # If the gate works, the bridge is NEVER called.
        async def fail_forward(payload):
            raise AssertionError("bridge should not be called when budget exhausted")

        monkeypatch.setattr(server_module, "_forward_non_stream", fail_forward)

        client = TestClient(server_module.app)
        resp = client.post("/v1/chat/completions", json={
            "model": "auto",
            "messages": [{"role": "user", "content": "anything"}],
        })
        assert resp.status_code == 200
        content = resp.json()["choices"][0]["message"]["content"].lower()
        assert "budget exhausted" in content


class TestHealthz:
    def test_healthz_reports_ok(self, server_module):
        client = TestClient(server_module.app)
        resp = client.get("/healthz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["budget_state"] == "ok"
        assert body["today_tokens"] == 0

    def test_healthz_reports_warn(self, server_module):
        _write_token_log(
            server_module.TOKEN_LOG,
            today_tokens=server_module.BUDGET_SOFT + 100,
        )
        client = TestClient(server_module.app)
        body = client.get("/healthz").json()
        assert body["budget_state"] == "warn"

    def test_healthz_reports_exhausted(self, server_module):
        _write_token_log(
            server_module.TOKEN_LOG,
            today_tokens=server_module.BUDGET_HARD,
        )
        client = TestClient(server_module.app)
        body = client.get("/healthz").json()
        assert body["budget_state"] == "exhausted"


class TestModelEscapeHatch:
    """Explicit tier names override the heuristic."""

    def test_explicit_opus_honored(self, server_module, monkeypatch):
        captured = {}

        async def capture_forward(payload):
            captured["model"] = payload["model"]
            return {
                "id": "x", "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": ""}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }

        monkeypatch.setattr(server_module, "_forward_non_stream", capture_forward)

        client = TestClient(server_module.app)
        client.post("/v1/chat/completions", json={
            "model": server_module.MODEL_OPUS,
            "messages": [{"role": "user", "content": "ok"}],  # would heuristic to haiku
        })
        assert captured["model"] == server_module.MODEL_OPUS

    def test_unknown_model_falls_to_heuristic(self, server_module, monkeypatch):
        captured = {}

        async def capture_forward(payload):
            captured["model"] = payload["model"]
            return {
                "id": "x", "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": ""}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }

        monkeypatch.setattr(server_module, "_forward_non_stream", capture_forward)

        client = TestClient(server_module.app)
        client.post("/v1/chat/completions", json={
            "model": "auto",  # not a tier name — heuristic fires
            "messages": [{"role": "user", "content": "ok"}],
        })
        # Short request, no tools → haiku
        assert captured["model"] == server_module.MODEL_HAIKU
