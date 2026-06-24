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
import yaml
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
MCP_DIR = pathlib.Path.home() / ".jarvis-dashboard" / "mcp"  # canonical per-project MCP store

# Markers delimiting the block the dashboard owns inside each profile's config.yaml.
# Everything outside stays untouched (the profile config is heavily commented).
MCP_BEGIN = "# >>> jarvis-dashboard managed MCP servers (edit via the dashboard) >>>"
MCP_END = "# <<< jarvis-dashboard managed MCP servers <<<"
_MCP_BLOCK_RE = re.compile(re.escape(MCP_BEGIN) + r".*?" + re.escape(MCP_END) + r"\n?", re.DOTALL)

# One live ACP agent process per project alias (kept warm across turns for memory).
AGENTS: dict = {}

# Knowledge-engine ingestion runs in Cognee's own venv (heavy, isolated deps).
COGNEE_DIR = BASE.parent / "cognee"
COGNEE_PY = COGNEE_DIR / ".venv" / "bin" / "python"
SEEDS_DIR = COGNEE_DIR / "seeds"  # committed per-project default configs (materialized locally)
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


# --------------------------------------------------------------------------- #
# MCP servers — per project. The dashboard is the source of truth (JSON store);
# it renders the servers into a delimited block inside the Hermes profile's
# config.yaml so every run of that profile (dashboard chat, ACP, CLI) sees them.
# --------------------------------------------------------------------------- #
def _mcp_store(alias: str) -> pathlib.Path:
    MCP_DIR.mkdir(parents=True, exist_ok=True)
    return MCP_DIR / f"{alias}.json"


def _mcp_load(alias: str) -> list:
    p = _mcp_store(alias)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:  # noqa: BLE001
            return []
    return []


def _mcp_save(alias: str, servers: list):
    _mcp_store(alias).write_text(json.dumps(servers, indent=2))


def _mcp_config_map(servers: list) -> dict:
    """Dashboard records -> Hermes `mcp_servers:` mapping (url/headers or command/args/env)."""
    out = {}
    for s in servers:
        if s.get("url"):
            entry = {"url": s["url"]}
            if s.get("headers"):
                entry["headers"] = s["headers"]
        else:
            entry = {"command": s.get("command", "")}
            if s.get("args"):
                entry["args"] = s["args"]
            if s.get("env"):
                entry["env"] = s["env"]
        if s.get("timeout"):
            entry["timeout"] = s["timeout"]
        out[s["name"]] = entry
    return out


def _mcp_sync_config(alias: str):
    """Rewrite the dashboard-managed mcp_servers block in the profile's config.yaml."""
    cfg = HERMES_PROFILES / alias / "config.yaml"
    if not cfg.exists():
        raise HTTPException(404, f"no hermes profile for {alias}")
    servers = _mcp_load(alias)
    text = _MCP_BLOCK_RE.sub("", cfg.read_text())
    if servers:
        rendered = yaml.safe_dump({"mcp_servers": _mcp_config_map(servers)}, sort_keys=False).rstrip()
        block = f"{MCP_BEGIN}\n{rendered}\n{MCP_END}"
        text = text.rstrip() + "\n\n" + block + "\n"
    cfg.write_text(text)
    os.chmod(cfg, 0o600)


async def _restart_agent(alias: str):
    """Drop the warm ACP process so the next prompt respawns with the new MCP config."""
    ag = AGENTS.pop(alias, None)
    if ag:
        try:
            await ag.stop()
        except Exception:  # noqa: BLE001
            pass


def _mcp_view(s: dict) -> dict:
    """Public shape — never leaks header/env secret values, only which keys are set."""
    return {
        "name": s["name"],
        "transport": "url" if s.get("url") else "stdio",
        "url": s.get("url"),
        "command": s.get("command"),
        "args": s.get("args") or [],
        "header_keys": list((s.get("headers") or {}).keys()),
        "env_keys": list((s.get("env") or {}).keys()),
        "timeout": s.get("timeout"),
    }


@app.get("/api/mcp/{alias}")
def mcp_list(alias: str):
    if not any(p["alias"] == alias for p in PROJECTS):
        raise HTTPException(404, "unknown project")
    return {"servers": [_mcp_view(s) for s in _mcp_load(alias)]}


class McpServer(BaseModel):
    name: str
    transport: str = "url"  # "url" | "stdio"
    url: str = ""
    headers: dict = {}
    command: str = ""
    args: list = []
    env: dict = {}
    timeout: int = 0


@app.post("/api/mcp/{alias}")
async def mcp_add(alias: str, body: McpServer):
    if not any(p["alias"] == alias for p in PROJECTS):
        raise HTTPException(404, "unknown project")
    name = re.sub(r"[^A-Za-z0-9_-]", "", (body.name or "")).strip("-_")
    if not name:
        raise HTTPException(400, "server name required (letters, digits, _ or -)")
    rec = {"name": name}
    if body.transport == "stdio":
        if not body.command.strip():
            raise HTTPException(400, "stdio server needs a command")
        rec["command"] = body.command.strip()
        if body.args:
            rec["args"] = [str(a) for a in body.args if str(a).strip()]
        if body.env:
            rec["env"] = {k: v for k, v in body.env.items() if k}
    else:
        if not body.url.strip():
            raise HTTPException(400, "url server needs a URL")
        rec["url"] = body.url.strip()
        if body.headers:
            rec["headers"] = {k: v for k, v in body.headers.items() if k}
    if body.timeout:
        rec["timeout"] = int(body.timeout)
    servers = [s for s in _mcp_load(alias) if s.get("name") != name]
    servers.append(rec)
    _mcp_save(alias, servers)
    _mcp_sync_config(alias)
    await _restart_agent(alias)
    return {"ok": True, "name": name}


@app.delete("/api/mcp/{alias}/{name}")
async def mcp_remove(alias: str, name: str):
    if not any(p["alias"] == alias for p in PROJECTS):
        raise HTTPException(404, "unknown project")
    servers = [s for s in _mcp_load(alias) if s.get("name") != name]
    _mcp_save(alias, servers)
    _mcp_sync_config(alias)
    await _restart_agent(alias)
    return {"ok": True}


@app.post("/api/mcp/{alias}/{name}/test")
async def mcp_test(alias: str, name: str):
    """Probe a configured server via `hermes -p <alias> mcp test <name>` (non-interactive)."""
    if not any(p["alias"] == alias for p in PROJECTS):
        raise HTTPException(404, "unknown project")
    if not any(s.get("name") == name for s in _mcp_load(alias)):
        raise HTTPException(404, "unknown server")
    try:
        proc = await asyncio.create_subprocess_exec(
            str(HERMES_BIN), "-p", alias, "mcp", "test", name,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env=_proc_env(),
        )
        out, _ = await asyncio.wait_for(proc.communicate(), 45)
        rc = proc.returncode
    except asyncio.TimeoutError:
        return {"ok": False, "output": "connection test timed out after 45s"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "output": str(e)}
    text = re.sub(r"\x1b\[[0-9;]*m", "", (out or b"").decode(errors="replace")).strip()
    ok = rc == 0 and ("✓" in text or "Connected" in text or "tool" in text.lower())
    return {"ok": ok, "output": text[-4000:] or "(no output)"}


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


# routing.py (in the Cognee dir) is pure-python, so it imports fine in the dashboard venv.
# Loaded by path (like preprocess.py) for the role/model-pool resolution helpers, so the
# default model map + pool-naming stay a single source of truth shared with ingest.py.
_ROUTING = None


def _routing_mod():
    global _ROUTING
    if _ROUTING is None:
        import importlib.util as ilu
        spec = ilu.spec_from_file_location("jarvis_routing", COGNEE_DIR / "routing.py")
        mod = ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _ROUTING = mod
    return _ROUTING


def _materialize(alias: str, filename: str, default) -> dict:
    """Return data/<alias>/<filename> as a dict.

    On first access (no local file) the config is **materialized** into the gitignored local
    dir from the committed seed (seeds/<alias>/<filename>) if one exists, else from `default`.
    This is how code-shipped defaults reach a fresh device without committing instance config.
    """
    local = _onto_dir(alias) / filename
    if local.exists():
        try:
            return json.loads(local.read_text())
        except Exception:  # noqa: BLE001
            pass
    data = None
    seed = SEEDS_DIR / alias / filename
    if seed.exists():
        try:
            data = json.loads(seed.read_text())
        except Exception:  # noqa: BLE001
            data = None
    if data is None:
        data = default() if callable(default) else json.loads(json.dumps(default))
    try:
        local.write_text(json.dumps(data, indent=2))
    except Exception:  # noqa: BLE001
        pass
    return data


def _read_onto(alias: str) -> dict:
    base = {"entityTypes": [], "relationTypes": [], "tags": [], "owlFile": None}
    return {**base, **(_materialize(alias, "ontology.json", lambda: dict(base)) or {})}


def _write_onto(alias: str, data: dict):
    (_onto_dir(alias) / "ontology.json").write_text(json.dumps(data, indent=2))


# --- pipeline.json: per-project pre-processing steps + cognify model-routing strategy ---
def _default_pipeline() -> dict:
    return {
        "preprocessing": {"enabled": False, "steps": []},
        "cognify": {"defaultModel": "extractor", "routing": {"enabled": False, "rules": []}},
    }


def _read_pipeline(alias: str) -> dict:
    cfg = _materialize(alias, "pipeline.json", _default_pipeline) or {}
    d = _default_pipeline()
    d.update({k: v for k, v in cfg.items() if v is not None})
    return d


def _write_pipeline(alias: str, data: dict):
    (_onto_dir(alias) / "pipeline.json").write_text(json.dumps(data, indent=2))


# --- models.json: per-project role -> OpenRouter model id (the editable mapping) ---
def _default_models() -> dict:
    return {"roles": dict(_routing_mod().DEFAULT_ROLE_MODELS)}


def _read_models(alias: str) -> dict:
    cfg = _materialize(alias, "models.json", _default_models) or {}
    roles = {**_routing_mod().DEFAULT_ROLE_MODELS, **(cfg.get("roles") or {})}
    return {"roles": roles}


def _write_models(alias: str, data: dict):
    (_onto_dir(alias) / "models.json").write_text(json.dumps(data, indent=2))


# --- gateway pool sync: back each distinct OpenRouter id with a DB model, fix the allowlist ---
# Shared config.yaml models every project key keeps: the agent brain (reasoner), the cheap chat
# model (fast), and embeddings (embed, global — never per-project). Per-project pipeline models
# are the deduped `or--<slug>` DB pool created on demand below.
SHARED_MODELS = ["reasoner", "fast", "embed"]


def _gateway_model_names_sync() -> set:
    try:
        r = httpx.get(f"{GATEWAY}/model/info",
                      headers={"Authorization": f"Bearer {MASTER}"}, timeout=10)
        r.raise_for_status()
        return {m.get("model_name") for m in r.json().get("data", []) if m.get("model_name")}
    except Exception:  # noqa: BLE001
        return set()


def _create_pool_model_sync(name: str, openrouter_id: str):
    # The DB model needs the resolved key value — `os.environ/...` is stored verbatim (not
    # resolved) for API-created models, so pass the real OpenRouter key from platform/.env.
    or_key = ENV.get("OPENROUTER_API_KEY", "")
    if not or_key:
        raise RuntimeError("OPENROUTER_API_KEY missing from platform/.env")
    body = {
        "model_name": name,
        "litellm_params": {
            "model": f"openrouter/{openrouter_id}",
            "api_key": or_key,
            "extra_body": {"usage": {"include": True}},
        },
        "model_info": {"jarvis_pool": True},
    }
    httpx.post(f"{GATEWAY}/model/new",
               headers={"Authorization": f"Bearer {MASTER}", "Content-Type": "application/json"},
               json=body, timeout=15).raise_for_status()


def _set_key_models_sync(alias: str, models: list):
    proj = next((p for p in PROJECTS if p["alias"] == alias), None)
    token = ENV.get(proj["key_env"]) if proj else None
    if not token:
        return
    httpx.post(f"{GATEWAY}/key/update",
               headers={"Authorization": f"Bearer {MASTER}", "Content-Type": "application/json"},
               json={"key": token, "models": models}, timeout=10)


def _sync_models_sync(alias: str) -> dict:
    """Make the project's model map real at the gateway. Idempotent + best-effort.

    For each distinct OpenRouter id in the project's map, ensure a deduped DB-pool model
    `or--<slug>` exists; then set the project key's allowlist to the shared globals + the
    project's pool models. Called before any pipeline gateway call (ingest / test / compile)
    and on a Models save, so a per-project remap takes effect without a restart or a git edit.
    """
    if not MASTER:
        return {"ok": False, "error": "no master key"}
    rt = _routing_mod()
    roles = _read_models(alias).get("roles") or {}
    pool = {rt.pool_model_name(mid): mid for mid in roles.values() if mid}
    try:
        existing = _gateway_model_names_sync()
        for name, mid in pool.items():
            if name not in existing:
                _create_pool_model_sync(name, mid)
        allow = sorted(set(SHARED_MODELS) | set(pool.keys()))
        _set_key_models_sync(alias, allow)
        return {"ok": True, "pool": pool, "allow": allow}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:300]}


# preprocess.py (in the Cognee dir) is httpx-only, so it imports fine in the dashboard venv.
# Loaded by path to avoid putting the whole cognee/ dir on sys.path. Used for the live
# "Test on sample" and "Compile from intent" affordances (both hit the gateway directly).
_PREPROCESS = None


def _preprocess_mod():
    global _PREPROCESS
    if _PREPROCESS is None:
        import importlib.util as ilu
        spec = ilu.spec_from_file_location("jarvis_preprocess", COGNEE_DIR / "preprocess.py")
        mod = ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _PREPROCESS = mod
    return _PREPROCESS


def _kkey(alias: str) -> str:
    """The project's LiteLLM virtual key, for direct gateway calls (test-step / compile)."""
    p = _kproj(alias)
    key = ENV.get(p["key_env"], "")
    if not key:
        raise HTTPException(500, f"missing {p['key_env']} in platform/.env")
    return key


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
        "pipeline": _read_pipeline(alias),
        "models": _read_models(alias),
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


class PipelineBody(BaseModel):
    preprocessing: dict = {}
    cognify: dict = {}


@app.post("/api/knowledge/{alias}/pipeline")
def knowledge_set_pipeline(alias: str, body: PipelineBody):
    _kproj(alias)
    cur = _read_pipeline(alias)
    cur["preprocessing"] = body.preprocessing or cur.get("preprocessing", {})
    cur["cognify"] = body.cognify or cur.get("cognify", {})
    _write_pipeline(alias, cur)
    return {"ok": True, "pipeline": cur}


class ModelsBody(BaseModel):
    roles: dict = {}  # {role: openrouter_model_id}


@app.post("/api/knowledge/{alias}/models")
def knowledge_set_models(alias: str, body: ModelsBody):
    """Save the per-project role->OpenRouter-model map, then make it real at the gateway:
    upsert a deduped DB-pool model per distinct id and refresh the project key allowlist."""
    _kproj(alias)
    roles = {k: (v or "").strip() for k, v in (body.roles or {}).items() if k and (v or "").strip()}
    if not roles:
        raise HTTPException(400, "no model mappings provided")
    _write_models(alias, {"roles": roles})
    sync = _sync_models_sync(alias)
    return {"ok": True, "models": _read_models(alias), "sync": sync}


class TestStepBody(BaseModel):
    step: dict
    text: str
    doc_type: str = ""


@app.post("/api/knowledge/{alias}/pipeline/test-step")
def knowledge_test_step(alias: str, body: TestStepBody):
    """Run ONE preprocessing step against a pasted sample — no graph writes. The guardrail
    that makes free-text prompts safe: see the output (cleaned text / DROP / value) live."""
    key = _kkey(alias)
    rt = _routing_mod()
    roles = _read_models(alias).get("roles") or {}
    _sync_models_sync(alias)  # ensure the step's resolved model exists + the key allows it
    step = dict(body.step or {})
    step["model"] = rt.gateway_model(step.get("model") or "preprocess", roles)
    try:
        res = _preprocess_mod().run_one_step(
            step, body.text, key, doc_type=(body.doc_type or None)
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"gateway error: {e}")
    meta = {k: v for k, v in (res.get("meta") or {}).items() if not k.startswith("_")}
    return {"ok": True, "output": res.get("text"), "dropped": res.get("dropped", False),
            "meta": meta, "error": res.get("error")}


class CompileBody(BaseModel):
    intent: str
    output: str = ""      # "rewrite" | "signal"
    kind: str = ""        # legacy alias for output ("transform"/"classify")
    labels: list = []


@app.post("/api/knowledge/{alias}/pipeline/compile-prompt")
def knowledge_compile_prompt(alias: str, body: CompileBody):
    """Sonnet authors a robust Flash execution prompt from plain-English intent. Authoring-time
    only — billed to the project key; the returned prompt is what Flash runs per document."""
    key = _kkey(alias)
    if not (body.intent or "").strip():
        raise HTTPException(400, "intent is empty")
    rt = _routing_mod()
    roles = _read_models(alias).get("roles") or {}
    _sync_models_sync(alias)  # ensure the project's reasoner model exists + the key allows it
    reasoner = rt.gateway_model("reasoner", roles)
    output = body.output or body.kind or "rewrite"
    try:
        prompt = _preprocess_mod().compile_prompt(
            body.intent, output, key, labels=body.labels, model=reasoner
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"gateway error: {e}")
    return {"ok": True, "prompt": prompt, "compiledBy": reasoner}


class IngestBody(BaseModel):
    text: str = ""
    tags: list = []
    files: list = []  # [{name, data(base64)}]
    model: str = ""   # extractor override; "" / "auto" defers to the cognify strategy
    doc_type: str = ""  # feeds preprocessing scope + cognify routing
    preprocess: bool = True


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
    # Make the project's model map real at the gateway before ingest.py resolves roles to it.
    await asyncio.to_thread(_sync_models_sync, alias)
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
    jobfile.write_text(json.dumps({
        "text": body.text, "files": paths, "tags": body.tags, "model": body.model,
        "doc_type": body.doc_type, "preprocess": body.preprocess,
    }))
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
