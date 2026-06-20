"""End-to-end smoke test: add -> cognify -> search, per project, on FalkorDB."""
import asyncio
import sys

import jarvis_cognee as jc
import cognee
from cognee import SearchType

SAMPLE = (
    "Reliance Industries reported Q3 FY26 results that beat analyst estimates, with "
    "net profit up 12% year on year, driven by strong Jio subscriber additions and "
    "higher retail margins. Chairman Mukesh Ambani said Jio will accelerate its 5G "
    "rollout across rural India. Analysts at Morgan Stanley raised their price target, "
    "citing the telecom momentum."
)


async def main(project: str):
    jc.configure(project)
    print(f"[{project}] pruning for a clean smoke test ...", flush=True)
    await cognee.prune.prune_data()
    await cognee.prune.prune_system(metadata=True)

    print(f"[{project}] add ...", flush=True)
    await cognee.add(SAMPLE, dataset_name=project)

    print(f"[{project}] cognify (building knowledge graph) ...", flush=True)
    await cognee.cognify(datasets=[project])

    print(f"[{project}] search (GRAPH_COMPLETION) ...", flush=True)
    res = await cognee.search(
        query_text="What did Reliance report, and who commented on it?",
        query_type=SearchType.GRAPH_COMPLETION,
        datasets=[project],
    )
    print("\n=== RESULT ===")
    print(res)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "hedgefund"))
