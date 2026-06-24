"""Sync a project's model map to the LiteLLM gateway (setup-time + CLI).

Reads <data>/<project>/models.json (materializing it from seeds/<project>/models.json or the
code defaults in routing.DEFAULT_ROLE_MODELS), ensures a deduped DB-pool model `or--<slug>`
exists for each distinct OpenRouter id, and sets the project's virtual-key allowlist to the
shared global models + its pool models.

This is the **setup-time** counterpart of dashboard/app.py:_sync_models_sync — the two are
kept parallel across the cognee/dashboard venv boundary (a few short admin calls). Run by
setup/04_cognee.sh; also usable standalone:

    .venv/bin/python sync_models.py --project hedgefund
"""
import argparse
import json
import pathlib
import sys

import httpx

import routing

BASE = pathlib.Path(__file__).resolve().parent
PLATFORM_ENV = BASE.parent / "platform" / ".env"
GATEWAY = "http://127.0.0.1:4000"
SHARED_MODELS = ["reasoner", "fast", "embed"]  # global config models every key keeps
KEY_ENV = {"hedgefund": "HEDGEFUND_LLM_KEY", "msme": "MSME_LLM_KEY", "jarvis": "JARVIS_LLM_KEY"}


def _env() -> dict:
    d = {}
    if PLATFORM_ENV.exists():
        for line in PLATFORM_ENV.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                d[k] = v
    return d


def _materialize_roles(project: str) -> dict:
    """Return the project's role->id map, writing the local file from seed/defaults if absent."""
    local = BASE / "data" / project / "models.json"
    if local.exists():
        try:
            return (json.loads(local.read_text()) or {}).get("roles") or {}
        except Exception:  # noqa: BLE001
            pass
    seed = BASE / "seeds" / project / "models.json"
    roles = None
    if seed.exists():
        try:
            roles = (json.loads(seed.read_text()) or {}).get("roles") or None
        except Exception:  # noqa: BLE001
            roles = None
    if not roles:
        roles = dict(routing.DEFAULT_ROLE_MODELS)
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(json.dumps({"roles": roles}, indent=2))
    return roles


def sync(project: str) -> int:
    env = _env()
    master = env.get("LITELLM_MASTER_KEY", "")
    if not master:
        print("  ! no LITELLM_MASTER_KEY in platform/.env — skipping model sync")
        return 1
    or_key = env.get("OPENROUTER_API_KEY", "")
    if not or_key:
        print("  ! no OPENROUTER_API_KEY in platform/.env — pool models would fail auth; skipping")
        return 1
    headers = {"Authorization": f"Bearer {master}", "Content-Type": "application/json"}
    roles = {**routing.DEFAULT_ROLE_MODELS, **_materialize_roles(project)}
    pool = {routing.pool_model_name(mid): mid for mid in roles.values() if mid}
    try:
        existing = {m.get("model_name") for m in
                    httpx.get(f"{GATEWAY}/model/info", headers={"Authorization": f"Bearer {master}"},
                              timeout=10).json().get("data", [])}
        for name, mid in pool.items():
            if name in existing:
                continue
            body = {
                "model_name": name,
                "litellm_params": {
                    "model": f"openrouter/{mid}",
                    # The DB model needs the resolved key value — `os.environ/...` is stored
                    # verbatim (not resolved) for API-created models, so pass the real key.
                    "api_key": or_key,
                    "extra_body": {"usage": {"include": True}},
                },
                "model_info": {"jarvis_pool": True},
            }
            httpx.post(f"{GATEWAY}/model/new", headers=headers, json=body, timeout=15).raise_for_status()
            print(f"  + pool model {name} -> openrouter/{mid}")
        token = env.get(KEY_ENV.get(project, ""), "")
        if token:
            allow = sorted(set(SHARED_MODELS) | set(pool.keys()))
            httpx.post(f"{GATEWAY}/key/update", headers=headers,
                       json={"key": token, "models": allow}, timeout=10)
            print(f"  {project} key allowlist: {allow}")
        else:
            print(f"  ! no key for {project} in platform/.env — pool created, allowlist not set")
    except Exception as e:  # noqa: BLE001
        print(f"  ! model sync failed for {project}: {e}")
        return 1
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    args = ap.parse_args()
    sys.exit(sync(args.project))
