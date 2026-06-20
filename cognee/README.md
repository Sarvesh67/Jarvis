# Jarvis Cognee

Shared knowledge engine for all Jarvis projects. Cognee builds a graph+vector knowledge
graph per project on FalkorDB, with the LLM + embeddings served through the LiteLLM gateway.

## Layout

- `jarvis_cognee.py` — bootstrap: `configure(project)` wires Cognee → FalkorDB + gateway.
- `smoke_test.py` — end-to-end check: `add → cognify → search` for a project.
- `requirements.lock` — frozen, known-good dependency set (see gotchas).
- `data/<project>/` — per-project Cognee system + data dirs.

## Run

```bash
cd /Users/sarvesh/Documents/Jarvis/cognee
./.venv/bin/python smoke_test.py hedgefund   # or msme
```

Prereqs: the platform stack up (`docker compose -f ../platform/docker-compose.yml up -d`),
Ollama serving with `nomic-embed-text`, and the project's LiteLLM key in `../platform/.env`.

## Architecture

- One FalkorDB **graph per project** (`<project>_graph`) — graph + vectors in the same
  hybrid store. Isolation is by graph name + per-project Cognee data dir.
- LLM (`extractor` = gpt-4o-mini) and embeddings (`embed` = local nomic-embed-text, 768-dim)
  both go through the LiteLLM gateway (`:4000`), billed to the project's budgeted key.

## Gotchas (why the config looks the way it does)

cognee 1.1.2 + the community falkor adapter 0.3.1 needed four non-obvious fixes:

1. **`LLM_INSTRUCTOR_MODE=tool_call`** — cognee defaults instructor mode to `""`, which routes
   to instructor 1.15.3's json handler that crashes on `response_model=str` (used all over
   cognee's completion paths). `tool_call` mode wraps primitives correctly. This is the big one.
2. **LLM provider = `custom`, model = `openai/extractor`** — provider `openai` uses a native
   client that ignores `api_base`; `custom` passes `api_base` to litellm so calls hit the gateway.
3. **Embeddings provider = `openai_compatible`** (not `openai`) — avoids cognee trying to map the
   alias `embed` to a tiktoken encoding by name (404).
4. **`vector_db_name` must be set** — defaults to `""`, which FalkorDB rejects as an empty graph
   key. Set to `<project>_graph` so vectors share the project's hybrid graph.

Plus `COGNEE_SKIP_CONNECTION_TEST=true` (the preflight also trips the `response_model=str` bug)
and `ENABLE_BACKEND_ACCESS_CONTROL=false` (single-user local box). All handled in `jarvis_cognee.py`.

Reinstall from the lock if the env is ever rebuilt:
```bash
uv pip install --python .venv -r requirements.lock
```
