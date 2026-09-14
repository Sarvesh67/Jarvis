"""Query router — natural language in, whitelisted Cypher out (spec §16, item 17).

The model NEVER writes Cypher. It may only choose one of the twelve reviewed templates
in query_templates.yaml and fill that template's declared parameters. Everything else
falls through to cited document search over the Stage 7 index.

That restriction is the whole point. The hard rules — tier 4 never answers a compliance
question, an EU rule never answers an India question — are properties of the Cypher, and
Cypher a model wrote at runtime cannot be reviewed. Written once and whitelisted, it can.

Parameter values are resolved DETERMINISTICALLY against the graph's own vocabularies
(category codes, cluster ids, INCI names), not by the model, so "Baddi" becomes HP_BBN
and "face cream" becomes SKIN_CARE by lookup rather than by generation.

    python -m pipeline.router "COS-8 licensed skin care manufacturers in Baddi"
    python -m pipeline.router --explain "who supplies niacinamide"
    python -m pipeline.router --list
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml
from rapidfuzz import fuzz, process

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib  # noqa: E402

TEMPLATES = {t["id"]: t for t in yaml.safe_load(
    (Path(__file__).resolve().parent / "query_templates.yaml").read_text())}

ROUTER_MODEL = "fast"
TIER_LABEL = {1: "regulator", 2: "company-declared", 3: "observed", 4: "inferred"}


# --------------------------------------------------------------------- vocabularies
def _vocab(session) -> dict[str, dict[str, str]]:
    v: dict[str, dict[str, str]] = {"category_code": {}, "cluster_id": {},
                                    "inci_name": {}, "packaging_type": {}, "state": {},
                                    "district": {}}
    # People say "Gujarat" far more often than "Ahmedabad-Vapi-Ankleshwar". A state is not
    # a cluster (Maharashtra holds two), so it is its own parameter rather than a cluster
    # alias — otherwise a state question silently resolves to one arbitrary cluster.
    for r in session.run("MATCH (c:Company) WHERE c.state IS NOT NULL "
                         "RETURN DISTINCT c.state AS s"):
        if r["s"]:
            v["state"][r["s"].strip().upper()] = r["s"]
    for r in session.run("MATCH (c:ProductCategory) RETURN c.code AS code, c.name AS name"):
        v["category_code"][(r["name"] or r["code"]).upper()] = r["code"]
        v["category_code"][r["code"].replace("_", " ")] = r["code"]
    # Buyers do not speak the regulator's vocabulary. The Fourth Schedule calls it
    # "Skin care"; a customer asks for a "face serum". category_map.yaml already holds
    # that mapping (spec §7.5 — retail strings are TAGS, never spine codes), and the
    # router was resolving against the spine names alone, so every real-world phrasing
    # came back unresolved and the query silently answered for all categories.
    try:
        for term, m in (lib.load_yaml("category_map.yaml").get("mappings") or {}).items():
            code = (m or {}).get("code")
            if code:
                v["category_code"].setdefault(term.strip().upper(), code)
    except Exception:
        pass
    # District names come from the LIVE GRAPH, not from cluster_map.yaml's hand-written
    # list. normalize.py also assigns a cluster by PINCODE PREFIX, so a district can be
    # correctly clustered in the data while never appearing in that list — 22 districts
    # and 3,777 companies were in exactly that state, and asking about Navsari or Daman
    # by name resolved to nothing at all. Deriving the vocabulary from what was actually
    # loaded fixes every such district at once, including spelling variants the map
    # missed ("gandhi nagar" vs "gandhinagar", "mahesana" vs "mehsana").
    for r in session.run("""
            MATCH (c:Company)
            WHERE c.district IS NOT NULL AND c.cluster_id IS NOT NULL
            WITH toUpper(c.district) AS d, c.cluster_id AS cid, count(*) AS n
            ORDER BY d, n DESC
            WITH d, collect(cid)[0] AS cid
            RETURN d, cid"""):
        v["district"][r["d"]] = r["d"]
        v["cluster_id"].setdefault(r["d"], r["cid"])
    for r in session.run("MATCH (c:Cluster) RETURN c.cluster_id AS id, c.name AS name, "
                         "c.districts AS districts"):
        v["cluster_id"][(r["name"] or "").upper()] = r["id"]
        for d in (r["districts"] or []):
            v["cluster_id"][d.upper()] = r["id"]
        for part in re.split(r"[-–]", r["name"] or ""):
            if len(part.strip()) > 3:
                v["cluster_id"][part.strip().upper()] = r["id"]
    for r in session.run("MATCH (p:PackagingComponent) RETURN p.type AS t"):
        v["packaging_type"][r["t"].replace("_", " ").upper()] = r["t"]
    # buyers ask for the object, not the NIC-derived type name
    for word, t in {"BOTTLE": "plastic_container", "BOTTLES": "plastic_container",
                    "JAR": "plastic_container", "JARS": "plastic_container",
                    "CONTAINER": "plastic_container", "CLOSURE": "plastic_container",
                    "CAP": "plastic_container", "PUMP": "plastic_container",
                    "TUBE": "metal_formed", "TUBES": "metal_formed",
                    "AEROSOL": "metal_formed", "CAN": "metal_formed",
                    "GLASS": "glass_container", "GLASS BOTTLE": "glass_container",
                    "CARTON": "carton", "BOX": "carton", "BOXES": "carton",
                    "CORRUGATED": "corrugated_box",
                    "LAMINATE": "plastic_film", "FILM": "plastic_film",
                    "POUCH": "plastic_film", "FOIL": "plastic_film",
                    "LABEL": "paper_container"}.items():
        v["packaging_type"].setdefault(word, t)
    return v


def resolve(kind: str, raw: str | None, vocab: dict, session) -> tuple[str | None, str | None]:
    """Free text -> a value that exists in the graph. Returns (value, how_it_resolved)."""
    if not raw:
        return None, None
    q = raw.strip().upper()
    if kind == "inci_name":
        rec = session.run("MATCH (i:Ingredient) WHERE i.inci_name = $q RETURN i.inci_name AS n",
                          q=q).single()
        if rec:
            return rec["n"], "exact"
        rows = [r["n"] for r in session.run(
            "MATCH (i:Ingredient) WHERE i.inci_name STARTS WITH $p "
            "RETURN i.inci_name AS n LIMIT 200", p=q.split()[0][:6])]
        m = process.extractOne(q, rows, scorer=fuzz.token_sort_ratio) if rows else None
        return (m[0], f"fuzzy {m[1]:.0f}") if m and m[1] >= 88 else (None, "unresolved")
    if kind == "company_name":
        return raw.strip(), "literal"
    if kind == "district":
        table = vocab.get("district", {})
        if q in table:
            return table[q], "exact"
        m = process.extractOne(q, list(table), scorer=fuzz.ratio) if table else None
        return (table[m[0]], f"fuzzy {m[1]:.0f}") if m and m[1] >= 88 else (None, "unresolved")
    if kind == "state":
        table = vocab.get("state", {})
        if q in table:
            return table[q], "exact"
        m = process.extractOne(q, list(table), scorer=fuzz.ratio) if table else None
        return (table[m[0]], f"fuzzy {m[1]:.0f}") if m and m[1] >= 88 else (None, "unresolved")
    table = vocab.get(kind, {})
    if q in table:
        return table[q], "exact"
    m = process.extractOne(q, list(table), scorer=fuzz.partial_ratio) if table else None
    return (table[m[0]], f"fuzzy {m[1]:.0f}") if m and m[1] >= 85 else (None, "unresolved")


# --------------------------------------------------------------------- routing
def route_with_model(question: str) -> dict | None:
    catalogue = [{"id": t["id"], "intent": t["intent"],
                  "params": list((t.get("params") or {}).keys()),
                  "examples": t.get("examples", [])[:2]} for t in TEMPLATES.values()]
    prompt = (
        "You route a question to ONE query template. You may not write a database query.\n"
        "Reply with JSON only: {\"template\": \"<id>\", \"params\": {...}, "
        "\"confidence\": 0.0-1.0}\n"
        "Use only parameter names the chosen template declares. Copy parameter values "
        "verbatim from the question; do not translate them into codes. If nothing fits, "
        "reply {\"template\": null}.\n\n"
        f"TEMPLATES:\n{json.dumps(catalogue, indent=1)}\n\nQUESTION: {question}"
    )
    key = lib.ENV.get("LITELLM_MASTER_KEY", "")
    import httpx
    try:
        with httpx.Client(timeout=90.0) as c:
            r = c.post("http://127.0.0.1:4000/v1/chat/completions",
                       headers={"Authorization": f"Bearer {key}"},
                       json={"model": ROUTER_MODEL, "temperature": 0,
                             "messages": [{"role": "user", "content": prompt}]})
            r.raise_for_status()
            txt = r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"  (router model unavailable: {e}; falling back to keyword routing)")
        return None
    m = re.search(r"\{.*\}", txt, re.S)
    return json.loads(m.group(0)) if m else None


# Below this, the best template match is not a match at all. Without a floor the keyword
# router returns its least-bad guess for ANY input, so "what is the general vibe of the
# industry" was routed to the EU-restricted-ingredients template and answered as though a
# regulatory question had been asked. A weak match must fall through to graph context.
ROUTE_FLOOR = 0.58


# A question that asks whether something is ALLOWED is a compliance question, whatever
# else it mentions. "Can I use mercury in a face cream in India" scored highest against
# `third_party_manufacturers` on the keyword path — "can I", "face cream", "make my
# product" — and a regulatory question was answered with a list of contract manufacturers.
# The model does not make this mistake; the fallback did, silently, and the fallback is
# what runs when the gateway is down mid-demo.
#
# So the flow is decided before the template is. These markers only ever NARROW the
# candidate set, and a question with no marker considers everything as before.
PERMISSION_MARKERS = re.compile(
    r"\b(allowed|permitted|prohibit\w*|banned?|restrict\w*|legal|illegal|"
    r"comply|complian\w*|limit|max(?:imum)?\s+concentration|"
    r"standard|regulation|rules?)\b"
    # "can I use X" carries no permission WORD and is the commonest way the question is
    # actually typed — it is what sent mercury to the contract-manufacturer template.
    r"|\b(can|may|am\s+i\s+allowed\s+to|is\s+it\s+(?:ok|okay|safe))\b[^.?]{0,40}"
    r"\b(use|add|put|include|formulate)\b", re.I)


def _named_ingredient(question: str, session) -> str | None:
    """An INCI name that appears verbatim in the question, longest match first.

    Looked up in the graph rather than guessed, for the same reason every other
    parameter is: the vocabulary is the graph's, not the model's.
    """
    if session is None:
        return None
    # "CREAM", "MILK", "TALC" and "SOAP" are real INCI names and also ordinary English —
    # decision 0011 already refuses to read them as ingredient mentions in a document,
    # and the same list applies here. Without it "what standard do I need for a face
    # CREAM" reads as a question about an ingredient.
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "adapters"))
    from s14b_supplies import AMBIGUOUS
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", question.upper())
    grams = [" ".join(words[i:i + n])
             for n in (3, 2, 1) for i in range(len(words) - n + 1)]
    if not grams:
        return None
    grams = [g for g in grams if g not in AMBIGUOUS]
    if not grams:
        return None
    rec = session.run(
        "MATCH (i:Ingredient) WHERE i.inci_name IN $g "
        "RETURN i.inci_name AS n ORDER BY size(i.inci_name) DESC LIMIT 1", g=grams).single()
    return rec["n"] if rec else None


def route_by_keyword(question: str, session=None) -> dict:
    """Deterministic fallback so the router still works with no model available."""
    q = question.lower()
    candidates = list(TEMPLATES.values())
    if PERMISSION_MARKERS.search(question):
        compliance = [t for t in candidates if t.get("flow") == "compliance"]
        if compliance:
            candidates = compliance
        # Narrowing to compliance is not enough on its own: "can I use mercury in a FACE
        # CREAM" still scored highest against "where can I test my face cream", because
        # token overlap cannot tell a question about a substance from a question about a
        # product. If the question names an ingredient the graph knows, the question is
        # about that ingredient, and the only remaining choice is which jurisdiction.
        # "which standard applies to X" asks about a CATEGORY even when X is also an
        # ingredient name, so the standard template keeps that question.
        asks_standard = re.search(r"\b(standard|bis|\bis\s*\d|specification)\b",
                                  question, re.I)
        ing = None if asks_standard else _named_ingredient(question, session)
        if ing:
            eu = re.search(r"\b(eu|european|europe|ec\s*1223)\b", question, re.I)
            tid = "ingredient_restriction_eu" if eu else "ingredient_restriction_india"
            return {"template": tid, "params": {"ingredient": ing}, "confidence": 0.9}
    best, score = None, 0
    for t in candidates:
        hay = " ".join([t["intent"]] + t.get("examples", [])).lower()
        sc = fuzz.token_set_ratio(q, hay)
        if sc > score:
            best, score = t["id"], sc
    conf = round(score / 100, 2)
    return {"template": best if conf >= ROUTE_FLOOR else None, "params": {},
            "confidence": conf}


def extract_params(question: str, template: dict, given: dict) -> dict:
    """Fill declared params the model left empty, from the question text."""
    out = dict(given or {})
    for name in (template.get("params") or {}):
        if out.get(name):
            continue
        m = re.search(r"\b(?:in|at|near|from)\s+([A-Z][\w .&-]{2,40})", question)
        if name in ("cluster",) and m:
            out[name] = m.group(1).strip()
    return out


# --------------------------------------------------------------------- execution
def answer(question: str, *, explain: bool = False, use_model: bool = True,
           template: str | None = None, params: dict | None = None,
           limit: int = 50, mode: str = "auto", hops: int = 2,
           include_derived: bool = False) -> dict:
    """`template` short-circuits routing.

    An agent calling this is already an LLM and already knows what it is asking, so
    making it pay for the router's own model call is a wasted hop and a second place for
    the intent to drift. It names the template and the parameters; the whitelist still
    applies, and parameter values are still resolved against the graph's vocabularies
    rather than trusted verbatim.
    """
    drv = lib.neo4j_driver()
    with drv.session() as s:
        if mode == "context":
            from pipeline.context import from_question
            out = from_question(s, question, hops=hops, include_derived=include_derived)
            drv.close()
            return out
        if template:
            if template not in TEMPLATES:
                drv.close()
                return {"question": question, "template": None,
                        "error": f"unknown template {template!r}",
                        "available": sorted(TEMPLATES)}
            decision = {"template": template, "params": params or {}, "confidence": 1.0}
        else:
            decision = (route_with_model(question) if use_model else None) or \
                route_by_keyword(question, s)
            # respect a model that says it is unsure, rather than running a template it
            # only half-believes in
            if (decision.get("confidence") or 0) < ROUTE_FLOOR and decision.get("template"):
                decision = {**decision, "template": None,
                            "low_confidence": decision.get("template")}
        tid = decision.get("template")
        if not tid or tid not in TEMPLATES:
            # No reviewed query fits. Fall through to GRAPH context rather than the
            # document-only search: the graph is where companies, licences, labs and
            # their edges live, and a question we did not anticipate is exactly the one
            # that needs them.
            from pipeline.context import from_question
            out = from_question(s, question, hops=hops, include_derived=include_derived)
            out["note"] = ("No whitelisted template matched, so this is retrieved context "
                           "rather than a reviewed answer. Reason over it and say so.")
            drv.close()
            return out
        t = TEMPLATES[tid]
        vocab = _vocab(s)
        raw_params = extract_params(question, t, decision.get("params"))
        params, how = {}, {}
        for name, kind in (t.get("params") or {}).items():
            val, note = resolve(kind, raw_params.get(name), vocab, s)
            params[name] = val
            how[name] = f"{raw_params.get(name)!r} -> {val!r} ({note})"
        # every template is bounded; the CALLER chooses the bound rather than
        # discovering a hidden one after the fact
        rows = [dict(r) for r in s.run(t["cypher"], limit=limit, **params)]
        result = {
            "answered_by": "template",
            "question": question, "template": tid, "intent": t["intent"],
            "flow": t.get("flow"), "tier_floor": t["tier_floor"],
            "tier_meaning": TIER_LABEL[t["tier_floor"]],
            "params": params, "param_resolution": how,
            "rows": rows, "note": t.get("note"), "limit": limit,
            "confidence": decision.get("confidence"),
        }
        if t["tier_floor"] >= 4:
            result["warning"] = ("Tier 4 — derived, not observed. Ranks and suggests; "
                                 "it is not a compliance answer (hard rule 3).")
        if not rows:
            result["empty_reason"] = coverage_note(tid, s)
        if explain:
            result["cypher"] = t["cypher"].strip()
    drv.close()
    return result


def doc_fallback(s, question: str) -> dict:
    """No template fits: answer from the indexed corpus, with citations."""
    try:
        vec = lib.embed([question])[0]
    except Exception as e:
        return {"question": question, "template": None,
                "error": f"no template matched and embeddings unavailable ({e})"}
    rows = [dict(r) for r in s.run("""
        CALL db.index.vector.queryNodes('docchunk_embedding', 5, $v)
        YIELD node, score
        RETURN score, node.doc_title AS document, node.page AS page,
               node.section AS section, left(node.text, 400) AS passage
    """, v=vec)]
    return {"question": question, "template": None,
            "answered_by": "document search over the Stage 7 index",
            "rows": rows,
            "note": "No whitelisted template matched, so this is cited source text, "
                    "not a graph answer. Read the citation before relying on it."}


def coverage_note(tid: str, session=None) -> str:
    # The India ingredient layer is the Cosmetics Rules 2020 substance list and nothing
    # else — five substances. An empty answer there means "not on that list", which is a
    # very different statement from "not restricted in India", and the difference is the
    # whole product. Read the list from the graph rather than restating it here: a note
    # that names substances is exactly the kind of text that goes stale silently.
    if tid == "ingredient_restriction_india" and session is not None:
        subs = sorted({r["n"] for r in session.run(
            "MATCH (i:Ingredient)-[:RESTRICTED_BY]->(:RegulatoryRule {jurisdiction:'IN'}) "
            "RETURN DISTINCT i.inci_name AS n")})
        if subs:
            return (f"India's substance-level rules here are the Cosmetics Rules 2020 "
                    f"list only — {len(subs)} substances: {', '.join(subs)}. This "
                    f"ingredient is not on that list, which is NOT the same as it being "
                    f"permitted: the Rules also work through the Fourth Schedule "
                    f"categories and the BIS standards under the Ninth, and those are "
                    f"not substance-indexed. Check with CDSCO before relying on silence.")
    return {
        "manufacturers_for_category_in_cluster":
            "No COS-8/COS-9 licence data is loaded — state licensing registers are not "
            "published in bulk. Try `cosmetics_firms_in_cluster` for registry activity, "
            "which answers presence, not permission.",
        "suppliers_of_ingredient":
            "Supply claims come from supplier catalogues scraped so far; the crawl is "
            "still widening.",
        "substitutes_for_ingredient":
            "Substitutes are derived from shared CosIng functions; the ingredient may "
            "have no declared function.",
        "packaging_converters_in_cluster":
            "Packaging converters come from the Udyam registry, and the extract loaded "
            "covers GUJARAT ONLY. There is no coverage in Himachal, Uttarakhand or "
            "Maharashtra until a Udyam extract for those states is loaded — this is a "
            "missing-data gap, not an absence of converters.",
        "third_party_manufacturers":
            "Contract-manufacturing claims are read from company websites, and most "
            "cluster MSMEs have no website — so absence here is missing evidence, not "
            "absence of contract manufacturers. Ask `cosmetics_firms_in_cluster` for the "
            "firms the registry shows in that cluster.",
    }.get(tid, "No rows. This is a coverage gap, not an assertion that none exist.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="*")
    ap.add_argument("--explain", action="store_true", help="show the Cypher that ran")
    ap.add_argument("--no-model", action="store_true", help="keyword routing only")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=50, help="max rows the query returns")
    ap.add_argument("--mode", choices=["auto", "template", "context", "cypher"],
                    default="auto",
                    help="auto: template, else graph context. context: graph retrieval. "
                         "cypher: run --cypher through the guard.")
    ap.add_argument("--cypher", help="Cypher YOU wrote; validated then run read-only")
    ap.add_argument("--expand", nargs="*", metavar="REF",
                    help="steer a further hop from refs returned by context, "
                         "e.g. Cluster:HP_BBN Company:c_ab12")
    ap.add_argument("--edge-types", nargs="*", help="restrict --expand to these edge types")
    ap.add_argument("--hops", type=int, default=2)
    ap.add_argument("--include-derived", action="store_true",
                    help="include tier-4 derived edges in graph context")
    ap.add_argument("--schema", action="store_true",
                    help="print the live schema; read this before writing Cypher")
    ap.add_argument("--rows", type=int, default=10,
                    help="rows to print (0 = all). --json always returns every row.")
    ap.add_argument("--template", help="invoke a named template directly, skipping routing")
    ap.add_argument("--param", action="append", default=[], metavar="K=V",
                    help="parameter for --template; repeatable")
    a = ap.parse_args()

    if a.schema:
        from pipeline.schema_card import build, render
        drv = lib.neo4j_driver()
        with drv.session() as s:
            print(render(build(s)))
        drv.close()
        return 0

    if a.cypher:
        from pipeline.cypher_guard import run as guarded_run
        res = guarded_run(a.cypher, question=" ".join(a.question) or None)
        print(json.dumps(res, indent=2, default=str))
        return 0 if res.get("ok") else 1

    if a.expand:
        from pipeline.context import expand_from
        drv = lib.neo4j_driver()
        with drv.session() as s:
            print(json.dumps(expand_from(s, a.expand, edge_types=a.edge_types,
                                         hops=a.hops,
                                         include_derived=a.include_derived),
                             indent=2, default=str))
        drv.close()
        return 0

    if a.list or (not a.question and not a.template):
        print(f"  {len(TEMPLATES)} whitelisted templates:\n")
        for t in TEMPLATES.values():
            print(f"  {t['id']:42s} [{t.get('flow','-'):11s}] tier<={t['tier_floor']}")
            print(f"    {t['intent']}")
            if t.get("examples"):
                print(f"    e.g. \"{t['examples'][0]}\"")
        return 0

    given = {}
    for kv in a.param:
        k, _, v = kv.partition("=")
        if k:
            given[k.strip()] = v.strip() or None
    res = answer(" ".join(a.question), explain=a.explain, use_model=not a.no_model,
                 template=a.template, params=given or None, limit=a.limit,
                 mode=a.mode, hops=a.hops, include_derived=a.include_derived)
    if a.json or res.get("answered_by") in ("graph_context", "generated_cypher"):
        print(json.dumps(res, indent=2, default=str))
        return 0
    print(f"\n  Q: {res['question']}")
    if res.get("template"):
        print(f"  -> template `{res['template']}` ({res['intent']})")
        print(f"     evidence tier <= {res['tier_floor']} ({res['tier_meaning']})")
        for k, v in (res.get("param_resolution") or {}).items():
            print(f"     {k}: {v}")
    else:
        print(f"  -> {res.get('answered_by', res.get('error'))}")
    if res.get("warning"):
        print(f"  ! {res['warning']}")
    if res.get("note"):
        print(f"  note: {res['note'].strip()}")
    rows = res.get("rows") or []
    shown = len(rows) if a.rows <= 0 else min(a.rows, len(rows))
    print(f"\n  {len(rows)} row(s)" + (f", showing {shown}" if shown < len(rows) else ""))
    for r in rows[:shown]:
        print("   ", {k: (str(v)[:60] if v is not None else None) for k, v in r.items()})
    if shown < len(rows):
        # Say so explicitly. Silently printing 10 of 50 made a previous reader conclude
        # the router was lossy and write raw Cypher to work around it — which discards
        # the tier, jurisdiction and whitelist guarantees the router exists to enforce.
        print(f"  ... {len(rows) - shown} more — pass --rows 0 for all, or --json")
    if not rows and res.get("empty_reason"):
        print(f"  {res['empty_reason']}")
    if res.get("cypher"):
        print(f"\n  cypher:\n{res['cypher']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
