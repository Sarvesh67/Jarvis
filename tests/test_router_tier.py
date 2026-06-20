"""Unit tests for the heuristic tier router in brain-stub/server.py.

These tests exercise _pick_tier() directly. No HTTP, no bridge, no I/O.
Goal: lock in the routing rules so we don't accidentally regress them
(every regression here = silently spending more on Opus).
"""

from __future__ import annotations

import pytest


def _msg(role: str, content: str) -> dict:
    return {"role": role, "content": content}


# ---------------------------------------------------------------- haiku
class TestHaikuTier:
    def test_short_no_tools_picks_haiku(self, server_module):
        msgs = [_msg("user", "ok")]
        model, reason = server_module._pick_tier(msgs, tools_present=False)
        assert model == server_module.MODEL_HAIKU
        assert "short" in reason or "haiku" in reason

    def test_acknowledge_pattern_picks_haiku(self, server_module):
        msgs = [_msg("user", "Please acknowledge the file was written.")]
        model, reason = server_module._pick_tier(msgs, tools_present=False)
        assert model == server_module.MODEL_HAIKU

    def test_did_this_succeed_picks_haiku(self, server_module):
        msgs = [_msg("user", "did this succeed?")]
        model, reason = server_module._pick_tier(msgs, tools_present=False)
        assert model == server_module.MODEL_HAIKU


# ---------------------------------------------------------------- opus
class TestOpusTier:
    @pytest.mark.parametrize("phrase", [
        "explain why this scrape failed",
        "investigate the anomaly in last night's run",
        "what's the root cause of the 429s",
        "interpret these correlation drifts",
        "deep dive into the gap analysis",
        "this is a pass 2 reasoning task",
    ])
    def test_opus_signals_route_to_opus(self, server_module, phrase):
        msgs = [_msg("user", phrase)]
        model, reason = server_module._pick_tier(msgs, tools_present=False)
        assert model == server_module.MODEL_OPUS, f"phrase {phrase!r} should route to Opus"
        assert "opus" in reason

    def test_opus_signal_in_long_context(self, server_module):
        # Even with tools + long context, opus signals win.
        msgs = [
            _msg("system", "you are an agent"),
            _msg("user", "x" * 3000 + " explain why this happened"),
        ]
        model, _ = server_module._pick_tier(msgs, tools_present=True)
        assert model == server_module.MODEL_OPUS


# ---------------------------------------------------------------- sonnet
class TestSonnetTier:
    def test_long_with_tools_picks_sonnet(self, server_module):
        msgs = [_msg("user", "x" * 2000)]
        model, reason = server_module._pick_tier(msgs, tools_present=True)
        assert model == server_module.MODEL_SONNET
        assert "tools" in reason or "default" in reason

    def test_medium_length_no_signal_default_sonnet(self, server_module):
        # 1500 chars, no tools, no haiku/opus signal → default = sonnet
        msgs = [_msg("user", "Run the data pipeline. " + "data " * 400)]
        model, reason = server_module._pick_tier(msgs, tools_present=False)
        assert model == server_module.MODEL_SONNET


# ---------------------------------------------------------------- multi-message
class TestMultiMessage:
    def test_signal_in_assistant_message_still_counts(self, server_module):
        # A previous turn's assistant message containing "why" should NOT
        # force opus on a new short question.
        msgs = [
            _msg("user", "what's next"),
            _msg("assistant", "I considered why this might have failed"),
            _msg("user", "ok"),
        ]
        # The combined text DOES contain "why" — current heuristic will pick opus.
        # This test documents that behavior; if we change the heuristic to look
        # at ONLY the latest user turn, this test should be updated.
        model, _ = server_module._pick_tier(msgs, tools_present=False)
        # Per current heuristic: combined text has "why" → opus.
        assert model == server_module.MODEL_OPUS

    def test_multipart_content(self, server_module):
        # OpenAI multimodal-style content list
        msgs = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "explain why this fails"},
                {"type": "image_url", "image_url": {"url": "..."}},
            ],
        }]
        model, _ = server_module._pick_tier(msgs, tools_present=False)
        assert model == server_module.MODEL_OPUS


# ---------------------------------------------------------------- empties / edge
class TestEdgeCases:
    def test_empty_messages_falls_through_to_haiku(self, server_module):
        # Length 0 < 800, no tools → short_request branch picks haiku
        model, _ = server_module._pick_tier([], tools_present=False)
        assert model == server_module.MODEL_HAIKU

    def test_messages_without_content_doesnt_crash(self, server_module):
        msgs = [{"role": "user"}]  # missing 'content' key
        model, _ = server_module._pick_tier(msgs, tools_present=False)
        assert model in {
            server_module.MODEL_HAIKU,
            server_module.MODEL_SONNET,
            server_module.MODEL_OPUS,
        }
