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

import jarvis_cognee as jc
import cognee

BASE = pathlib.Path(__file__).resolve().parent


def _say(msg: str):
    print(msg, flush=True)


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

    job = json.loads(pathlib.Path(args.job).read_text())
    text = (job.get("text") or "").strip()
    files = job.get("files") or []
    tags = [t for t in (job.get("tags") or []) if t]
    model = (job.get("model") or "").strip()  # e.g. "openai/reasoner" for high-value docs

    _say(f"configuring Cognee for project '{args.project}'…")
    jc.configure(args.project)

    # Extraction quality is baked into the graph permanently, so high-value documents
    # can opt into a stronger extractor. Default (no model) keeps the cheap gpt-4o-mini
    # set by jc.configure(). The "openai/" prefix tells litellm the gateway is OpenAI-compatible.
    if model:
        cognee.config.set_llm_model(model)
        _say(f"  extractor model: {model}")

    items = []
    if text:
        items.append(text)
    for f in files:
        items.append(f)  # Cognee's loaders handle file paths (txt, md, pdf, …)
    if not items:
        _say("nothing to ingest (no text or files)")
        return
    _say(f"adding {len(items)} item(s)" + (f" with tags {tags}" if tags else "") + " …")
    await cognee.add(items, dataset_name=args.project, node_set=tags or None)

    resolver = _resolver(args.project)
    kwargs = {"datasets": [args.project]}
    if resolver is not None:
        kwargs["config"] = {"ontology_config": {"ontology_resolver": resolver}}
        _say("cognifying with ontology — this can take a few minutes…")
    else:
        _say("cognifying (no ontology set) — this can take a few minutes…")
    await cognee.cognify(**kwargs)
    _say("INGEST_DONE")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:  # noqa: BLE001
        _say(f"INGEST_ERROR: {e}")
        sys.exit(1)
