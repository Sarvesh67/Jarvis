# jarvis-knowledge

Retrieval over a project's Cognee knowledge graph on FalkorDB. Shared by every Hermes
profile, which is why it sits at the repo root rather than under one project.

## Which copy this is

Four profiles carry this skill. `cosmetics`, `hedgefund` and `jarvis` held byte-identical
copies — this is that file. **`msme` has diverged** and is deliberately not overwritten by
it: its copy carries roughly 1.7 KB more, hand-written and worth reading before any
consolidation.

| Only in the msme copy | What it says |
|---|---|
| Multi-angle parallel querying | Run 2-3 differently-phrased queries at once; the graph's vector entry points are sensitive to wording and a single query under-retrieves |
| `references/msme-surat-textile-cluster.md` | Known companies, product flows and raw-material nodes, loaded before querying to avoid re-asking the graph what is already known |
| Conversation-level caching | Do not re-query something already answered earlier in the conversation |

That is real technique, learned on the graph where it was needed. Whether it generalises
to the others is a judgement call nobody has made yet, so the divergence stands rather
than being resolved by whoever copies a file next.

## Note for the cosmetics profile

This skill's project table is `hedgefund` and `msme`. **Cosmetics is not in it**, and that
is correct — the cosmetics graph is Neo4j and deliberately outside the Cognee semantic
layer. A cosmetics question is answered by `cosmetics/skills/jarvis-cosmetics-search`,
never by querying another project's graph through this one.
