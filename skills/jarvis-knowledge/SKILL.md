---
name: jarvis-knowledge
description: "Query a Jarvis project's Cognee knowledge graph (hedgefund, msme) for facts, entities, and multi-hop reasoning."
version: 1.0.0
platforms: [macos]
metadata:
  hermes:
    tags: [cognee, knowledge-graph, falkordb, hedgefund, msme, rag, alpha, research]
    related_skills: []
---

# Jarvis Knowledge Graph

Each Jarvis project has its own Cognee knowledge graph on FalkorDB (graph + vector hybrid),
built from that project's ingested data. Use it to answer questions that need facts,
relationships, or multi-hop reasoning over a project's domain — not your own training data.

## When to use

- The user asks about entities/events/relationships in a project's data
  (companies, people, market events, MSME suppliers, signals, etc.).
- You need grounded, cited-from-the-graph answers rather than general knowledge.

## Projects

| Project | Domain |
|---------|--------|
| `hedgefund` | Indian markets: instruments, companies, market events, alpha signals |
| `msme`      | Indian MSME B2B: companies, capabilities, trade relationships |

## How to query

Run the project's knowledge graph with one command (graph context prints to stdout; logs go to stderr):

```bash
/Users/sarvesh/Documents/Jarvis/cognee/.venv/bin/python \
  /Users/sarvesh/Documents/Jarvis/cognee/query.py <project> "<natural language question>" 2>/dev/null
```

Example:

```bash
/Users/sarvesh/Documents/Jarvis/cognee/.venv/bin/python \
  /Users/sarvesh/Documents/Jarvis/cognee/query.py hedgefund "What did Reliance report and who commented?" 2>/dev/null
```

Pick `<project>` from the table above based on the question's domain. Pass the user's question
(rephrased clearly) as the query. You can run it more than once with different phrasings to pull
in more of the graph before answering.

## What you get back, and your job

This returns **retrieved graph context, not a finished answer** — a list of `Nodes:`
(entities with their content) and `Connections:` (typed edges like `is_a`, `contains`,
`made_from` between them). It makes **no LLM call** — it is pure retrieval.

**You are the reasoner.** Read the nodes and connections, follow the relationships to do any
multi-hop reasoning, and synthesize the answer yourself, grounded in this context and the
conversation. Do not just paste the raw triplets back to the user — interpret them. Cite the
entities/relationships you used.

## Notes

- Retrieval is vector entry points + graph traversal, so phrasing the question in terms of
  entities/events works best. If the first pull is thin, query again with different wording.
- If the graph returns "No relevant knowledge found" or nothing useful, say so plainly rather
  than inventing facts.
- Embeddings run locally (free); this query path itself spends no LLM budget. Only your own
  reasoning turn bills the project budget.
- Rare standalone case: prefix the project with `--answer` to have Cognee synthesize a
  one-shot answer itself (1 extra LLM call). Don't use this when you (the agent) are answering —
  it would mean reasoning twice over the same context.
