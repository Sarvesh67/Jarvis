"""The public demo surface — one question box over the cosmetics graph.

Deliberately much smaller than the dashboard. This is read-only, unauthenticated at the
app layer (Cloudflare Access sits in front), and answers ONE question shape: ask, and get
back rows with the evidence tier and the provenance attached.

What it will not do, and why:

  * **No generated Cypher.** `--mode cypher` exists for an agent that has read the schema
    card and can repair a rejected query. A public box cannot, and an LLM writing queries
    against a box anyone can type into is a different product with a different risk
    profile. Templates and graph context only.
  * **No Postgres.** The query path never touches it (people live there, hard rule 4),
    so the demo process has no credentials for it at all.
  * **A budget, not a rate limit.** Routing costs about $0.0018 a question. The cap here
    is a daily spend ceiling and a per-IP burst, so the failure mode when someone hammers
    it is "the demo says come back tomorrow", not a surprise invoice.

    cosmetics/.venv/bin/python -m serve.app          # 127.0.0.1:8099

Port 8099, not 8080: the Jarvis dashboard binds 0.0.0.0:8080, and a second listener on
127.0.0.1:8080 is accepted by the OS while leaving which process answers a loopback
request down to bind specificity. Two services that resolve by accident is not something
to discover during a demo.
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict, deque
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import lib  # noqa: E402

DEMO = yaml.safe_load((ROOT / "validate" / "demo_questions.yaml").read_text())

# Routing is metered in questions, not dollars, because a question is the unit a person
# understands. At ~$0.0018 each, the default ceiling is roughly $3.60 a day.
DAILY_QUESTION_CAP = int(os.environ.get("DEMO_DAILY_CAP", "2000"))
BURST_PER_IP = int(os.environ.get("DEMO_BURST_PER_IP", "20"))
BURST_WINDOW_S = 60
MAX_QUESTION_CHARS = 300

def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


app = FastAPI(title="Lattix — cosmetics supply-chain graph", docs_url=None, redoc_url=None)


@app.middleware("http")
async def no_store(request: Request, call_next):
    """Nothing here is cacheable, and the demo should be able to say so.

    Cloudflare already reports `cf-cache-status: DYNAMIC` for these routes and never
    caches a POST, so this changes no behaviour — it removes the question. Every answer
    is computed against the live graph at the moment it is asked, and a stale row on a
    screen in front of a customer is not a risk worth carrying for a response time that
    is already about two seconds.
    """
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response

_recent: dict[str, deque] = defaultdict(deque)
_day = {"stamp": time.strftime("%Y-%m-%d"), "asked": 0}


def _client_ip(request: Request) -> str:
    # Cloudflare terminates TLS and forwards the real client; behind a tunnel the socket
    # peer is always the tunnel itself, so it identifies nobody.
    return (request.headers.get("cf-connecting-ip")
            or (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
            or (request.client.host if request.client else "?"))


def _meter(ip: str) -> None:
    today = time.strftime("%Y-%m-%d")
    if _day["stamp"] != today:
        _day.update(stamp=today, asked=0)
    if _day["asked"] >= DAILY_QUESTION_CAP:
        raise HTTPException(429, "The demo has answered its budget of questions for "
                                 "today. It resets at midnight.")
    now = time.monotonic()
    q = _recent[ip]
    while q and now - q[0] > BURST_WINDOW_S:
        q.popleft()
    if len(q) >= BURST_PER_IP:
        raise HTTPException(429, f"That is more than {BURST_PER_IP} questions in a "
                                 f"minute. Give it a moment.")
    q.append(now)
    _day["asked"] += 1


@app.get("/healthz")
def healthz():
    try:
        drv = lib.neo4j_driver()
        with drv.session() as s:
            n = s.run("MATCH (c:Company) RETURN count(c) AS n").single()["n"]
        drv.close()
        return {"ok": True, "companies": n, "asked_today": _day["asked"]}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)


@app.get("/api/suggestions")
def suggestions():
    """Opening questions for the empty state.

    Served from `validate/demo_questions.yaml` — the same file `tests/test_demo.py`
    asserts still works — so a question cannot appear here unless it is tested.

    The `caveat` on each entry is deliberately NOT sent. It is a note to whoever is
    presenting, and this page is the product as a customer sees it. That material lives
    in the playbook, which is where the presenter reads it — off screen.
    """
    return [{"id": d["id"], "ask": d["asks"][0]} for d in DEMO]


def _evidence_view(res: dict) -> dict:
    """One tool result, shaped for the page beside the answer."""
    return {
        "template": res.get("template"),
        "intent": " ".join((res.get("intent") or "").split()),
        "tier": res.get("tier_floor"),
        "tier_meaning": res.get("tier_meaning"),
        "note": " ".join((res.get("note") or "").split()) or None,
        "warning": res.get("warning"),
        "empty_reason": res.get("empty_reason"),
        "param_resolution": res.get("param_resolution") or {},
        "rows": res.get("rows") or [],
    }


@app.post("/api/ask")
async def ask(request: Request):
    """One conversational turn, streamed.

    Server-sent events rather than a single response: a turn runs the model, a reviewed
    query, sometimes a second query, then the model again — ten seconds or more. The
    stages are worth showing on their own, because they are the product: a whitelisted
    query is chosen and returns rows BEFORE any prose exists.
    """
    body = await request.json()
    question = (body.get("question") or "").strip()
    history = body.get("history") or []
    if not question:
        raise HTTPException(400, "Ask something.")
    if len(question) > MAX_QUESTION_CHARS:
        raise HTTPException(400, f"Keep it under {MAX_QUESTION_CHARS} characters.")
    _meter(_client_ip(request))

    def events():
        from serve import agent
        try:
            for ev in agent.stream(question, history):
                if ev["type"] == "done":
                    yield _sse({"type": "done",
                                "reply": ev["reply"],
                                "asked_at": time.strftime("%H:%M:%S"),
                                "evidence": [_evidence_view(e) for e in ev["evidence"]]})
                else:
                    yield _sse(ev)
        except Exception as e:                                # pragma: no cover
            yield _sse({"type": "error", "message": f"The graph did not answer: {e}"})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"X-Accel-Buffering": "no",
                                      "Cache-Control": "no-store"})


@app.get("/", response_class=HTMLResponse)
def index():
    return (Path(__file__).resolve().parent / "index.html").read_text()


def main() -> int:
    import uvicorn
    uvicorn.run(app, host=os.environ.get("DEMO_HOST", "127.0.0.1"),
                port=int(os.environ.get("DEMO_PORT", "8099")), log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
