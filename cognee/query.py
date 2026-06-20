"""Query a project's Cognee knowledge graph. Clean stdout for agent/tool use.

Usage:
    python query.py <project> <natural-language question>
    python query.py hedgefund "What did Reliance report and who commented?"
    python query.py --answer hedgefund "..."   # let Cognee synthesize (standalone, 1 LLM call)

Default mode is RETRIEVAL-ONLY: it returns the relevant graph context (entities +
their typed connections) with **zero LLM calls** — just a local-embedding vector
search over the graph plus deterministic text resolution. The Hermes agent (Opus)
then reasons over this context. This is the single reasoning brain: we never pay an
LLM to synthesize here only to have the agent reason over that synthesis again.

Use --answer only for standalone, agent-less use (e.g. a dashboard search box) where
no downstream reasoner exists; it runs Cognee's GRAPH_COMPLETION (1 LLM call).

Answer/context prints to stdout; Cognee's logs go to stderr (redirect with 2>/dev/null).
"""
import asyncio
import sys

import jarvis_cognee as jc


async def retrieve(project: str, question: str) -> None:
    """Retrieval-only: graph triplets resolved to text, no LLM completion."""
    import cognee

    jc.configure(project)
    results = await cognee.recall(question, only_context=True, datasets=[project])
    parts = [r.text for r in (results or []) if getattr(r, "text", None)]
    ctx = "\n\n".join(parts)
    if not ctx.strip():
        print("No relevant knowledge found in the graph for this query.")
        return
    print(ctx)


async def answer(project: str, question: str) -> None:
    """Standalone synthesis via Cognee (1 LLM call). For agent-less callers only."""
    import cognee
    from cognee import SearchType

    jc.configure(project)
    res = await cognee.search(
        query_text=question,
        query_type=SearchType.GRAPH_COMPLETION,
        datasets=[project],
    )
    if isinstance(res, list):
        for r in res:
            print(r)
    else:
        print(res)


if __name__ == "__main__":
    argv = sys.argv[1:]
    mode = retrieve
    if argv and argv[0] == "--answer":
        mode = answer
        argv = argv[1:]
    if len(argv) < 2:
        print("usage: python query.py [--answer] <project> <question>", file=sys.stderr)
        sys.exit(2)
    asyncio.run(mode(argv[0], " ".join(argv[1:])))
