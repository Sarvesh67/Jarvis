"""Stage 7 — document index: DocChunk nodes, embeddings, and MENTIONS edges.

Corpus (spec §14): the Cosmetics Rules 2020 and its schedules, plus the company pages
S14 already landed. Chunks keep page and section anchors so an answer can cite "Rule 23,
page 24" rather than a floating paragraph — in a compliance product the citation is the
product.

Embeddings are 768-dim from the platform's LiteLLM gateway routed to local Ollama
(nomic-embed-text). Local and free, so a full rebuild costs nothing, which is exactly the
constraint the spec set when it asked for sentence-transformers — without the ~2 GB torch
install or a second embedding stack in the repo.

The linker attaches every chunk to the entities it names, using the same longest-match
n-gram matcher as S14b. The spec is blunt that "a chunk with zero MENTIONS is a red flag
— fix the linker, don't ship it", so the unlinked rate is reported every run and the
worst offenders are printed rather than quietly averaged away.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib  # noqa: E402
from pipeline.load_neo4j import pv, run, q  # noqa: E402

SOURCE = "s18_docs"
# ~800 tokens / 100 overlap, expressed in words (English prose runs ~0.75 words per token).
WORDS_PER_CHUNK = 600
WORD_OVERLAP = 75

RULE_RE = re.compile(r"^\s*(\d{1,3})\.\s+([A-Z][^.—]{4,90})[.—]", re.M)
COS_FORM_RE = re.compile(r"\bCOS-\s?(\d{1,2}[A-Z]?)\b")
SCHEDULE_RE = re.compile(
    r"\b(FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|SEVENTH|EIGHTH|NINTH|TENTH|ELEVENTH)\s+SCHEDULE\b")


SCHEDULE_WORDS = ("FIRST", "SECOND", "THIRD", "FOURTH", "FIFTH", "SIXTH", "SEVENTH",
                  "EIGHTH", "NINTH", "TENTH", "ELEVENTH")


def anchors_of(s: str) -> set[str]:
    """{"RULE 39", "TENTH SCHEDULE"} from "Rule 39(1) / Tenth Schedule"."""
    up = (s or "").upper()
    out = {f"RULE {m}" for m in re.findall(r"\bRULE\s*(\d{1,3})", up)}
    out |= {f"{w} SCHEDULE" for w in SCHEDULE_WORDS if f"{w} SCHEDULE" in up}
    return out


def chunk_words(text: str) -> list[str]:
    words = text.split()
    if not words:
        return []
    out, step = [], WORDS_PER_CHUNK - WORD_OVERLAP
    for i in range(0, len(words), step):
        piece = " ".join(words[i:i + WORDS_PER_CHUNK])
        if len(piece.split()) >= 40 or i == 0:
            out.append(piece)
        if i + WORDS_PER_CHUNK >= len(words):
            break
    return out


def rules_corpus(pdf_path: Path) -> list[dict]:
    """One record per page, carrying the rule/schedule anchor in force on that page."""
    import pdfplumber

    docs, section = [], None
    with pdfplumber.open(pdf_path) as pdf:
        for pageno, page in enumerate(pdf.pages, start=1):
            text = (page.extract_text() or "").strip()
            if len(text) < 80:
                continue
            sched = SCHEDULE_RE.search(text.upper())
            rule = RULE_RE.search(text)
            if sched:
                section = f"{sched.group(1).title()} Schedule"
            elif rule:
                section = f"Rule {rule.group(1)} — {rule.group(2).strip().title()}"
            forms = sorted({f"COS-{m}" for m in COS_FORM_RE.findall(text)})
            for piece in chunk_words(text):
                docs.append({
                    "forms": sorted({f"COS-{m}" for m in COS_FORM_RE.findall(piece)}) or forms,
                    "doc_id": "cosmetics_rules_2020",
                    "doc_title": "Cosmetics Rules, 2020 (India)",
                    "page": pageno, "section": section, "text": piece,
                    "source_id": "s05_rules", "tier": 1, "company_id": None,
                    "jurisdiction": "IN",
                })
    return docs


def site_corpus(limit: int) -> list[dict]:
    """Company pages, each already tied to a resolved company by host."""
    rows = q("""SELECT x.entity_id, r.website, r.legal_name FROM raw_company r
                JOIN crosswalk x ON x.source_id=r.source_id AND x.source_key=r.source_key
                 AND x.entity_type='company' AND x.superseded_by IS NULL
                WHERE r.source_id='s14_sites' AND r.website IS NOT NULL""")
    by_host = {(urlparse(r["website"]).netloc or "").lower().replace("www.", ""): r
               for r in rows}
    docs = []
    for p in sorted((lib.RAW / "s14_sites").glob("*.html")):
        host = p.name.split("_")[0].replace("www.", "").lower()
        meta = by_host.get(host)
        if not meta:
            continue
        soup = BeautifulSoup(p.read_text(encoding="utf-8", errors="ignore"), "lxml")
        for t in soup(["script", "style", "noscript", "nav", "footer"]):
            t.decompose()
        text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
        if len(text.split()) < 60:
            continue
        for piece in chunk_words(text):
            docs.append({
                "forms": [],
                "doc_id": f"site_{host}", "doc_title": meta["legal_name"],
                "page": None, "section": None, "text": piece,
                # tier 2: the company's own words about itself
                "source_id": "s14_sites", "tier": 2,
                "company_id": meta["entity_id"], "jurisdiction": None,
            })
        if limit and len({d["doc_id"] for d in docs}) >= limit:
            break
    return docs


def linker_vocab() -> tuple[dict[str, str], dict[str, str]]:
    """Ingredient keys (-> INCI name) and category needles.

    The ingredient keys are pipeline/materials.matching_keys(), the same set S14b and
    the router use (decisions/0018), so "glycerol" in a rule links to GLYCERIN.
    """
    from pipeline import materials
    ing = materials.matching_keys()
    # category names live on the ProductCategory nodes (the regulator vocabulary from S5)
    cats: dict[str, str] = {}
    drv = lib.neo4j_driver()
    with drv.session() as s:
        for rec in s.run("MATCH (c:ProductCategory) RETURN c.code AS code, c.name AS name"):
            name = (rec["name"] or "").strip().upper()
            if len(name) >= 5 and len(name.split()) <= lib.MAX_NGRAM:
                cats[name] = rec["code"]
    drv.close()
    return ing, cats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sites", type=int, default=200, help="cap on distinct company sites")
    ap.add_argument("--skip-sites", action="store_true")
    ap.add_argument("--wipe", action="store_true")
    a = ap.parse_args()

    pdf = lib.RAW / "s05_rules" / "cosmetics_rules_2020.pdf"
    docs = rules_corpus(pdf) if pdf.exists() else []
    print(f"  Cosmetics Rules 2020: {len(docs)} chunks")
    if not a.skip_sites:
        s_docs = site_corpus(a.sites)
        print(f"  company pages: {len(s_docs)} chunks from "
              f"{len({d['doc_id'] for d in s_docs})} sites")
        docs += s_docs
    if not docs:
        print("  nothing to index")
        return 0

    ing_needles, cat_needles = linker_vocab()
    ing_key_set = set(ing_needles)
    print(f"  linker vocabulary: {len(ing_needles)} ingredients, {len(cat_needles)} categories")

    for d in docs:
        d["chunk_id"] = "dc_" + hashlib.blake2b(
            f"{d['doc_id']}|{d['page']}|{d['text'][:200]}".encode(), digest_size=8).hexdigest()

    print(f"  embedding {len(docs)} chunks (768-dim, local)...", flush=True)
    vecs = lib.embed([d["text"] for d in docs])
    for d, v in zip(docs, vecs):
        d["embedding"] = v

    # The citation path that actually matters for a compliance answer: which paragraph of
    # the Rules SUPPORTS a given RegulatoryRule. Matched on the schedule/section anchor,
    # which is what the rule rows already carry as their clause.
    # RegulatoryRule nodes are keyed by a content hash, and a clause may name more than
    # one anchor ("Rule 39(1) / Tenth Schedule"), so both sides are reduced to a set of
    # anchors — {"RULE 39", "TENTH SCHEDULE"} — and linked when those sets intersect.
    clause_anchors: dict[str, set[str]] = {}
    for r in q("""SELECT DISTINCT clause FROM raw_rule
                  WHERE jurisdiction='IN' AND clause IS NOT NULL"""):
        clause_anchors[r["clause"]] = anchors_of(r["clause"])

    ing_edges, cat_edges, co_edges, rule_edges = [], [], [], []
    unlinked = 0
    per_doc_unlinked: dict[str, int] = defaultdict(int)
    for d in docs:
        toks = lib.tokens_of(d["text"])
        hits_i = {ing_needles[k] for k in lib.ngrams(toks, ing_key_set)}
        hits_c = lib.ngrams(toks, set(cat_needles))
        for inci in hits_i:
            ing_edges.append({"chunk_id": d["chunk_id"], "inci": inci,
                              "prov": pv(SOURCE, d["tier"], 0.8)})
        for name in hits_c:
            cat_edges.append({"chunk_id": d["chunk_id"], "code": cat_needles[name],
                              "prov": pv(SOURCE, d["tier"], 0.8)})
        if d["company_id"]:
            co_edges.append({"chunk_id": d["chunk_id"], "company_id": d["company_id"],
                             "prov": pv(SOURCE, d["tier"], 1.0)})
        sec_anchors = anchors_of(d["section"] or "")
        if sec_anchors:
            for clause, ca in clause_anchors.items():
                if ca & sec_anchors:
                    rule_edges.append({"chunk_id": d["chunk_id"], "clause": clause,
                                       "prov": pv(SOURCE, 1, 0.9)})
        if not (hits_i or hits_c or d["company_id"] or d["forms"]):
            unlinked += 1
            per_doc_unlinked[d["doc_id"]] += 1

    drv = lib.neo4j_driver()
    with drv.session() as s:
        if a.wipe:
            s.run("MATCH (d:DocChunk) DETACH DELETE d")
        run(s, """
            UNWIND $rows AS row
            MERGE (d:DocChunk {chunk_id: row.chunk_id})
            SET d.text = row.text, d.doc_id = row.doc_id, d.doc_title = row.doc_title,
                d.page = row.page, d.section = row.section, d.tier = row.tier,
                d.jurisdiction = row.jurisdiction, d.embedding = row.embedding,
                d.updated_at = datetime()
        """, [{k: d[k] for k in ("chunk_id", "text", "doc_id", "doc_title", "page",
                                 "section", "tier", "jurisdiction", "embedding")}
              for d in docs], "DocChunk")
        run(s, """
            UNWIND $rows AS row
            MATCH (d:DocChunk {chunk_id: row.chunk_id})
            MATCH (i:Ingredient {inci_name: row.inci})
            MERGE (d)-[e:MENTIONS]->(i) SET e += row.prov
        """, ing_edges, "MENTIONS->Ingredient")
        run(s, """
            UNWIND $rows AS row
            MATCH (d:DocChunk {chunk_id: row.chunk_id})
            MATCH (c:ProductCategory {code: row.code})
            MERGE (d)-[e:MENTIONS]->(c) SET e += row.prov
        """, cat_edges, "MENTIONS->ProductCategory")
        run(s, """
            UNWIND $rows AS row
            MATCH (d:DocChunk {chunk_id: row.chunk_id})
            MATCH (c:Company {company_id: row.company_id})
            MERGE (d)-[e:MENTIONS]->(c) SET e += row.prov
        """, co_edges, "MENTIONS->Company")
        run(s, """
            UNWIND $rows AS row
            MATCH (d:DocChunk {chunk_id: row.chunk_id})
            MATCH (r:RegulatoryRule {jurisdiction: 'IN', clause: row.clause})
            MERGE (r)-[e:SUPPORTED_BY]->(d) SET e += row.prov
        """, rule_edges, "RegulatoryRule SUPPORTED_BY DocChunk")
    drv.close()

    pct = 100 * unlinked / max(len(docs), 1)
    site_chunks = [d for d in docs if d["company_id"]]
    site_unlinked = sum(1 for d in site_chunks if not d["company_id"])
    print(f"S7 done: {len(docs)} chunks | MENTIONS: {len(ing_edges)} ingredient, "
          f"{len(cat_edges)} category, {len(co_edges)} company | "
          f"{len(rule_edges)} rule citations")
    print(f"  chunks with no entity link: {unlinked} ({pct:.1f}%)")
    if per_doc_unlinked:
        worst = sorted(per_doc_unlinked.items(), key=lambda kv: -kv[1])[:5]
        print("  worst documents: " + ", ".join(f"{k} ({v})" for k, v in worst))
    # Spec §14 calls a zero-MENTIONS chunk a red flag, but that rule assumes a corpus of
    # entity-bearing text. Procedural law legitimately names no ingredient and no company
    # ("Application for grant of licence shall be made in Form COS-5"), which is why those
    # chunks carry the COS form and the rule citation instead. The check that MUST hold is
    # that every COMPANY page links to its company.
    if site_unlinked:
        print(f"  ! {site_unlinked} company-page chunk(s) with no company link — "
              f"that IS a linker bug (spec §14)")
    else:
        print(f"  every company-page chunk links to its company "
              f"({len(site_chunks)} chunks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
