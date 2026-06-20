"""Jarvis unified dashboard — one pane over budgets, knowledge graphs, and agents.

Aggregates:
  - LiteLLM gateway  (per-project budget / spend / models)   via admin API
  - FalkorDB         (per-project graph node/edge/label stats + subgraph viz)
  - Hermes           (per-project agent profile + brain model)

Read-only by default; one write action: update a project's monthly budget.
Runs on the host (needs to read ~/.hermes profiles); binds 127.0.0.1 only.
"""
import asyncio
import base64
import json
import os
import pathlib
import re

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from falkordb import FalkorDB

from acp import AcpAgent, _env as _proc_env

BASE = pathlib.Path(__file__).resolve().parent
PLATFORM_ENV = BASE.parent / "platform" / ".env"
HERMES_PROFILES = pathlib.Path.home() / ".hermes" / "profiles"
HERMES_BIN = pathlib.Path.home() / ".local" / "bin" / "hermes"
UPLOADS = pathlib.Path.home() / ".jarvis-dashboard" / "uploads"

# One live ACP agent process per project alias (kept warm across turns for memory).
AGENTS: dict = {}

# Knowledge-engine ingestion runs in Cognee's own venv (heavy, isolated deps).
COGNEE_DIR = BASE.parent / "cognee"
COGNEE_PY = COGNEE_DIR / ".venv" / "bin" / "python"
KNOWLEDGE_ALIASES = {"hedgefund", "msme"}  # the projects that have a Cognee dataset
JOBS: dict = {}  # alias -> [recent ingest jobs]
_JOB_SEQ = 0
GATEWAY = "http://127.0.0.1:4000"
FALKOR_HOST, FALKOR_PORT = "127.0.0.1", 6379


def _load_env(p: pathlib.Path) -> dict:
    d = {}
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                d[k] = v
    return d


ENV = _load_env(PLATFORM_ENV)
MASTER = ENV.get("LITELLM_MASTER_KEY", "")

# Budget lines shown on the dashboard. Projects have a knowledge graph; the generic
# `jarvis` agent has none (graph card shows empty) but its budget is still tracked here.
PROJECTS = [
    {"alias": "hedgefund", "label": "AI Hedge Fund", "graph": "hedgefund_graph", "key_env": "HEDGEFUND_LLM_KEY"},
    {"alias": "msme", "label": "MSME Second Brain", "graph": "msme_graph", "key_env": "MSME_LLM_KEY"},
    {"alias": "jarvis", "label": "Jarvis (general)", "graph": "jarvis_graph", "key_env": "JARVIS_LLM_KEY"},
]

app = FastAPI(title="Jarvis Dashboard")


def _falkor():
    return FalkorDB(host=FALKOR_HOST, port=FALKOR_PORT)


def _graph_stats(graph_name: str) -> dict:
    try:
        db = _falkor()
        existing = [g.name if hasattr(g, "name") else g for g in db.list_graphs()]
        if graph_name not in existing:
            return {"exists": False, "nodes": 0, "edges": 0, "labels": []}
        g = db.select_graph(graph_name)
        nodes = g.query("MATCH (n) RETURN count(n)").result_set[0][0]
        edges = g.query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
        rows = g.query("MATCH (n) RETURN DISTINCT labels(n)").result_set
        labels = sorted({l for r in rows for l in (r[0] if isinstance(r[0], list) else [r[0]]) if l})
        return {"exists": True, "nodes": nodes, "edges": edges, "labels": labels}
    except Exception as e:  # noqa: BLE001
        return {"exists": False, "error": str(e), "nodes": 0, "edges": 0, "labels": []}


def _hermes_profile(alias: str) -> dict:
    pdir = HERMES_PROFILES / alias
    if not pdir.exists():
        return {"exists": False}
    model = None
    cfg = pdir / "config.yaml"
    if cfg.exists():
        for line in cfg.read_text().splitlines():
            s = line.strip()
            if s.startswith("default:"):
                model = s.split(":", 1)[1].split("#")[0].strip().strip('"')
                break
    return {"exists": True, "model": model, "wrapper": f"~/.local/bin/{alias}"}


async def _litellm_keys() -> dict:
    if not MASTER:
        return {}
    async with httpx.AsyncClient() as c:
        r = await c.get(
            f"{GATEWAY}/key/list?return_full_object=true",
            headers={"Authorization": f"Bearer {MASTER}"},
            timeout=10,
        )
        r.raise_for_status()
        out = {}
        for k in r.json().get("keys", []):
            if isinstance(k, dict) and k.get("key_alias"):
                out[k["key_alias"]] = k
        return out


async def _gateway_up() -> bool:
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{GATEWAY}/health/liveliness", timeout=5)
            return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


@app.get("/api/overview")
async def overview():
    keys = await _litellm_keys()
    gw = await _gateway_up()
    projects = []
    for p in PROJECTS:
        k = keys.get(p["alias"], {})
        spend = float(k.get("spend") or 0)
        budget = k.get("max_budget")
        projects.append(
            {
                "alias": p["alias"],
                "label": p["label"],
                "budget": budget,
                "spend": round(spend, 5),
                "remaining": round(budget - spend, 5) if budget is not None else None,
                "budget_duration": k.get("budget_duration"),
                "models": k.get("models") or [],
                "graph": _graph_stats(p["graph"]),
                "agent": _hermes_profile(p["alias"]),
            }
        )
    total_budget = sum((p["budget"] or 0) for p in projects)
    total_spend = sum(p["spend"] for p in projects)
    return {
        "gateway_up": gw,
        "total_budget": round(total_budget, 2),
        "total_spend": round(total_spend, 5),
        "projects": projects,
    }


@app.get("/api/graph/{alias}")
def graph_preview(alias: str, limit: int = 40):
    proj = next((p for p in PROJECTS if p["alias"] == alias), None)
    if not proj:
        raise HTTPException(404, "unknown project")
    try:
        db = _falkor()
        g = db.select_graph(proj["graph"])
        q = (
            "MATCH (a)-[r]->(b) RETURN id(a), labels(a), a.name, id(b), labels(b), b.name, type(r) "
            f"LIMIT {int(limit)}"
        )
        nodes, edges, seen = [], [], set()
        for a_id, a_lab, a_name, b_id, b_lab, b_name, rtype in g.query(q).result_set:
            for nid, lab, name in ((a_id, a_lab, a_name), (b_id, b_lab, b_name)):
                if nid not in seen:
                    seen.add(nid)
                    label = (lab[0] if isinstance(lab, list) and lab else lab) or "Node"
                    nodes.append({"data": {"id": str(nid), "label": (name or label)[:28], "group": label}})
            edges.append({"data": {"source": str(a_id), "target": str(b_id), "label": rtype or ""}})
        return {"nodes": nodes, "edges": edges}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, str(e))


class BudgetUpdate(BaseModel):
    max_budget: float


@app.post("/api/project/{alias}/budget")
async def set_budget(alias: str, body: BudgetUpdate):
    proj = next((p for p in PROJECTS if p["alias"] == alias), None)
    if not proj:
        raise HTTPException(404, "unknown project")
    token = ENV.get(proj["key_env"])
    if not token:
        raise HTTPException(500, f"missing {proj['key_env']} in platform/.env")
    async with httpx.AsyncClient() as c:
        r = await c.post(
            f"{GATEWAY}/key/update",
            headers={"Authorization": f"Bearer {MASTER}"},
            json={"key": token, "max_budget": body.max_budget},
            timeout=10,
        )
        if r.status_code >= 400:
            raise HTTPException(r.status_code, r.text)
    return {"ok": True, "alias": alias, "max_budget": body.max_budget}


def _short_model(m: str) -> str:
    return (m or "").split("/")[-1]


@app.get("/api/transactions/{alias}")
async def transactions(alias: str, limit: int = 200):
    """Per-project transaction ledger, read live from the LiteLLM spend logs.

    Filtered server-side by the project's hashed key (`?api_key=<token>`), which
    returns individual request rows (model, tokens, cost, timing). No new storage —
    the gateway already records every call the agent and Cognee make.
    """
    if not any(p["alias"] == alias for p in PROJECTS):
        raise HTTPException(404, "unknown project")
    empty = {"rows": [], "summary": {"count": 0, "spend": 0, "tokens": 0, "by_model": []}}
    keys = await _litellm_keys()
    token = (keys.get(alias) or {}).get("token")
    if not MASTER or not token:
        return empty
    async with httpx.AsyncClient() as c:
        r = await c.get(
            f"{GATEWAY}/spend/logs",
            params={"api_key": token},
            headers={"Authorization": f"Bearer {MASTER}"},
            timeout=15,
        )
        r.raise_for_status()
        raw = r.json()
    if not isinstance(raw, list):
        return empty
    by_model, tot_spend, tot_tokens, rows = {}, 0.0, 0, []
    for x in raw:
        spend = float(x.get("spend") or 0)
        tt = int(x.get("total_tokens") or 0)
        grp = x.get("model_group") or _short_model(x.get("model")) or "—"
        tot_spend += spend
        tot_tokens += tt
        m = by_model.setdefault(grp, {"model": grp, "count": 0, "spend": 0.0, "tokens": 0})
        m["count"] += 1
        m["spend"] += spend
        m["tokens"] += tt
        rows.append(
            {
                "id": x.get("request_id"),
                "time": x.get("endTime") or x.get("startTime"),
                "model": _short_model(x.get("model")),
                "group": x.get("model_group"),
                "call_type": x.get("call_type"),
                "provider": x.get("custom_llm_provider"),
                "prompt_tokens": x.get("prompt_tokens"),
                "completion_tokens": x.get("completion_tokens"),
                "total_tokens": tt,
                "spend": round(spend, 6),
                "duration_ms": x.get("request_duration_ms"),
            }
        )
    rows.sort(key=lambda r: r["time"] or "", reverse=True)
    for m in by_model.values():
        m["spend"] = round(m["spend"], 6)
    return {
        "rows": rows[: int(limit)],
        "summary": {
            "count": len(rows),
            "spend": round(tot_spend, 6),
            "tokens": int(tot_tokens),
            "by_model": sorted(by_model.values(), key=lambda m: m["spend"], reverse=True),
        },
    }


class ChatMessage(BaseModel):
    message: str


@app.post("/api/chat/{alias}")
async def chat(alias: str, body: ChatMessage):
    """Talk to the project's real Hermes agent (its skills, Cognee graph, own budget).

    Runs `hermes -p <alias> -c ui-<alias> -z <msg>` headless: -z prints only the
    reply, -c keeps a persistent per-project session so the conversation has memory.
    The reply is billed to that project's LiteLLM key like any other agent run.
    """
    if not any(p["alias"] == alias for p in PROJECTS):
        raise HTTPException(404, "unknown project")
    msg = (body.message or "").strip()
    if not msg:
        raise HTTPException(400, "empty message")
    if not HERMES_BIN.exists():
        raise HTTPException(500, f"hermes not found at {HERMES_BIN}")
    home = str(pathlib.Path.home())
    env = {
        **os.environ,
        "HOME": home,
        "PATH": f"{home}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:"
        + os.environ.get("PATH", ""),
    }
    session = f"ui-{alias}"
    try:
        proc = await asyncio.create_subprocess_exec(
            str(HERMES_BIN), "-p", alias, "-c", session, "-z", msg,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=home,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=240)
    except asyncio.TimeoutError:
        return {"ok": False, "error": "agent timed out after 240s"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    reply = (out or b"").decode("utf-8", "replace").strip()
    if not reply:
        errtxt = (err or b"").decode("utf-8", "replace").strip()
        return {"ok": False, "error": errtxt[-1500:] or "no output from agent"}
    return {"ok": True, "reply": reply, "session": session}


def _model_label(group: str, under: str) -> str:
    short = under.split("/")[-1] if under else group
    return f"{group} · {short}" if short and short != group else group


async def _chat_models() -> list:
    """Chat-able models the gateway exposes (excludes the embedding model)."""
    if not MASTER:
        return []
    h = {"Authorization": f"Bearer {MASTER}"}
    try:
        async with httpx.AsyncClient() as c:
            ids = [m["id"] for m in (await c.get(f"{GATEWAY}/v1/models", headers=h, timeout=8)).json().get("data", [])]
            info = {}
            for m in (await c.get(f"{GATEWAY}/model/info", headers=h, timeout=8)).json().get("data", []):
                info[m.get("model_name")] = (m.get("litellm_params") or {}).get("model", "")
    except Exception:  # noqa: BLE001
        return []
    return [
        {"id": mid, "model": info.get(mid, ""), "label": _model_label(mid, info.get(mid, ""))}
        for mid in ids
        if mid != "embed"
    ]


@app.get("/api/models")
async def models():
    return {"models": await _chat_models()}


@app.get("/api/toolsets/{alias}")
async def toolsets(alias: str):
    """Hermes built-in toolsets and their default enabled state, for the chat permissions UI."""
    if not any(p["alias"] == alias for p in PROJECTS):
        raise HTTPException(404, "unknown project")
    try:
        proc = await asyncio.create_subprocess_exec(
            str(HERMES_BIN), "-p", alias, "tools", "list",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=_proc_env(),
        )
        out, _ = await asyncio.wait_for(proc.communicate(), 15)
    except Exception:  # noqa: BLE001
        return {"toolsets": []}
    items = []
    for line in out.decode(errors="replace").splitlines():
        parts = line.strip().split()
        if len(parts) >= 3 and parts[1] in ("enabled", "disabled"):
            name = parts[2]
            desc = re.sub(r"^[^A-Za-z]+", "", " ".join(parts[3:])).strip()
            items.append({"name": name, "enabled": parts[1] == "enabled", "desc": desc})
    return {"toolsets": items}


def _build_blocks(text: str, attachments: list, alias: str) -> list:
    """ACP prompt content blocks: text first, images inline (vision), other files saved + referenced."""
    images, notes = [], []
    for a in attachments or []:
        name = re.sub(r"[^A-Za-z0-9._-]", "_", a.get("name") or "file")
        mime = a.get("mime") or ""
        try:
            raw = base64.b64decode((a.get("data") or "").split(",")[-1])
        except Exception:  # noqa: BLE001
            continue
        if mime.startswith("image/"):
            images.append({"type": "image", "mimeType": mime, "data": base64.b64encode(raw).decode()})
        else:
            d = UPLOADS / alias
            d.mkdir(parents=True, exist_ok=True)
            (d / name).write_bytes(raw)
            notes.append(str(d / name))
    txt = (text or "").strip()
    if notes:
        txt += "\n\nAttached file(s) saved locally — read them with your file tools if relevant:\n" + "\n".join(notes)
    if not txt and images:
        txt = "Please look at the attached image."
    return [{"type": "text", "text": txt}, *images]


@app.websocket("/ws/chat/{alias}")
async def ws_chat(ws: WebSocket, alias: str):
    if not any(p["alias"] == alias for p in PROJECTS):
        await ws.close(code=4004)
        return
    await ws.accept()
    agent = AGENTS.setdefault(alias, AcpAgent(alias))
    run_task = None

    async def perm_cb(req_id, params):
        await ws.send_json({"t": "permission", "reqId": req_id, "params": params})

    try:
        while True:
            data = await ws.receive_json()
            t = data.get("t")
            if t == "prompt":
                if run_task and not run_task.done():
                    continue
                try:
                    await agent.ensure(
                        data.get("model"), data.get("toolsets"), bool(data.get("yolo")), perm_cb
                    )
                except Exception as e:  # noqa: BLE001
                    await ws.send_json({"t": "error", "error": f"agent start failed: {e}"})
                    continue
                blocks = _build_blocks(data.get("text"), data.get("attachments"), alias)

                async def upd(u):
                    try:
                        await ws.send_json({"t": "update", "u": u})
                    except Exception:  # noqa: BLE001
                        pass

                async def run(blocks=blocks):
                    try:
                        res = await agent.prompt(blocks, upd)
                        await ws.send_json({"t": "done", "res": res})
                    except Exception as e:  # noqa: BLE001
                        await ws.send_json({"t": "error", "error": str(e)})

                run_task = asyncio.create_task(run())
            elif t == "permission":
                await agent.resolve_permission(
                    data.get("reqId"), data.get("optionId"), bool(data.get("allow", True))
                )
            elif t == "cancel":
                await agent.cancel()
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        pass


def _rel_types(graph_name: str) -> list:
    try:
        db = _falkor()
        existing = [g.name if hasattr(g, "name") else g for g in db.list_graphs()]
        if graph_name not in existing:
            return []
        g = db.select_graph(graph_name)
        rows = g.query("MATCH ()-[r]->() RETURN DISTINCT type(r)").result_set
        return sorted({r[0] for r in rows if r and r[0]})
    except Exception:  # noqa: BLE001
        return []


def _kproj(alias: str) -> dict:
    p = next((p for p in PROJECTS if p["alias"] == alias), None)
    if not p or alias not in KNOWLEDGE_ALIASES:
        raise HTTPException(404, "no knowledge graph for this project")
    return p


def _onto_dir(alias: str) -> pathlib.Path:
    d = COGNEE_DIR / "data" / alias
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read_onto(alias: str) -> dict:
    p = _onto_dir(alias) / "ontology.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:  # noqa: BLE001
            pass
    return {"entityTypes": [], "relationTypes": [], "tags": [], "owlFile": None}


def _write_onto(alias: str, data: dict):
    (_onto_dir(alias) / "ontology.json").write_text(json.dumps(data, indent=2))


@app.get("/api/knowledge/{alias}/schema")
def knowledge_schema(alias: str):
    p = _kproj(alias)
    st = _graph_stats(p["graph"])
    return {
        "nodes": st.get("nodes", 0),
        "edges": st.get("edges", 0),
        "labels": st.get("labels", []),
        "rel_types": _rel_types(p["graph"]),
        "ontology": _read_onto(alias),
    }


class OntologyBody(BaseModel):
    entityTypes: list = []
    relationTypes: list = []
    tags: list = []


@app.post("/api/knowledge/{alias}/ontology")
def knowledge_set_ontology(alias: str, body: OntologyBody):
    _kproj(alias)
    cur = _read_onto(alias)
    cur.update({"entityTypes": body.entityTypes, "relationTypes": body.relationTypes, "tags": body.tags})
    _write_onto(alias, cur)
    return {"ok": True, "ontology": cur}


class OwlBody(BaseModel):
    name: str = "ontology_upload.owl"
    data: str


@app.post("/api/knowledge/{alias}/owl")
def knowledge_owl(alias: str, body: OwlBody):
    _kproj(alias)
    try:
        raw = base64.b64decode((body.data or "").split(",")[-1])
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "bad file data")
    fn = "ontology_upload.owl"
    (_onto_dir(alias) / fn).write_bytes(raw)
    cur = _read_onto(alias)
    cur["owlFile"] = fn
    _write_onto(alias, cur)
    return {"ok": True, "owlFile": fn, "bytes": len(raw)}


@app.delete("/api/knowledge/{alias}/owl")
def knowledge_owl_delete(alias: str):
    _kproj(alias)
    cur = _read_onto(alias)
    if cur.get("owlFile"):
        try:
            (_onto_dir(alias) / cur["owlFile"]).unlink()
        except Exception:  # noqa: BLE001
            pass
    cur["owlFile"] = None
    _write_onto(alias, cur)
    return {"ok": True}


class IngestBody(BaseModel):
    text: str = ""
    tags: list = []
    files: list = []  # [{name, data(base64)}]
    model: str = ""   # optional stronger extractor for cognify, e.g. "openai/reasoner"


async def _run_ingest(alias: str, jid: int, jobfile: pathlib.Path):
    job = next((j for j in JOBS.get(alias, []) if j["id"] == jid), None)
    if not job:
        return
    ansi = re.compile(r"\x1b\[[0-9;]*m")
    ts = re.compile(r"^\d{4}-\d\d-\d\dT")  # Cognee structlog line prefix — noisy, drop it
    errbuf = []

    async def pump_out(stream):
        while True:
            line = await stream.readline()
            if not line:
                break
            s = ansi.sub("", line.decode(errors="replace"))
            if s.strip() and not ts.match(s.strip()):
                job["log"] = (job["log"] + s)[-6000:]

    async def pump_err(stream):
        while True:
            line = await stream.readline()
            if not line:
                break
            errbuf.append(ansi.sub("", line.decode(errors="replace")))
            del errbuf[:-80]

    try:
        proc = await asyncio.create_subprocess_exec(
            str(COGNEE_PY), "ingest.py", "--project", alias, "--job", str(jobfile),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=str(COGNEE_DIR), env=_proc_env(),
        )
        await asyncio.gather(pump_out(proc.stdout), pump_err(proc.stderr))
        rc = await proc.wait()
        ok = rc == 0 and "INGEST_DONE" in job["log"]
        job["status"] = "done" if ok else "failed"
        if not ok and "".join(errbuf).strip():
            job["log"] += "\n--- errors ---\n" + "".join(errbuf)[-1500:]
    except Exception as e:  # noqa: BLE001
        job["log"] += f"\nerror: {e}"
        job["status"] = "failed"


@app.post("/api/knowledge/{alias}/ingest")
async def knowledge_ingest(alias: str, body: IngestBody):
    _kproj(alias)
    if not COGNEE_PY.exists():
        raise HTTPException(500, f"cognee venv not found at {COGNEE_PY}")
    updir = _onto_dir(alias) / "uploads"
    updir.mkdir(parents=True, exist_ok=True)
    paths = []
    for f in body.files or []:
        nm = re.sub(r"[^A-Za-z0-9._-]", "_", f.get("name") or "file")
        try:
            raw = base64.b64decode((f.get("data") or "").split(",")[-1])
        except Exception:  # noqa: BLE001
            continue
        (updir / nm).write_bytes(raw)
        paths.append(str(updir / nm))
    if not body.text.strip() and not paths:
        raise HTTPException(400, "nothing to ingest")
    global _JOB_SEQ
    _JOB_SEQ += 1
    jid = _JOB_SEQ
    jobfile = updir / f"job_{jid}.json"
    jobfile.write_text(json.dumps({"text": body.text, "files": paths, "tags": body.tags, "model": body.model}))
    summary = (body.text.strip()[:64] + ("…" if len(body.text.strip()) > 64 else "")) or f"{len(paths)} file(s)"
    JOBS.setdefault(alias, []).insert(0, {"id": jid, "status": "running", "log": "", "summary": summary})
    JOBS[alias] = JOBS[alias][:20]
    asyncio.create_task(_run_ingest(alias, jid, jobfile))
    return {"ok": True, "job": jid}


@app.get("/api/knowledge/{alias}/jobs")
def knowledge_jobs(alias: str):
    _kproj(alias)
    return {"jobs": JOBS.get(alias, [])}


@app.get("/", response_class=HTMLResponse)
def index():
    return (BASE / "static" / "index.html").read_text()
