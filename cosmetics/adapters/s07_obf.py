"""S7 — Open Beauty Facts.  Tier 3 (observed).

Purpose per spec §4/S7: this is NOT a company source and never mints Company nodes.
Its value is the ALIAS TABLE — pack ingredient lists carry the misspellings,
abbreviations and punctuation variants a clean dictionary (S6) never shows — plus
real observed Indian products for the Product->Ingredient layer.

Gate (spec §6 S7 failure mode): if India coverage with usable ingredient text is
below the threshold, we keep alias mining and skip the product layer, and say so.

Licence: ODbL, share-alike. Fine for the PoC; flagged for legal review before
commercialisation (spec §4/S7).
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from collections import Counter
from pathlib import Path

from rapidfuzz import fuzz, process

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib  # noqa: E402

SOURCE = "s07_obf"
TIER = 3
EXPORT = lib.RAW / "s07_obf" / "openbeautyfacts-products.jsonl.gz"
INDIA_GATE = 500

CI_RE = re.compile(r"\bCI\s*[\.:]?\s*(\d{5})\b", re.I)
NANO_RE = re.compile(r"\((?:nano|nanoparticules?)\)", re.I)
PAREN_RE = re.compile(r"\s*\([^)]*\)")
SPLIT_RE = re.compile(r"[,;•·\n\r]|\s+/\s+")
NOISE_RE = re.compile(r"^[\W_\d]+$")


def normalize_token(s: str) -> str:
    """Spec §7.4.1: uppercase, strip * and (nano), split CI-numbers out."""
    s = NANO_RE.sub(" ", s)
    s = s.replace("*", " ").replace("_", " ")
    s = PAREN_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip(" .:-").upper()
    return s


def split_ingredients(text: str) -> list[str]:
    if not text:
        return []
    out = []
    for part in SPLIT_RE.split(text):
        tok = normalize_token(part)
        if not tok or NOISE_RE.match(tok) or len(tok) < 3 or len(tok) > 90:
            continue
        out.append(tok)
    return out


def load_vocabulary() -> list[str]:
    with lib.pg() as c, c.cursor() as cur:
        cur.execute("SELECT DISTINCT inci_name FROM raw_ingredient WHERE source_id='s06_cosing'")
        return [r[0] for r in cur.fetchall() if r[0]]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-count", type=int, default=2, help="alias must appear on >=N packs")
    ap.add_argument("--fuzzy-cutoff", type=int, default=95, help="spec §7.4.3")
    ap.add_argument("--max-aliases", type=int, default=40000)
    a = ap.parse_args()

    if not EXPORT.exists():
        print(f"! export missing: {EXPORT}")
        return 1

    vocab = load_vocabulary()
    vocab_set = set(vocab)
    print(f"  S6 vocabulary: {len(vocab)} INCI names")

    counts: Counter[str] = Counter()
    india_products: list[dict] = []
    n_total = n_india = n_india_ing = 0

    with gzip.open(EXPORT, "rt", encoding="utf-8") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            n_total += 1
            text = d.get("ingredients_text") or d.get("ingredients_text_en") or ""
            toks = split_ingredients(text)
            counts.update(toks)

            if "en:india" in (d.get("countries_tags") or []):
                n_india += 1
                if toks:
                    n_india_ing += 1
                india_products.append({
                    "source_id": SOURCE, "source_key": str(d.get("code") or "")[:100], "tier": TIER,
                    "product_name": (d.get("product_name") or d.get("product_name_en") or "")[:300] or None,
                    "brands": [b.strip() for b in (d.get("brands") or "").split(",") if b.strip()][:10],
                    "countries": d.get("countries_tags") or [],
                    "categories_tags": d.get("categories_tags") or [],
                    "ingredients_text": text or None,
                    "ingredients_tags": (d.get("ingredients_tags") or [])[:200],
                    "payload": lib.jsonb({"lang": d.get("lang"), "tokens": toks[:200]}),
                })
            if n_total % 20000 == 0:
                print(f"    {n_total} products scanned, {n_india} India, {len(counts)} distinct strings",
                      flush=True)

    print(f"  scanned {n_total} products: {n_india} India ({n_india_ing} with ingredient text)")

    # --- alias resolution (spec §7.4) ------------------------------------------------
    candidates = [(s, n) for s, n in counts.items() if n >= a.min_count and s not in vocab_set]
    candidates.sort(key=lambda x: -x[1])
    candidates = candidates[:a.max_aliases]
    print(f"  {len(counts)} distinct strings; {len(candidates)} unresolved candidates to match")

    alias_rows, unresolved = [], []
    exact_hits = sum(1 for s in counts if s in vocab_set)
    for i, (s, n) in enumerate(candidates):
        # CI colour numbers are a separate namespace, not INCI
        if CI_RE.fullmatch(s) or s.startswith("CI "):
            continue
        m = process.extractOne(s, vocab, scorer=fuzz.token_sort_ratio, score_cutoff=a.fuzzy_cutoff)
        if m:
            alias_rows.append({
                "source_id": SOURCE, "source_key": f"alias:{s}"[:200], "tier": TIER,
                "inci_name": m[0], "cas": None, "ec_no": None, "functions": [],
                "alias": s,
                "payload": lib.jsonb({"score": m[1], "occurrences": n, "method": "token_sort_ratio"}),
            })
        else:
            unresolved.append({"raw_string": s[:300], "source_id": SOURCE, "occurrences": n,
                               "best_candidate": None, "best_score": None})
        if (i + 1) % 5000 == 0:
            print(f"    matched {i+1}/{len(candidates)} ({len(alias_rows)} aliases)", flush=True)

    n_alias = lib.upsert("raw_ingredient", alias_rows, "source_id, source_key")
    n_unres = lib.upsert("unresolved_ingredient", unresolved[:20000], "raw_string")

    gate_ok = n_india_ing >= INDIA_GATE
    n_prod = 0
    if gate_ok:
        n_prod = lib.upsert("raw_product", india_products, "source_id, source_key")
    print(f"S7 done: {exact_hits} exact vocab hits, {n_alias} aliases, {n_unres} unresolved")
    print(f"  India gate ({n_india_ing} >= {INDIA_GATE}): {'PASS' if gate_ok else 'FAIL'}"
          f" -> {n_prod} India products written" if gate_ok else
          f"  India gate FAIL ({n_india_ing} < {INDIA_GATE}) -> alias-only, product layer skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
