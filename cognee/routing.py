"""Jarvis cognify model routing + per-project model resolution.

Pure, dependency-free, unit-testable. Two concerns:

1. **Routing** — given a document's signals and the project's `cognify` config (from
   `pipeline.json`), decide which **role** drives cognify. This is the budget lever: keep
   bulk on cheap Flash (`extractor`), escalate only high-signal docs to `extractor-pro`.
2. **Resolution** — a role (`extractor`, `preprocess`, …) is a stable, portable handle.
   The project's `models.json` maps each role to a real **OpenRouter model id**; that id is
   served at the gateway by a deduped DB-pool model named `or--<slug>`. `gateway_model()`
   turns a role into the gateway `model_name` to actually call. Roles stay in `pipeline.json`
   so config is portable; the per-project mapping (and the real model name) lives in
   `models.json`.

Routing config shape:

    "cognify": {
      "defaultModel": "extractor",
      "routing": {
        "enabled": true,
        "rules": [
          { "if": { "docType": ["concall","quarterly","brokerage"] }, "model": "extractor-pro" },
          { "if": { "pattern": "earnings|guidance|acquisition|merger|rating" }, "model": "extractor-pro" },
          { "if": { "relevance": ["high"] }, "model": "extractor-pro" }
        ]
      }
    }

A rule matches when **every** condition in its `if` holds (AND). Supported conditions:
`docType` (in list), `tag` (any overlap), `pattern` (regex over text), `relevance` (in list,
typically set by a classify preprocessing step), `signal` (a `{metaKey: [values]}` map matching
ANY preprocess meta signal — e.g. `{"pricemove": ["high"]}` from the price-relevance feature
step), `minLength` / `maxLength` (chars). An empty `if` never matches — use `defaultModel` for
the catch-all. First matching rule wins.
"""
from __future__ import annotations

import re

# Default role -> OpenRouter model id, shipped in code so a fresh device (or a project with
# no models.json yet) still resolves. The dashboard Models page edits these per project; the
# values are OpenRouter-compatible ids (vendor/model). `embed` is deliberately absent — the
# embedding model is global and must never change per project (vector dims must stay stable).
DEFAULT_ROLE_MODELS = {
    "preprocess": "deepseek/deepseek-v4-flash",
    "extractor": "deepseek/deepseek-v4-flash",
    "extractor-pro": "deepseek/deepseek-v4-pro",
    "reasoner": "anthropic/claude-sonnet-4.6",
}


def model_slug(openrouter_id: str) -> str:
    """A filesystem/identifier-safe slug for an OpenRouter id (vendor kept to avoid clashes)."""
    s = re.sub(r"[^a-z0-9]+", "-", (openrouter_id or "").lower()).strip("-")
    return s or "model"


def pool_model_name(openrouter_id: str) -> str:
    """Gateway `model_name` of the deduped DB-pool model that backs an OpenRouter id."""
    return "or--" + model_slug(openrouter_id)


def resolve_role(role: str, roles_map: dict | None) -> str:
    """Role handle -> OpenRouter model id (project map, then code defaults, then the role itself)."""
    return (roles_map or {}).get(role) or DEFAULT_ROLE_MODELS.get(role) or role


def gateway_model(role: str, roles_map: dict | None) -> str:
    """Role handle -> the gateway `model_name` to call (the pool model for its OpenRouter id)."""
    return pool_model_name(resolve_role(role, roles_map))


def _as_list(v) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def signals_for(text: str, meta: dict | None, tags: list | None) -> dict:
    """Build the signal dict the router evaluates, from an item's text + preprocessing meta.

    `meta` is passed through whole so a rule's generic `signal` condition can match ANY
    preprocess output (e.g. a `pricemove` feature signal), not just docType/relevance."""
    meta = meta or {}
    return {
        "docType": meta.get("docType"),
        "relevance": meta.get("relevance"),
        "tags": list(tags or []),
        "text": text or "",
        "meta": {k: v for k, v in meta.items() if not str(k).startswith("_")},
    }


def _rule_matches(cond: dict, signals: dict) -> bool:
    if not cond:
        return False
    doc_type = signals.get("docType")
    tags = [str(t).lower() for t in (signals.get("tags") or [])]
    text = signals.get("text") or ""
    relevance = signals.get("relevance")
    n = len(text)

    if "docType" in cond:
        if doc_type is None or doc_type not in _as_list(cond["docType"]):
            return False
    if "tag" in cond:
        want = [str(t).lower() for t in _as_list(cond["tag"])]
        if not any(t in tags for t in want):
            return False
    if "relevance" in cond:
        if relevance is None or relevance not in _as_list(cond["relevance"]):
            return False
    if "signal" in cond:
        # Generic: match arbitrary preprocess meta signals, e.g. {"signal": {"pricemove": ["high"]}}.
        meta = signals.get("meta") or {}
        for skey, allowed in (cond["signal"] or {}).items():
            if meta.get(skey) not in _as_list(allowed):
                return False
    if cond.get("pattern"):
        try:
            if not re.search(cond["pattern"], text, re.IGNORECASE):
                return False
        except re.error:
            return False
    if "minLength" in cond and n < int(cond["minLength"]):
        return False
    if "maxLength" in cond and n > int(cond["maxLength"]):
        return False
    return True


def pick_model(signals: dict, cognify_cfg: dict | None) -> str:
    """Return the extractor **role** for a document, per the cognify routing config.

    The returned role is resolved to a gateway model by `gateway_model()`."""
    cfg = cognify_cfg or {}
    default = cfg.get("defaultModel") or "extractor"
    routing = cfg.get("routing") or {}
    if not routing.get("enabled", True):
        return default
    for rule in routing.get("rules") or []:
        if _rule_matches(rule.get("if") or {}, signals):
            return rule.get("model") or default
    return default
