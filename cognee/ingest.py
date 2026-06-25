"""Jarvis knowledge ingestion CLI — runs in the Cognee venv, driven by the dashboard.

    python ingest.py --project msme --job /tmp/job.json

The job JSON: {"text": "...", "files": ["/path/a.txt", ...], "tags": ["earnings","q3"]}

Pipeline: configure(project) -> cognee.add(..., node_set=tags) -> cognee.cognify(...).
Ontology (all three formats) is read from <data>/<project>/ontology.json:
  - entityTypes / relationTypes  -> compiled to an OWL file with rdflib
  - owlFile                      -> a user-uploaded OWL/RDF file
Both are handed to Cognee's RDFLibOntologyResolver (fuzzy matching) so extraction
aligns to the schema. Tags ride along as node_sets. Progress prints to stdout so the
dashboard can tail it as a job log.
"""
import argparse
import asyncio
import json
import pathlib
import re
import sys
from collections import defaultdict

import jarvis_cognee as jc
import preprocess
import routing
import linker
import cognee

BASE = pathlib.Path(__file__).resolve().parent

# Files we can read as text for preprocessing. Anything else (PDF, docx, …) is handed
# straight to Cognee's loaders unchanged and routed on doc_type/tags only.
_TEXT_EXTS = {".txt", ".md", ".markdown", ".text", ".log", ".csv", ".tsv", ".json"}
_PREPROCESS_MAX_CHARS = 40_000  # above this, skip single-pass preprocessing (output cap risk)

_DEFAULT_PIPELINE = {
    "preprocessing": {"enabled": False, "steps": []},
    "cognify": {"defaultModel": "extractor", "routing": {"enabled": False, "rules": []}},
}


def _say(msg: str):
    print(msg, flush=True)


def _pipeline_cfg(project: str) -> dict:
    """Read <data>/<project>/pipeline.json; fall back to a safe pass-through default."""
    p = BASE / "data" / project / "pipeline.json"
    if p.exists():
        try:
            cfg = json.loads(p.read_text())
            cfg.setdefault("preprocessing", dict(_DEFAULT_PIPELINE["preprocessing"]))
            cfg.setdefault("cognify", dict(_DEFAULT_PIPELINE["cognify"]))
            return cfg
        except Exception as e:  # noqa: BLE001
            _say(f"  pipeline.json unreadable ({e}); using defaults")
    return json.loads(json.dumps(_DEFAULT_PIPELINE))


def _models_cfg(project: str) -> dict:
    """Read <data>/<project>/models.json -> {role: openrouter_id}; fall back to code defaults."""
    p = BASE / "data" / project / "models.json"
    if p.exists():
        try:
            roles = (json.loads(p.read_text()) or {}).get("roles") or {}
            if isinstance(roles, dict):
                return {**routing.DEFAULT_ROLE_MODELS, **roles}
        except Exception as e:  # noqa: BLE001
            _say(f"  models.json unreadable ({e}); using default model map")
    return dict(routing.DEFAULT_ROLE_MODELS)


def _resolve_step_models(steps: list, models_map: dict) -> list:
    """Replace each step's `model` (a role handle) with the gateway pool model_name to call."""
    out = []
    for s in steps or []:
        s2 = dict(s)
        s2["model"] = routing.gateway_model(s.get("model") or "preprocess", models_map)
        out.append(s2)
    return out


def _cognee_model(alias: str) -> str:
    """Cognee's custom adapter expects an 'openai/<alias>' model id; pass-through if prefixed."""
    return alias if "/" in alias else f"openai/{alias}"


def _is_textfile(path: str) -> bool:
    return pathlib.Path(path).suffix.lower() in _TEXT_EXTS


def _read_text(path: str) -> str:
    try:
        return pathlib.Path(path).read_text(errors="replace")
    except Exception:  # noqa: BLE001
        return ""


def _est_tokens(s: str) -> int:
    return max(1, len(s or "") // 4)


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", (s or "").strip()) or "x"


def _build_types_owl(cfg: dict, out: pathlib.Path) -> bool:
    """Compile the custom entity/relation types into an OWL/RDF-XML file."""
    ents = cfg.get("entityTypes") or []
    rels = cfg.get("relationTypes") or []
    if not ents and not rels:
        return False
    from rdflib import Graph, Namespace, RDF, RDFS, OWL, Literal

    g = Graph()
    EX = Namespace("http://jarvis.local/onto#")
    g.bind("ex", EX)
    g.bind("owl", OWL)
    for t in ents:
        name = t if isinstance(t, str) else t.get("name", "")
        if not name:
            continue
        c = EX[_slug(name)]
        g.add((c, RDF.type, OWL.Class))
        g.add((c, RDFS.label, Literal(name)))
    for r in rels:
        name = r if isinstance(r, str) else r.get("name", "")
        if not name:
            continue
        p = EX[_slug(name)]
        g.add((p, RDF.type, OWL.ObjectProperty))
        g.add((p, RDFS.label, Literal(name)))
        if isinstance(r, dict):
            if r.get("source"):
                g.add((p, RDFS.domain, EX[_slug(r["source"])]))
            if r.get("target"):
                g.add((p, RDFS.range, EX[_slug(r["target"])]))
    g.serialize(destination=str(out), format="xml")
    return True


def _resolver(project: str):
    """Build a Cognee ontology resolver from the project's saved ontology.json, or None."""
    ddir = BASE / "data" / project
    cfg_path = ddir / "ontology.json"
    if not cfg_path.exists():
        return None
    try:
        cfg = json.loads(cfg_path.read_text())
    except Exception:  # noqa: BLE001
        return None
    files = []
    types_owl = ddir / "ontology_types.owl"
    if _build_types_owl(cfg, types_owl):
        files.append(str(types_owl))
        _say(f"  ontology: compiled {len(cfg.get('entityTypes',[]))} types / "
             f"{len(cfg.get('relationTypes',[]))} relations -> OWL")
    if cfg.get("owlFile"):
        owl = ddir / cfg["owlFile"]
        if owl.exists():
            files.append(str(owl))
            _say(f"  ontology: + uploaded {cfg['owlFile']}")
    if not files:
        return None
    from cognee.modules.ontology.rdf_xml.RDFLibOntologyResolver import RDFLibOntologyResolver
    from cognee.modules.ontology.matching_strategies import FuzzyMatchingStrategy

    return RDFLibOntologyResolver(
        ontology_file=files if len(files) > 1 else files[0],
        matching_strategy=FuzzyMatchingStrategy(),
    )


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--job", required=True)
    args = ap.parse_args()
    project = args.project

    job = json.loads(pathlib.Path(args.job).read_text())
    text = (job.get("text") or "").strip()
    files = job.get("files") or []
    tags = [t for t in (job.get("tags") or []) if t]
    doc_type = (job.get("doc_type") or "").strip() or None
    # Timeseries linkage: explicit as_of/tickers from the job; tickers also auto-resolved from
    # the doc text via the curated symbols.json (never guessed). Stamped into the graph as
    # namespaced node_set tags so the backtester can join doc events to price history.
    as_of = linker.normalize_asof(job.get("as_of"))
    explicit_tickers = [t for t in (job.get("tickers") or []) if t]
    do_preprocess = job.get("preprocess", True)
    # Manual extractor override (e.g. "openai/extractor-pro"): wins over routing for the
    # whole job. Empty / "auto" defers to the configured cognify strategy.
    model_override = (job.get("model") or "").strip()
    if model_override.lower() == "auto":
        model_override = ""

    _say(f"configuring Cognee for project '{project}'…")
    jc.configure(project)
    key = jc.project_key(project)

    pipe = _pipeline_cfg(project)
    models_map = _models_cfg(project)  # role -> OpenRouter id (per project, with code defaults)
    pp = pipe.get("preprocessing") or {}
    steps = pp.get("steps") or []
    steps_resolved = _resolve_step_models(steps, models_map)  # step.model role -> gateway model
    pp_on = bool(do_preprocess and pp.get("enabled") and steps)
    cognify_cfg = pipe.get("cognify") or {}
    if pp_on:
        _say(f"pre-processing on: {len([s for s in steps if s.get('enabled', True)])} step(s)"
             + (f", doc_type={doc_type}" if doc_type else ""))

    # --- Resolve ticker(s) + as_of BEFORE preprocessing, so feature-engine steps (e.g. price
    #     relevance) have their timeseries context. Resolve from the raw inputs (job text + any
    #     readable text files). Tickers/as_of also become provenance node_set tags at cognify. ---
    symbols = linker.load_symbols(project)
    _resolve_parts = [text] + [_read_text(f) for f in files if _is_textfile(f)]
    raw_blob = " ".join(p for p in _resolve_parts if p)
    tickers = linker.resolve_tickers(raw_blob, symbols, explicit=explicit_tickers)
    benchmarks = {t: linker.benchmark_for(t, symbols) for t in tickers}
    link_ctx = {"tickers": tickers, "as_of": as_of, "benchmarks": benchmarks}
    prov_tags = linker.provenance_tags(tickers, as_of)  # ['ticker:INFY.NS', 'asof:2024-06-14']
    node_set = (tags + prov_tags) or None
    _say(f"linked: tickers={tickers or '—'} as_of={as_of or '—'}")

    # --- Preprocess each item into cleaned prose (+meta), or drop it. ---
    prepared: list[tuple[str, dict]] = []   # (text, meta)
    raw_files: list[str] = []               # binary/unknown files → straight to Cognee
    dropped = 0
    preprocess_tokens = 0

    def _prep(raw: str, dtype):
        nonlocal dropped, preprocess_tokens
        if not pp_on:
            return raw, {"docType": dtype}
        preprocess_tokens += _est_tokens(raw) * 2  # in + out, rough
        out = preprocess.run_steps(steps_resolved, raw, key, doc_type=dtype, tags=tags,
                                   link=link_ctx)
        for t in out.get("trace", []):
            flag = "DROP" if t.get("dropped") else (t.get("error") or "ok")
            extra = f" meta={t['meta']}" if t.get("meta") else ""
            _say(f"    · {t['step']}: {flag}{extra}")
        if out.get("text") is None:
            dropped += 1
            return None, None
        return out["text"], out["meta"]

    if text:
        t, m = _prep(text, doc_type)
        if t is not None:
            prepared.append((t, m))

    for f in files:
        if pp_on and _is_textfile(f):
            ftext = _read_text(f)
            if ftext and len(ftext) <= _PREPROCESS_MAX_CHARS:
                t, m = _prep(ftext, doc_type)
                if t is not None:
                    prepared.append((t, m))
                continue
            if ftext:
                _say(f"  {pathlib.Path(f).name}: too large for single-pass preprocess "
                     f"({len(ftext)} chars) — ingesting raw")
        raw_files.append(f)  # Cognee's loaders handle file paths (txt, md, pdf, …)

    # --- Route each item to an extractor model and bucket by it. ---
    buckets: dict[str, list] = defaultdict(list)

    def _route(itext: str, meta: dict | None) -> str:
        # A manual pick or a routing decision both yield a role; resolve it to the project's
        # gateway pool model, then prefix for Cognee's OpenAI-compatible adapter.
        if model_override:
            role = model_override
        else:
            sig = routing.signals_for(itext, meta, tags)
            role = routing.pick_model(sig, cognify_cfg)
        return _cognee_model(routing.gateway_model(role, models_map))

    for itext, meta in prepared:
        buckets[_route(itext, meta)].append(itext)
    for f in raw_files:
        buckets[_route("", {"docType": doc_type})].append(f)

    total = sum(len(v) for v in buckets.values())
    _say(f"prepared {total} item(s)" + (f", dropped {dropped} by gate" if dropped else "")
         + (f", tags {tags}" if tags else ""))
    if total == 0:
        _say("nothing to cognify" + (" (all items gated out)" if dropped else " (no input)"))
        _say("INGEST_DONE")
        return

    # --- Cognify: per-bucket add + cognify with the routed model. ---
    resolver = _resolver(project)
    cog_kwargs = {"datasets": [project]}
    if resolver is not None:
        cog_kwargs["config"] = {"ontology_config": {"ontology_resolver": resolver}}
        _say("cognifying with ontology — this can take a few minutes…")
    else:
        _say("cognifying (no ontology set) — this can take a few minutes…")

    cognify_tokens = sum(_est_tokens(t) for t, _ in prepared)  # cognify input estimate (text items)
    for model_alias, items in buckets.items():
        _say(f"  → {model_alias}: {len(items)} item(s)")
        # cognify is incremental (processes only un-cognified data), so adding a bucket and
        # cognifying it with its model, then the next, gives per-document model routing.
        await cognee.add(items, dataset_name=project, node_set=node_set)
        cognee.config.set_llm_model(model_alias)
        await cognee.cognify(**cog_kwargs)

    _say(f"~tokens: preprocess≈{preprocess_tokens}, cognify inputs≈{cognify_tokens} "
         f"(buckets: {', '.join(f'{m}×{len(v)}' for m, v in buckets.items())})")
    _say("INGEST_DONE")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:  # noqa: BLE001
        _say(f"INGEST_ERROR: {e}")
        sys.exit(1)
