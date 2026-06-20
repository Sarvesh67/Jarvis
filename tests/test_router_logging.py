"""Tests for the request/token logging side effect of /v1/chat/completions."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient


async def _ok_forward(payload):
    return {
        "id": "ok",
        "object": "chat.completion",
        "model": payload["model"],
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": "fine"},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 11, "completion_tokens": 22, "total_tokens": 33},
    }


def _read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_request_writes_both_logs(server_module, monkeypatch):
    monkeypatch.setattr(server_module, "_forward_non_stream", _ok_forward)
    client = TestClient(server_module.app)
    client.post("/v1/chat/completions", json={
        "model": "auto",
        "messages": [{"role": "user", "content": "ok then"}],
    })

    tokens = _read_jsonl(server_module.TOKEN_LOG)
    requests = _read_jsonl(server_module.REQ_LOG)
    assert len(tokens) == 1
    assert len(requests) == 1

    t = tokens[0]
    assert t["input_tokens"] == 11
    assert t["output_tokens"] == 22
    assert t["chosen_model"] in {server_module.MODEL_HAIKU, server_module.MODEL_SONNET, server_module.MODEL_OPUS}
    assert t["tier_reason"]  # non-empty

    r = requests[0]
    assert "first_msg_preview" in r
    assert "ok then" in r["first_msg_preview"]


def test_preview_truncated_to_300(server_module, monkeypatch):
    monkeypatch.setattr(server_module, "_forward_non_stream", _ok_forward)
    client = TestClient(server_module.app)
    long_msg = "x" * 1000
    client.post("/v1/chat/completions", json={
        "model": "auto",
        "messages": [{"role": "user", "content": long_msg}],
    })
    requests = _read_jsonl(server_module.REQ_LOG)
    assert len(requests[-1]["first_msg_preview"]) <= 300
