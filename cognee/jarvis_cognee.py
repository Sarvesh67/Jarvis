"""Jarvis Cognee bootstrap.

Shared knowledge engine: FalkorDB (graph + vector hybrid), brain + embeddings via the
LiteLLM gateway. One dataset + one FalkorDB graph per project, so projects stay isolated.

Usage:
    import jarvis_cognee as jc
    jc.configure("hedgefund")   # or "msme"
    # ... then cognee.add / cognee.cognify / cognee.search
"""
import os
import pathlib

# Single-user local box — turn off Cognee 1.1's multi-tenant auth gating.
os.environ.setdefault("ENABLE_BACKEND_ACCESS_CONTROL", "false")
# Cognee defaults the instructor mode to "" which routes to instructor 1.15.3's json
# handler — that crashes on response_model=str (used across cognee's completion paths).
# "tool_call" mode wraps primitives correctly. This is the real fix.
os.environ.setdefault("LLM_INSTRUCTOR_MODE", "tool_call")
# Belt-and-suspenders: the LLM/embedding preflight also uses response_model=str; the real
# pipeline works with tool_call mode, but skip the preflight to avoid any edge cases.
os.environ.setdefault("COGNEE_SKIP_CONNECTION_TEST", "true")

import cognee_community_hybrid_adapter_falkor.register  # noqa: F401  registers the 'falkor' provider
import cognee

BASE = pathlib.Path(__file__).resolve().parent
PLATFORM_ENV = BASE.parent / "platform" / ".env"
GATEWAY = "http://127.0.0.1:4000/v1"

# project -> the LiteLLM virtual-key env var that carries its budget
PROJECT_KEYS = {
    "hedgefund": "HEDGEFUND_LLM_KEY",
    "msme": "MSME_LLM_KEY",
}


def _load_env(path: pathlib.Path) -> dict:
    d = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                d[k] = v
    return d


def configure(project: str) -> str:
    """Point Cognee at FalkorDB + the LiteLLM gateway for the given project."""
    if project not in PROJECT_KEYS:
        raise ValueError(f"unknown project {project!r}; expected {list(PROJECT_KEYS)}")
    env = _load_env(PLATFORM_ENV)
    key = env.get(PROJECT_KEYS[project])
    if not key:
        raise RuntimeError(f"missing {PROJECT_KEYS[project]} in {PLATFORM_ENV}")

    # Stable storage outside site-packages, isolated per project.
    sysdir = BASE / "data" / project / "system"
    datadir = BASE / "data" / project / "data"
    sysdir.mkdir(parents=True, exist_ok=True)
    datadir.mkdir(parents=True, exist_ok=True)
    cognee.config.system_root_directory(str(sysdir))
    cognee.config.data_root_directory(str(datadir))

    # LLM (cognify/extraction) via the gateway — billed to the project's budgeted key.
    # provider="custom" uses Cognee's generic adapter, which passes api_base to litellm;
    # the "openai/" prefix tells litellm the endpoint is OpenAI-compatible (our gateway).
    cognee.config.set_llm_provider("custom")
    cognee.config.set_llm_endpoint(GATEWAY)
    cognee.config.set_llm_model("openai/extractor")
    cognee.config.set_llm_api_key(key)

    # Embeddings via the same gateway (Ollama backend, free), 768-dim.
    # Use "openai_compatible" (not "openai") so Cognee doesn't try to map the
    # alias "embed" to a tiktoken encoding by name (which 404s).
    cognee.config.set_embedding_provider("openai_compatible")
    cognee.config.set_embedding_endpoint(GATEWAY)
    cognee.config.set_embedding_model("embed")
    cognee.config.set_embedding_api_key(key)
    cognee.config.set_embedding_dimensions(768)

    # Graph + vector hybrid on FalkorDB. Cognee 1.1 creates one database per dataset via a
    # "dataset database handler"; the falkor adapter ships falkor_graph_local / falkor_vector_local
    # handlers that key each dataset's graph by dataset id. Select them, or the default
    # lancedb/ladybug handlers run and hand FalkorDB an empty graph key.
    cognee.config.set_graph_db_config(
        {
            "graph_database_provider": "falkor",
            "graph_database_url": "localhost",
            "graph_database_port": 6379,
            "graph_database_name": f"{project}_graph",
            "graph_dataset_database_handler": "falkor_graph_local",
        }
    )
    cognee.config.set_vector_db_config(
        {
            "vector_db_provider": "falkor",
            "vector_db_url": "localhost",
            "vector_db_port": 6379,
            # Same graph as the graph store: FalkorDB is a hybrid (graph + vector) store, so
            # both live in one per-project graph. Without this, vector_db_name defaults to ""
            # and FalkorDB rejects the empty graph key.
            "vector_db_name": f"{project}_graph",
            "vector_dataset_database_handler": "falkor_vector_local",
        }
    )
    return project
