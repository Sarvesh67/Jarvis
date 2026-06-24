# Jarvis — A Knowledge Engine Platform

Build a domain-specific **knowledge engine** (an ontology-guided knowledge graph + an
agent that reasons over it) for **any project**, on your own machine, with hard budget
control over every LLM call.

Jarvis is a *neuro-symbolic* second brain: neural extraction (LLMs) + embeddings build a
**symbolic** knowledge graph (typed entities and relationships), and an agent reasons over
that graph through a chat UI. Each project gets its own isolated graph, ontology, budget,
and agent profile.

> This repo ships with two example projects — `hedgefund` (market research) and `msme`
> (a small-business second brain) — but the whole point is that you can spin up a knowledge
> engine for **anything**: a research domain, a codebase, your personal notes, a company wiki.

---

## What you get

- **Per-project knowledge graphs** — isolated graph + vector store per project (FalkorDB hybrid).
- **Ontology-guided extraction** — define your domain's entity/relation types (3 ways: tags,
  custom types, or OWL/RDF) so a cheap model extracts like an expensive one.
- **A budget-governed LLM gateway** — every call routes through LiteLLM with per-project
  hard-stop budgets; spend is tracked per request.
- **A live agent chat UI** — a "Command Center" dashboard where you chat with a real agent
  that streams its tool calls, plan, and reasoning, and queries the knowledge graph live.
- **A clean ingest → retrieve pipeline** with an explicit cost model (see below).

---

## Architecture

```
                          ┌─────────────────────────────────────────┐
                          │  Dashboard  (FastAPI + vanilla-JS SPA)   │
                          │  chat · transactions · knowledge engine  │
                          └───────────────┬─────────────┬───────────┘
                                 ACP (stdio)│             │ HTTP
                          ┌────────────────▼───┐   ┌─────▼──────────────┐
                          │  Hermes agent      │   │  Cognee            │
                          │  (per-project      │   │  ingest / retrieve │
                          │   profile, reasons)│   │  (graph build)     │
                          └─────────┬──────────┘   └─────────┬──────────┘
                                    │  every LLM call         │
                          ┌─────────▼─────────────────────────▼─────────┐
                          │     LiteLLM gateway (:4000)  — budgets       │
                          │ preprocess · extractor · extractor-pro ·     │
                          │ reasoner · fast · embed                      │
                          └─────────┬───────────────────────┬───────────┘
                            cloud   │                        │  local
                          ┌─────────▼────────┐      ┌────────▼─────────┐
                          │   OpenRouter     │      │  Ollama (embed,  │
                          │ (GPT/Claude/...) │      │  local models)   │
                          └──────────────────┘      └──────────────────┘

                          FalkorDB (:6379) — one graph per project · Postgres (:5432) — spend ledger
```

| Component | Role | Lives in |
|---|---|---|
| **LiteLLM gateway** | One OpenAI-compatible endpoint for all LLM traffic; per-project budgets + spend ledger | `platform/` |
| **FalkorDB** | Graph + vector hybrid store; one graph per project | Docker (`platform/`) |
| **Cognee** | Builds the graph (`add` → `cognify`) and retrieves context | `cognee/` |
| **Hermes** | The agent that reasons over retrieved context; one profile per project | `~/.hermes/` |
| **Dashboard** | Web UI: chat, transactions, knowledge-engine management | `dashboard/` |

---

## How it works — the cost model

The split that keeps quality high and cost low:

**Ingest (write-once, read-many) — invest here.** Two configurable stages (the **Pipeline** tab):
- **Pre-process** (optional, cheap): ordered steps clean / filter / annotate each doc before it
  hits the graph. Each step declares an output mode — **rewrite** the text (strip boilerplate,
  drop junk via a `DROP` gate) or **extract a signal** (a label like `relevance: high`, or a
  free-form value like a detected doc type) that the cognify router can use. Runs on a cheap model
  (DeepSeek V4 Flash), prose-in/prose-out. Keeps the graph clean and cuts cognify tokens. Steps are
  editable per project; describe intent and let Sonnet compile the prompt.
- **Cognify** → entity/relation extraction. **One LLM call per chunk**, baked into the graph
  permanently, so it's the place to spend. A **cognify strategy** routes each doc to a model by
  relevance (DeepSeek V4 Flash for bulk, V4 Pro for high-value). A good **ontology** lets the cheap
  model punch above its weight.
- `cognee.add` → chunk + embed (embeddings are local/free). ~No LLM cost.

Models are referenced by **role** (`preprocess`, `extractor`, `extractor-pro`, `reasoner`); the
**Models** tab maps each role to a real OpenRouter model per project (stored locally, applied to the
gateway live), so you can re-point `extractor` without editing config or the pipeline.

**Retrieve (read-many) — keep it lean.**
- `cognee/query.py` uses `recall(..., only_context=True)` → returns the relevant graph
  context (entities + typed connections) with **zero LLM calls**.
- The **agent** then reasons over that context. Reasoning happens **once**, at the agent —
  never pay an LLM to synthesize an answer only to have the agent reason over it again.

So: **Cognee is the memory substrate; the agent is the single reasoning brain.** Pick the
agent's model (e.g. Opus) for reasoning quality; pick the extractor model for graph quality.

---

## Quickstart

**Prerequisites:** macOS, [Homebrew](https://brew.sh), Docker (via Colima), an
[OpenRouter](https://openrouter.ai/keys) API key, and [Ollama](https://ollama.com) serving
`nomic-embed-text`.

```bash
git clone <your-fork-url> Jarvis && cd Jarvis

# 1. Secrets — copy the template; you only need to paste your OpenRouter key.
cp platform/.env.example platform/.env
$EDITOR platform/.env          # set OPENROUTER_API_KEY

# 2. Build everything (idempotent — safe to re-run).
bash setup/00_run_all.sh
```

`setup/00_run_all.sh` runs, in order:

| Script | Does |
|---|---|
| `01_prereqs.sh` | Homebrew, Colima, base tooling |
| `02_platform.sh` | Generates remaining `.env` secrets; starts FalkorDB + Postgres + LiteLLM |
| `03_keys.sh` | Creates per-project virtual keys with monthly budgets |
| `04_cognee.sh` | Sets up the Cognee venv + FalkorDB wiring |
| `05_hermes.sh` | Installs Hermes; builds per-project agent profiles |
| `06_dashboard.sh` | Sets up the dashboard venv + autostart |

Then open the dashboard at **http://127.0.0.1:8080**.

Full operational reference (start/stop, reload, troubleshooting): see **[RUNBOOK.md](RUNBOOK.md)**.

---

## Build a knowledge engine for a new project

Say you want a project called `research`. Six steps:

1. **Budget key** — add `RESEARCH_LLM_KEY=` to `platform/.env`, add a line to
   `setup/03_keys.sh` (`ensure_key research 20 RESEARCH_LLM_KEY`), and re-run it.
   This creates a LiteLLM virtual key with a hard monthly budget.

2. **Register the project** in two places:
   - `cognee/jarvis_cognee.py` → add `"research": "RESEARCH_LLM_KEY"` to `PROJECT_KEYS`.
   - `dashboard/app.py` → add `"research"` to `KNOWLEDGE_ALIASES` and a row to the project
     list (`alias`, `label`, `graph: "research_graph"`, `key_env`).

3. **Agent profile** — create a Hermes profile (`05_hermes.sh` is the template) so the agent
   bills to the project's key and can query its graph via the `jarvis-knowledge` skill.

4. **Define the ontology** (optional but high-leverage) — in the dashboard's *Knowledge
   engine → Ontology* tab, declare your domain's entity/relation types (or upload an OWL
   file). This guides extraction so the graph captures *your* domain's structure.

5. **Pipeline** (optional) — *Knowledge engine → Pipeline*: configure pre-processing steps
   (rewrite / extract-signal) and the cognify routing strategy (which model per doc). On the
   *Models* tab, map each role to an OpenRouter model. Defaults work out of the box.

6. **Ingest** — *Knowledge engine → Add data*: paste text or attach files, set a doc type, leave
   the extractor on **Auto** (uses your strategy) or pick a role to override, and run. Then chat.

That's it — a new isolated graph (`research_graph`), budget, ontology, and agent.

---

## Repository layout

```
platform/      Docker services (FalkorDB + Postgres) + LiteLLM gateway config
cognee/        Knowledge engine — ingest.py, query.py, jarvis_cognee.py (own venv)
dashboard/     Web UI — app.py (FastAPI), acp.py (live agent), static/index.html (own venv)
setup/         Idempotent rebuild scripts (00_run_all → 01..06)
tests/         pytest suite + postdeploy checks
launchagents/  macOS autostart service templates
RUNBOOK.md     Operational guide
```

> Note: `cognee/` and `dashboard/` each have their **own** virtualenv (`.venv/`) — they have
> conflicting dependencies and must never share one.

---

## Security

- **No secrets in the repo.** All keys live in `platform/.env` (gitignored). Configs
  reference them via `os.environ/...`, never inline.
- **`cognee/data/` is gitignored** — it holds your ingested documents and graph state.
- Everything binds to `127.0.0.1`; nothing is exposed to the network by default.
- Before publishing a fork, double-check: `git status` should never show `platform/.env`
  or anything under `cognee/data/`.

---

## License

[MIT](LICENSE) © 2026 Sarvesh Shinde
