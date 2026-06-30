"""Graph admin CLI — destructive graph maintenance through Cognee's own CRUD API.

Runs inside the Cognee venv (heavy deps), invoked by the dashboard as a subprocess so the
relational + vector + graph stores stay consistent. We deliberately go through
`cognee.forget` (the unified delete API) rather than poking FalkorDB directly, because a raw
`DETACH DELETE` would orphan the vector embeddings and the pipeline-status bookkeeping that
Cognee keeps per dataset — the next cognify would then double-count or refuse to re-process.

Each Jarvis project maps to ONE cognee dataset (dataset_name == project) and ONE FalkorDB
graph ({project}_graph); see jarvis_cognee.configure(). So a dataset-scoped forget targets
exactly that project and nothing else.

Modes:
  reset   --project X   forget(dataset=X, memory_only=True)
                        → wipe graph nodes/edges + vector embeddings, RESET pipeline status,
                          but KEEP the raw source files, so `cognify` can rebuild from scratch.
  wipe    --project X   forget(dataset=X)
                        → delete the whole dataset: graph + vectors + raw data records.
  delete  --project X --data-id <uuid>
                        → forget(dataset=X, data_id=<uuid>) — drop one source document and
                          everything derived from it.

Prints a single JSON line with the deletion summary to stdout (machine-readable for the API).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

import jarvis_cognee as jc


async def _run(args) -> dict:
    import cognee

    jc.configure(args.project)  # binds this process to the project's dataset + FalkorDB graph

    if args.mode == "reset":
        return await cognee.forget(dataset=args.project, memory_only=True)
    if args.mode == "wipe":
        return await cognee.forget(dataset=args.project)
    if args.mode == "delete":
        if not args.data_id:
            raise SystemExit("--data-id required for delete")
        import uuid
        return await cognee.forget(dataset=args.project, data_id=uuid.UUID(args.data_id))
    raise SystemExit(f"unknown mode {args.mode}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="cognee.admin")
    ap.add_argument("mode", choices=["reset", "wipe", "delete"])
    ap.add_argument("--project", required=True)
    ap.add_argument("--data-id", default="")
    args = ap.parse_args(argv)
    try:
        result = asyncio.run(_run(args))
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(e)}), flush=True)
        sys.exit(1)
    summary = result if isinstance(result, dict) else {"result": str(result)}
    print(json.dumps({"ok": True, **summary}), flush=True)


if __name__ == "__main__":
    main()
