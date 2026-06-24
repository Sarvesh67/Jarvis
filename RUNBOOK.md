# Jarvis Platform — Runbook

Operator reference for the shared multi-project agent platform on the Mac Mini M4.
Everything runs as **sarvesh** (no separate service account). All ports bind `127.0.0.1` only.

## Architecture

```
        ┌─────────────── Dashboard :8080 (one pane: budgets + graphs + agents) ──────────────┐
        │                                                                                      │
  Hermes agent (per-project profiles)        LiteLLM gateway :4000  ── OpenRouter (cloud)
   jarvis / hedgefund / msme  ──brain──▶      (budgets, spend, /ui)  ── Ollama :11434 (local, free)
        │                                            ▲
        └── jarvis-knowledge skill ──▶ Cognee ───────┘  (LLM + embeddings via gateway)
                                          │
                                   FalkorDB :6379  (graph + vector hybrid, one graph per project)
                                          └ browser UI :3000
                                   Postgres :5432  (LiteLLM spend ledger)
```

Containers (FalkorDB, Postgres, LiteLLM) run under **Colima** (Docker). Colima + the dashboard
autostart at login; containers have `restart: unless-stopped`.

## URLs

| What | URL | Login |
|------|-----|-------|
| Dashboard | http://127.0.0.1:8080 | none |
| LiteLLM (budgets/models/spend) | http://127.0.0.1:4000/ui | user `admin`, pw = `LITELLM_MASTER_KEY` |
| FalkorDB graph browser | http://127.0.0.1:3000 | none |

## Agents

| Command | Bills to | Use |
|---------|----------|-----|
| `jarvis --tui`    | jarvis ($20/mo)   | generic / non-project |
| `hedgefund --tui` | hedgefund ($40/mo)| AI Hedge Fund |
| `msme --tui`      | msme ($40/mo)     | MSME project |

One-shot: `jarvis chat -q "…" --yolo`. Query a graph directly:
`cognee/.venv/bin/python cognee/query.py <project> "<question>" 2>/dev/null`

## Start / stop / status

```bash
# status of everything
docker compose -f platform/docker-compose.yml ps
launchctl print gui/$(id -u)/com.jarvis.dashboard | grep state
colima status

# start (Colima autostarts at login; if not:)
brew services start colima
docker compose -f platform/docker-compose.yml up -d
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jarvis.dashboard.plist

# stop the data/gateway stack (agents are CLI, nothing to stop)
docker compose -f platform/docker-compose.yml stop
# full stop incl. VM:
colima stop
```

## Health checks

```bash
curl -s http://127.0.0.1:4000/health/liveliness          # gateway -> {"status":"healthy"}
docker exec jarvis-falkordb redis-cli PING               # -> PONG
docker exec jarvis-falkordb redis-cli GRAPH.LIST         # project graphs
docker exec jarvis-postgres pg_isready -U jarvis -d jarvis
curl -s http://127.0.0.1:8080/api/overview | python3 -m json.tool
curl -s http://127.0.0.1:11434/api/tags                  # ollama models
```

## Budgets

- View/edit per-project budgets in the dashboard or at `:4000/ui`. Total target: **$100/mo**
  (hedgefund $40, msme $40, jarvis $20), monthly reset.
- The OpenRouter account balance is the ultimate hard cap — keep it funded/limited there too.
- Embeddings run on Ollama = **$0**. The cloud models cost money. Pipeline models are referenced
  by **role** and mapped to a real OpenRouter model **per project** on the dashboard *Models* tab
  (stored in the gitignored `cognee/data/<project>/models.json`, pushed to the gateway as deduped
  `or--<slug>` DB models). Defaults: `preprocess`/`extractor` = DeepSeek V4 Flash, `extractor-pro` =
  DeepSeek V4 Pro, `reasoner` = Sonnet (also compiles step prompts). Shared globals in
  `config.yaml`: `reasoner`, `fast` (Haiku), `embed`. Re-point a role with no restart and no git
  edit; `cognee/sync_models.py` re-syncs the gateway + key allowlist at setup.

## Kill switches

```bash
# stop all cloud spend immediately: stop the gateway (agents + cognee then fail closed)
docker compose -f platform/docker-compose.yml stop litellm
# or zero a project's budget in :4000/ui (that key 429s until raised)
# stop the agent doing work: it's a CLI — just Ctrl-C / close the session
```

## Troubleshooting

- **Gateway 401 "LiteLLM Virtual Key expected"** — a profile's `config.yaml model.api_key` is wrong/missing.
  The Hermes `custom` provider reads the key from `config.yaml`, NOT `.env`. See `hermes/README.md`.
- **Cognee "Invalid graph operation on empty key"** — `vector_db_name`/`graph_database_name` unset;
  `cognee/jarvis_cognee.py` sets both to `<project>_graph`.
- **Cognee `response_model=str` / model_json_schema error** — instructor mode; `jarvis_cognee.py` sets
  `LLM_INSTRUCTOR_MODE=tool_call`. Don't remove it.
- **Dashboard down** — `cat dashboard/dashboard.log`; reload `launchctl kickstart -k gui/$(id -u)/com.jarvis.dashboard`.
- **Docker unreachable** — `colima start` (or `brew services restart colima`).

## Rebuild from scratch

```bash
cd /Users/sarvesh/Documents/Jarvis
bash setup/00_run_all.sh        # idempotent; runs 01_prereqs → 06_dashboard
```
You only supply the OpenRouter key (prompted in `02_platform`). Dependency versions are pinned in
`cognee/requirements.lock` and `dashboard/requirements.lock`. Per-component notes:
`cognee/README.md`, `hermes/README.md`.
