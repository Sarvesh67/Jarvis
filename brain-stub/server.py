"""Hermes brain router — OpenAI-compatible HTTP endpoint.

Architecture:

    Hermes ──HTTP──▶ this router (127.0.0.1:8765) ──HTTP──▶ Claude Code bridge (127.0.0.1:3456) ──▶ Anthropic

The router exists for four reasons:

1. **Tiered routing** — pick `claude-haiku-4` / `claude-sonnet-4` / `claude-opus-4`
   per request based on cheap heuristics. Hermes asks for "claude-sonnet-4" by
   default; we override based on what the request actually contains. Heuristics
   only — NO extra LLM call (a Haiku classifier would itself cost tokens).

2. **Daily budget cap** — soft warn at 50K tok, hard stop at 100K tok per UTC day.
   Hard stop returns a canned "budget exhausted" response so Hermes's cron loop
   queues work for next cycle instead of crashing.

3. **Observability** — every request logged to brain-stub.jsonl with the routing
   decision, latency, and token count. After a week of real traffic, we'll know
   which Hermes contexts are biggest and where to tune.

4. **Bridge isolation** — Hermes never touches the bridge directly. If we swap
   the bridge for raw Anthropic API or a different provider later, only this
   file changes.

Bound to 127.0.0.1 only. No auth (local-only).
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

# ---------------------------------------------------------------- config
HOME = Path(os.path.expanduser("~"))
LOG_DIR = HOME / "financial-data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
REQ_LOG = LOG_DIR / "brain-stub.jsonl"
TOKEN_LOG = LOG_DIR / "brain-tokens.jsonl"

# --- secrets / overrides: load from ~/brain-stub/.env
# Format: simple KEY=VALUE per line, # for comments. Keeps API keys out of the
# plist (world-readable in /Library/LaunchDaemons/) and the git history.
# .env values OVERRIDE the plist defaults — so swapping providers (Meridian
# ↔ OpenRouter ↔ raw Anthropic) is a single-file edit, no plist surgery.
ENV_FILE = Path(__file__).resolve().parent / ".env"
if ENV_FILE.exists():
    for _line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ[_k.strip()] = _v.strip().strip('"').strip("'")

# Upstream LLM endpoint. Default is OpenRouter (OpenAI-compatible API exposing
# Anthropic, OpenAI, Google, etc.). Switching providers = change .env, no code.
BRIDGE_URL = os.environ.get(
    "BRAIN_BRIDGE_URL",
    "https://openrouter.ai/api/v1/chat/completions",
)
BRIDGE_API_KEY = os.environ.get("BRAIN_BRIDGE_API_KEY", "")
BRIDGE_TIMEOUT_S = float(os.environ.get("BRAIN_BRIDGE_TIMEOUT", "120"))

# ── Tier → upstream model mapping ──────────────────────────────────────────
# The router exposes three stable TIER LABELS to Hermes (/v1/models):
#     claude-haiku-4, claude-sonnet-4, claude-opus-4
# Internally, each tier maps to a specific UPSTREAM MODEL ID at the provider.
# Defaults are the latest Claude 4.x family on OpenRouter; override per-tier
# via .env if you want cross-vendor optimization (e.g. cheaper gpt-4o-mini for
# haiku, keep Claude Opus 4.7 for opus). The tier labels stay stable so the
# Hermes wizard config never needs to change.
MODEL_HAIKU = "claude-haiku-4"
MODEL_SONNET = "claude-sonnet-4"
MODEL_OPUS = "claude-opus-4"

UPSTREAM = {
    MODEL_HAIKU: os.environ.get("ROUTER_HAIKU_MODEL",  "anthropic/claude-haiku-4.5"),
    MODEL_SONNET: os.environ.get("ROUTER_SONNET_MODEL", "anthropic/claude-sonnet-4.6"),
    MODEL_OPUS: os.environ.get("ROUTER_OPUS_MODEL",   "anthropic/claude-opus-4.7"),
}
# Reverse map for response normalization (upstream id → tier label)
TIER_FROM_UPSTREAM = {v: k for k, v in UPSTREAM.items()}

BUDGET_SOFT = int(os.environ.get("BRAIN_BUDGET_SOFT", "50000"))
BUDGET_HARD = int(os.environ.get("BRAIN_BUDGET_HARD", "100000"))
MAX_OUTPUT_TOKENS = int(os.environ.get("BRAIN_MAX_OUTPUT", "2000"))

app = FastAPI(title="hermes-brain-router", version="0.2.0")


# ---------------------------------------------------------------- helpers
def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _extract_messages_text(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for m in messages or []:
        content = m.get("content", "")
        if isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and "text" in c:
                    parts.append(c["text"])
        else:
            parts.append(str(content))
    return "\n".join(parts)


def _today_token_spend() -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not TOKEN_LOG.exists():
        return 0
    total = 0
    with TOKEN_LOG.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("date") != today:
                continue
            total += int(row.get("input_tokens", 0)) + int(row.get("output_tokens", 0))
    return total


# ---------------------------------------------------------------- tier router
# Heuristic-only. No extra LLM call. Rules are tuned for Hermes's prompt patterns
# (skill orchestration, tool result acknowledgment, cron decisions).

# Signals that demand reasoning-class thinking → Opus.
# NB: trailing \w* on prefixes lets us match suffixed forms like "anomaly",
# "investigate", "investigation", "correlated" — without listing each variant.
# Test test_router_tier.py locks this contract.
_OPUS_PATTERNS = re.compile(
    r"\b(why|anomal\w*|investigat\w*|explain\s+why|root\s+cause|incident\w*|"
    r"correlat\w*|novelty|pass\s*2|gap\s+analy\w*|debug\s+this|"
    r"interpret\w*|deep\s+dive|reason\s+about)\b",
    re.IGNORECASE,
)

# Signals that the request is trivial/routine → Haiku
_HAIKU_PATTERNS = re.compile(
    r"\b(acknowledge|confirm|yes/no|did this succeed|is this done|"
    r"return the result|extract the field|format as|next step|"
    r"which schedule|when did|is .* present)\b",
    re.IGNORECASE,
)


def _pick_tier(messages: list[dict[str, Any]], tools_present: bool) -> tuple[str, str]:
    """Return (model_id, reason). Heuristic only — no LLM call."""
    text = _extract_messages_text(messages)

    if _OPUS_PATTERNS.search(text):
        return MODEL_OPUS, "opus_pattern_match"

    # Tool-call orchestration is usually Sonnet-tier — needs reasoning but not Opus
    if tools_present and len(text) > 1500:
        return MODEL_SONNET, "tools_with_long_context"

    if _HAIKU_PATTERNS.search(text):
        return MODEL_HAIKU, "haiku_pattern_match"

    # Short, no signals → Haiku (cheapest)
    if len(text) < 800 and not tools_present:
        return MODEL_HAIKU, "short_request"

    # Default: Sonnet (the workhorse)
    return MODEL_SONNET, "default"


# ---------------------------------------------------------------- forwarding
async def _forward_non_stream(payload: dict[str, Any]) -> dict[str, Any]:
    """Forward to upstream with auth + tier→upstream model translation."""
    headers = {"content-type": "application/json"}
    if BRIDGE_API_KEY:
        headers["Authorization"] = f"Bearer {BRIDGE_API_KEY}"
        # OpenRouter ranking/attribution; harmless on other providers.
        headers.setdefault("HTTP-Referer", "http://127.0.0.1:8765")
        headers.setdefault("X-Title", "hermes-brain-router")
    # Translate tier label → upstream model id (e.g. claude-opus-4 → anthropic/claude-opus-4.7)
    requested = payload.get("model", "")
    if requested in UPSTREAM:
        payload = {**payload, "model": UPSTREAM[requested]}
    async with httpx.AsyncClient(timeout=BRIDGE_TIMEOUT_S) as client:
        resp = await client.post(BRIDGE_URL, json=payload, headers=headers)
    resp.raise_for_status()
    body = resp.json()
    # Normalize the upstream model id back to our tier label so callers and
    # token logs see stable IDs regardless of which underlying model served.
    if isinstance(body.get("model"), str) and body["model"] in TIER_FROM_UPSTREAM:
        body["model"] = TIER_FROM_UPSTREAM[body["model"]]
    return body


# ---------------------------------------------------------------- routes
@app.get("/healthz")
def healthz() -> dict[str, Any]:
    spent = _today_token_spend()
    return {
        "ok": True,
        "bridge_url": BRIDGE_URL,
        "today_tokens": spent,
        "budget_soft": BUDGET_SOFT,
        "budget_hard": BUDGET_HARD,
        "budget_state": (
            "exhausted" if spent >= BUDGET_HARD
            else "warn" if spent >= BUDGET_SOFT
            else "ok"
        ),
    }


@app.get("/v1/models")
def list_models() -> dict[str, Any]:
    # Mirror what the bridge exposes so probes don't get confused.
    return {
        "object": "list",
        "data": [
            {"id": MODEL_HAIKU, "object": "model", "owned_by": "anthropic-via-router"},
            {"id": MODEL_SONNET, "object": "model", "owned_by": "anthropic-via-router"},
            {"id": MODEL_OPUS, "object": "model", "owned_by": "anthropic-via-router"},
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(req: Request) -> JSONResponse:
    start = time.monotonic()
    body = await req.json()

    # --- 1. Budget gate (hard stop) ---
    spent = _today_token_spend()
    if spent >= BUDGET_HARD:
        # Return a valid completion that tells Hermes to back off.
        return JSONResponse(
            status_code=200,
            content={
                "id": f"budget-stop-{uuid.uuid4().hex[:8]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": body.get("model", MODEL_SONNET),
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": (
                            "Daily token budget exhausted. Resuming at 00:00 UTC. "
                            "Queuing scheduled work for the next cycle."
                        ),
                    },
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            },
        )

    messages = body.get("messages", [])
    tools = body.get("tools") or []
    requested_model = body.get("model", "")

    # --- 2. Pick tier ---
    chosen_model, reason = _pick_tier(messages, bool(tools))

    # Escape hatch: if Hermes (or a skill) explicitly names a tier, honor it.
    # This lets specific skills force opus on hard reasoning, or haiku on
    # bulk classification. The Hermes default config uses "auto" so the
    # heuristic picks for every untouched call.
    if requested_model in (MODEL_HAIKU, MODEL_SONNET, MODEL_OPUS):
        chosen_model = requested_model
        reason = f"explicit:{requested_model}"

    # --- 3. Build forwarded payload ---
    forwarded = dict(body)
    forwarded["model"] = chosen_model
    # Cap output to prevent runaway responses (orchestration rarely needs > 2K).
    if not forwarded.get("max_tokens") or forwarded["max_tokens"] > MAX_OUTPUT_TOKENS:
        forwarded["max_tokens"] = MAX_OUTPUT_TOKENS
    # Force non-stream (router doesn't proxy SSE in v1; keep it simple).
    forwarded["stream"] = False

    # --- 4. Forward to bridge ---
    try:
        upstream = await _forward_non_stream(forwarded)
    except httpx.HTTPStatusError as e:
        # Bridge said no. Surface as a Hermes-readable error.
        raise HTTPException(
            status_code=502,
            detail=f"bridge rejected request: {e.response.status_code} {e.response.text[:200]}",
        )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail=f"bridge unreachable: {e}")

    # --- 5. Token accounting & log ---
    usage = upstream.get("usage") or {}
    in_tok = int(usage.get("prompt_tokens", 0))
    out_tok = int(usage.get("completion_tokens", 0))
    latency_ms = int((time.monotonic() - start) * 1000)

    now = datetime.now(timezone.utc)
    row = {
        "ts": now.isoformat(),
        "date": now.strftime("%Y-%m-%d"),
        "requested_model": requested_model,
        "chosen_model": chosen_model,
        "tier_reason": reason,
        "tools_present": bool(tools),
        "n_messages": len(messages),
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "latency_ms": latency_ms,
        "soft_warn": (spent + in_tok + out_tok) >= BUDGET_SOFT,
    }
    _append_jsonl(TOKEN_LOG, row)

    # Truncated request preview for the verbose log
    preview = _extract_messages_text(messages)[:300]
    _append_jsonl(REQ_LOG, {**row, "first_msg_preview": preview})

    return JSONResponse(upstream)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")
