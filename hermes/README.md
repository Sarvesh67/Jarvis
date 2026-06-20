# Hermes — the common Jarvis agent

Hermes Agent (Nous Research) is the single agent across all Jarvis projects. Its brain runs
through the LiteLLM gateway (so spend is budgeted per project), and it queries each project's
Cognee/FalkorDB knowledge graph via the `jarvis-knowledge` skill.

Install lives at `~/.hermes` (config + `.env` + `hermes-agent/` code); binary `~/.local/bin/hermes`.
This dir is notes only — the live config is under `~/.hermes`.

## Profiles (per-project)

| Command | Profile | Brain key (budget) | Use |
|---------|---------|--------------------|-----|
| `jarvis`    | jarvis    | jarvis key ($20/mo)   | generic / non-project work |
| `hermes`    | default   | jarvis key (shared)   | synonym for the generic agent |
| `hedgefund` | hedgefund | hedgefund key ($40/mo)| AI Hedge Fund project |
| `msme`      | msme      | msme key ($40/mo)     | MSME project |

`jarvis` is the generic agent — project names are never used for general stuff.

Each profile is `~/.hermes/profiles/<name>/` with its own `config.yaml` (carrying that project's
LiteLLM key), `.env`, `SOUL.md`, and `skills/`. Wrapper scripts in `~/.local/bin` run `hermes -p <name>`.

Run one-shot: `msme chat -q "..." --yolo`  ·  interactive: `hedgefund` or `hedgefund --tui`.

## Brain config (the non-obvious bits)

In each profile's `config.yaml` `model:` block:
- `provider: custom`, `base_url: http://127.0.0.1:4000/v1`, `default: reasoner`, `context_length: 200000`
- `api_key: "<project LiteLLM key>"` — **must be here.** The runtime turn-client reads the key from
  `model.api_key` (config), NOT from `.env`. `CUSTOM_API_KEY` in `.env` is only used for model listing,
  so a key set only in `.env` yields `no-key-required` → 401. config.yaml is chmod 600.
- A shared `config.yaml` would bill all profiles to one key, so each profile gets its own copy.

## Knowledge skill

`~/.hermes/profiles/<name>/skills/jarvis-knowledge/SKILL.md` teaches the agent to run:
```
Jarvis/cognee/.venv/bin/python Jarvis/cognee/query.py <project> "<question>" 2>/dev/null
```
Verified: the agent loads the skill, runs the query itself, and answers from the graph.

## Budget control

All brain spend flows through the gateway → visible/adjustable at `http://127.0.0.1:4000/ui`.
To change a project's agent budget, edit its key in the LiteLLM UI. To switch the agent's model,
edit `default:` in that profile's `config.yaml` (e.g. `fast` for cheaper, `reasoner` for stronger).
