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
import threading
import time
import uuid
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
    chosen = sorted((d for d in DEMO if d.get("suggest")), key=lambda d: d["suggest"])
    return [{"id": d["id"], "ask": d["asks"][0]} for d in (chosen or DEMO)]


_STATS: dict = {"at": 0.0, "body": None}


@app.get("/api/stats")
def stats():
    """The four figures under the headline, counted from the live graph.

    They were typed into the page once and went stale: "201 accredited labs" stayed after
    10 of those certificates lapsed. Cached for ten minutes; the graph changes nightly.
    """
    if _STATS["body"] and time.monotonic() - _STATS["at"] < 600:
        return _STATS["body"]
    drv = lib.neo4j_driver()
    with drv.session() as s:
        one = lambda q: s.run(q).single()["n"]  # noqa: E731
        body = {
            "companies": one("MATCH (c:Company) RETURN count(c) AS n"),
            "ingredients": one("MATCH (i:Ingredient) RETURN count(i) AS n"),
            "rules": one("MATCH (r:RegulatoryRule) RETURN count(r) AS n"),
            # in-date cosmetics scope only (TRUST_PLAN §10.1): a lapsed lab is not one
            "labs": one("MATCH (c:Company) WHERE c.trust_licence = 'verified' "
                        "AND 'testing_lab' IN coalesce(c.roles, []) RETURN count(c) AS n"),
        }
    drv.close()
    _STATS.update(at=time.monotonic(), body=body)
    return body


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
    job = _start(question, history)
    return _stream(job, 0)


# An answer takes 10-25 seconds, and the connection carrying it can drop: the tunnel's
# QUIC links to Cloudflare time out and reconnect several times a day, and a phone that
# switches apps mid-answer kills its request. Computed inside the response, the answer
# died with the connection and the page printed the browser's own error ("Load failed").
# So the answer runs in its own thread, its events are kept, numbered, for JOB_TTL
# seconds, and a page that loses the stream resumes it at /api/ask/{job}?after=N.
JOB_TTL = 600
KEEPALIVE_S = 10
_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()


def _start(question: str, history: list) -> dict:
    now = time.monotonic()
    with _JOBS_LOCK:
        for k in [k for k, j in _JOBS.items() if now - j["t"] > JOB_TTL]:
            del _JOBS[k]
        job = {"id": uuid.uuid4().hex, "t": now, "events": [], "done": False,
               "cond": threading.Condition()}
        _JOBS[job["id"]] = job

    def run():
        from serve import agent

        def put(ev):
            with job["cond"]:
                job["events"].append(ev)
                job["cond"].notify_all()
        try:
            for ev in agent.stream(question, history):
                if ev["type"] == "done":
                    put({"type": "done", "reply": ev["reply"],
                         "asked_at": time.strftime("%H:%M:%S"),
                         "evidence": [_evidence_view(e) for e in ev["evidence"]]})
                else:
                    put(ev)
        except Exception as e:                                # pragma: no cover
            put({"type": "error", "message": f"The graph did not answer: {e}"})
        finally:
            with job["cond"]:
                job["done"] = True
                job["cond"].notify_all()

    threading.Thread(target=run, name=f"ask-{job['id'][:8]}", daemon=True).start()
    return job


def _follow(job: dict, after: int):
    """Every event from number `after` on, then the rest as they arrive. A comment line
    every KEEPALIVE_S seconds keeps a quiet connection from being idled out while the
    model thinks."""
    yield _sse({"type": "job", "id": job["id"]})
    i = after
    while True:
        with job["cond"]:
            if i >= len(job["events"]) and not job["done"]:
                job["cond"].wait(timeout=KEEPALIVE_S)
            fresh, finished = job["events"][i:], job["done"]
        for ev in fresh:
            i += 1
            yield _sse({**ev, "seq": i})
        if finished and i >= len(job["events"]):
            return
        if not fresh:
            yield ": keepalive\n\n"


def _stream(job: dict, after: int) -> StreamingResponse:
    return StreamingResponse(_follow(job, after), media_type="text/event-stream",
                             headers={"X-Accel-Buffering": "no",
                                      "Cache-Control": "no-store"})


@app.get("/api/ask/{job_id}")
def resume(job_id: str, after: int = 0):
    """Pick an answer up where a dropped connection left it. Not metered: the question
    was already counted when it was asked."""
    job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "That answer has expired. Ask again.")
    return _stream(job, max(0, after))


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
