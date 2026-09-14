"""Udyam registry -> enriched MSME second-brain documents.

    msme/.venv/bin/python -m msme.preprocess --csv udyam_500k.csv [--limit N] [--no-llm]

Pipeline per firm:
  1. Parse the row + the Activities JSON (one or more NIC-5 codes).
  2. Enrich each NIC code -> canonical output/input material slugs (nic.enrich_nic, cached).
  3. Union the firm's outputs/inputs across its activities.
  4. (all firms scanned) build inverted material indexes producers[m] / consumers[m].
  5. Infer candidate roles by MATERIAL JOIN:
        potential_buyers[A]    = firms consuming what A outputs   (A SELLS_TO them)
        potential_suppliers[A] = firms producing what A inputs    (A BUYS_FROM them)
        competitors[A]         = firms outputting the same material AND same NIC class (+ geo)
  6. Emit, per firm: (a) a Cognee document (namespaced filter tags + prose body), and
     (b) deterministic supply edges. The bulk 500k lands in DuckDB (msme.duckdb) — the graph
     is promoted selectively (search hits / shortlists), mirroring marketdata's
     "numbers stay OUT of the graph; the store holds the mass" principle.

Outputs (all under msme/out/):
  firms.parquet     — the full structured registry (DuckDB reads this)
  documents.jsonl   — one Cognee-ready doc per firm (text + tags + meta)  [--emit-docs]
  edges.jsonl       — deterministic SELLS_TO / BUYS_FROM / COMPETES_WITH edges
"""
from __future__ import annotations

import argparse
import json
import pathlib
from collections import defaultdict

import pandas as pd

from . import nic as N

BASE = pathlib.Path(__file__).resolve().parent
OUT = BASE / "out"
OUT.mkdir(exist_ok=True)

# Search results must stay a "reasonable number" — every counterparty list is capped so no
# one firm carries thousands of edges (a mega-material like "cotton_yarn" would explode).
MAX_COUNTERPARTIES = 25


def _slug(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "_", (s or "").strip().lower()).strip("_")


def _get(row: dict, *keys) -> str:
    """First non-empty value across header variants (e.g. 'EnterpriseName' or 'Enterprise Name')."""
    for k in keys:
        v = row.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _clean_numeric_str(s: str) -> str:
    """Strip a trailing '.0' from an integer-valued field. CONFIRMED on the real Udyam export:
    345,061 of 539,453 rows format Pincode as '395006.0' while the rest are clean '395006' —
    an inconsistent Excel-export artifact within the SAME file. Without this, two firms at the
    identical physical pincode get different string keys, silently breaking exact pincode-tier
    search and producing inconsistent udyam fallback IDs for the same location."""
    s = (s or "").strip()
    if s.endswith(".0") and s[:-2].isdigit():
        return s[:-2]
    return s


def parse_activities(raw: str) -> list[dict]:
    """The Activities column is a JSON array string of {NIC5DigitId, Description}."""
    try:
        arr = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except Exception:  # noqa: BLE001
        return []
    out = []
    for a in arr if isinstance(arr, list) else []:
        code = str(a.get("NIC5DigitId", "")).strip()
        if code:
            out.append({"nic5": code, "desc": a.get("Description", "")})
    return out


def _dedupe_udyam(firms: list[dict]) -> int:
    """GUARANTEE zero udyam collisions, in place. Without a true registration-number column, no
    fallback key (however many fields we pack in) can be proven collision-free — two firms can
    coincidentally share pincode+name+reg_date too. This pass is the backstop: any key seen before
    gets a stable `-2`, `-3`, … suffix, in CSV row order (deterministic — same file -> same IDs
    every run). Confirmed necessary: real data still collided on 13,522/539,453 rows after adding
    reg_date to the base key, and those were GENUINELY different enterprises (different addresses/
    activities), not safe-to-merge duplicates — silently colliding would corrupt both firms'
    CLASSIFIED_AS edges onto a single graph node."""
    seen: dict[str, int] = {}
    n_collisions = 0
    for f in firms:
        base = f["udyam"]
        if base not in seen:
            seen[base] = 1
        else:
            seen[base] += 1
            n_collisions += 1
            f["udyam"] = f"{base}-{seen[base]}"
    return n_collisions


def build_firm(row: dict, nic_map: dict, vocab: N.Vocabulary, cache: dict, http) -> dict:
    """One CSV row -> a structured firm record with unioned output/input material slugs.
    Headers are read defensively (spaced + unspaced variants) so the real Udyam export
    (`EnterpriseName`, `RegistrationDate`) and the sample (`Enterprise Name`) both work."""
    acts = parse_activities(_get(row, "Activities", "activities"))
    outputs, inputs, nic_codes, sections, labels = set(), set(), [], set(), []
    for a in acts:
        e = N.enrich_nic(a["nic5"], a["desc"], nic_map, vocab, cache, http=http)
        outputs |= set(e["outputs"])
        inputs |= set(e["inputs"])
        nic_codes.append(a["nic5"])
        sections.add(e["section"])
        labels.append(e["label"])

    name = _get(row, "EnterpriseName", "Enterprise Name", "Name")
    pincode = _clean_numeric_str(_get(row, "Pincode", "PinCode", "pincode"))
    reg_date = _get(row, "RegistrationDate", "Registration Date")
    # There is no true unique registration-number column in the real export, so the fallback key
    # must pack in every distinguishing field available. pincode+name alone collided on 62,710/
    # 539,453 real rows (genuinely DIFFERENT enterprises sharing a common name like "FARMER" at the
    # same pincode) — silently merging their distinct NIC activities onto one graph node. Adding
    # reg_date cuts that to 13,522; run()'s _dedupe_udyam() then guarantees the rest are unique.
    udyam = _get(row, "Udyam", "UdyamRegistrationNumber", "Registration Number") \
        or f"{pincode}-{_slug(name)[:24]}-{_slug(reg_date)}"
    # GST is CUT (no GSTIN/PAN in Udyam data). gst_status stays "unknown"; an explicit column, if a
    # future export adds one, is still honoured. No verification is performed. See plan "GST LATER".
    gst_status = (_get(row, "gst_status", "GST Status") or "unknown").lower()
    return {
        "udyam": udyam,
        "name": name,
        "state": _get(row, "State", "state"),
        "district": _get(row, "District", "district"),
        "pincode": pincode,
        "reg_date": _get(row, "RegistrationDate", "Registration Date"),
        "address": _get(row, "CommunicationAddress", "Communication Address"),
        "nic_codes": nic_codes,
        "nic_classes": sorted({c[:4] for c in nic_codes}),
        "sections": sorted(sections),
        "activity_labels": labels,
        "outputs": sorted(outputs),
        "inputs": sorted(inputs),
        # GST is cut (no data); stays "unknown". Field + `gst:` tag kept so it's re-addable later.
        "gst_status": gst_status,
        "gstin": "",
    }


def build_indexes(firms: list[dict]):
    """Inverted material indexes — the whole supply web in two dicts, computed once."""
    producers: dict[str, set[int]] = defaultdict(set)   # material -> firm rows that OUTPUT it
    consumers: dict[str, set[int]] = defaultdict(set)   # material -> firm rows that INPUT it
    for i, f in enumerate(firms):
        for m in f["outputs"]:
            producers[m].add(i)
        for m in f["inputs"]:
            consumers[m].add(i)
    return producers, consumers


def infer_roles(i: int, firms: list[dict], producers, consumers):
    """Candidate counterparties for firm i, by material join. Returns dicts keyed by firm index
    -> the list of shared materials (the EVIDENCE that justifies each edge)."""
    f = firms[i]
    buyers, suppliers, competitors = defaultdict(list), defaultdict(list), defaultdict(list)

    for m in f["outputs"]:                       # who consumes what I make -> I sell to them
        for j in consumers.get(m, ()):
            if j != i:
                buyers[j].append(m)
        for j in producers.get(m, ()):           # who else makes what I make -> competitor
            if j != i and (set(firms[j]["nic_classes"]) & set(f["nic_classes"])):
                competitors[j].append(m)
    for m in f["inputs"]:                         # who makes what I need -> I buy from them
        for j in producers.get(m, ()):
            if j != i:
                suppliers[j].append(m)

    def _top(d):
        # Rank counterparties: more shared materials first, then same-district (local supply),
        # then GST-verified. Cap to keep the list a reasonable size.
        def _key(j):
            same_dist = firms[j]["district"] == f["district"]
            verified = firms[j]["gst_status"] == "verified"
            return (len(d[j]), same_dist, verified)
        return [{"udyam": firms[j]["udyam"], "via": sorted(set(d[j]))}
                for j in sorted(d, key=_key, reverse=True)[:MAX_COUNTERPARTIES]]

    return {"buyers": _top(buyers), "suppliers": _top(suppliers), "competitors": _top(competitors)}


# ---------------------------------------------------------------- documents

def firm_tags(f: dict) -> list[str]:
    """Namespaced node_set tags — the FILTER/INDEX layer (Cognee-native, same mechanism the
    dashboard + linker.py already use). Every dimension a search will constrain on is a tag."""
    tags = [
        f"udyam:{f['udyam']}",
        f"state:{_slug(f['state'])}",
        f"district:{_slug(f['district'])}",
        f"pincode:{f['pincode']}",
        f"gst:{f['gst_status']}",
    ]
    tags += [f"section:{s}" for s in f["sections"] if s and s != "?"]
    tags += [f"nic:{c}" for c in f["nic_codes"]]
    tags += [f"nicclass:{c}" for c in f["nic_classes"]]
    tags += [f"makes:{m}" for m in f["outputs"]]     # producer index
    tags += [f"needs:{m}" for m in f["inputs"]]       # consumer index
    return tags


def firm_prose(f: dict, vocab: N.Vocabulary, roles: dict | None = None) -> str:
    """Natural-language profile — what Cognee embeds/extracts for a promoted firm. Describes the
    firm's own products + inputs (enough for semantic search). Counterparty lines are added ONLY
    when `roles` is supplied (promote.py injects them for the small promoted slice, from the graph);
    the bulk pass leaves them out because materialising counterparties for every firm is O(n×bucket)
    and explodes in a dense single-cluster dataset."""
    def lbl(slug):
        return (vocab.materials.get(slug, {}) or {}).get("label", slug)
    makes = ", ".join(lbl(m) for m in f["outputs"]) or "no distinct manufactured output (trade/service)"
    needs = ", ".join(lbl(m) for m in f["inputs"]) or "no significant material inputs"
    acts = "; ".join(f["activity_labels"])
    lines = [
        f"{f['name']} is a micro/small enterprise in {f['district']}, {f['state']} (PIN {f['pincode']}).",
        f"Registered activities: {acts} (NIC {', '.join(f['nic_codes'])}).",
        f"It produces: {makes}. It consumes as raw material: {needs}.",
    ]
    if roles:
        if roles.get("suppliers"):
            v = sorted({m for r in roles["suppliers"] for m in r["via"]})
            lines.append(f"Buys from firms producing {', '.join(lbl(m) for m in v)}.")
        if roles.get("buyers"):
            v = sorted({m for r in roles["buyers"] for m in r["via"]})
            lines.append(f"Sells to firms that consume {', '.join(lbl(m) for m in v)}.")
    return "\n".join(lines)


def firm_document(f: dict, vocab: N.Vocabulary, roles: dict | None = None) -> dict:
    """The per-firm document fed to Cognee (`text` + `tags`). No `edges` — supply relationships are
    derived at query time via graph traversal (search.py), never materialised here."""
    return {
        "udyam": f["udyam"],
        "doc_type": "msme_profile",
        "tags": firm_tags(f),
        "text": firm_prose(f, vocab, roles),
        "meta": {k: f[k] for k in ("name", "state", "district", "pincode", "gst_status",
                                   "nic_codes", "outputs", "inputs")},
    }


# ---------------------------------------------------------------- driver

def _distinct_codes(df) -> dict:
    """All distinct NIC codes present in the data -> a representative description (for enrichment)."""
    codes: dict[str, str] = {}
    for raw in df.get("Activities", []):
        for a in parse_activities(raw):
            codes.setdefault(a["nic5"], a["desc"])
    return codes


def run(csv_path: str, limit: int | None, use_llm: bool, emit_docs: bool,
        enrich_only: bool = False, connected_only: bool = False):
    nic_map = N._materialize("nic_map.json")
    vocab = N.Vocabulary()
    cache = N._load_enrich_cache()
    http = None
    if use_llm:
        import httpx
        http = httpx.Client()

    df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    if limit:
        df = df.head(limit)
    print(f"loaded {len(df)} rows from {csv_path}")

    # --enrich-only: just warm the per-code material inference cache, then stop. This is the
    # (unary, once-per-code) inference step, kept separate from the firm pass so it's inspectable.
    if enrich_only:
        codes = _distinct_codes(df)
        print(f"enriching {len(codes)} distinct NIC codes (unary, cached)…")
        N.enrich_all(codes, nic_map, vocab, cache, http=http)
        if http:
            http.close()
        return

    firms = [build_firm(r, nic_map, vocab, cache, http) for r in df.to_dict("records")]
    vocab.flush_pending()
    n_collisions = _dedupe_udyam(firms)
    if n_collisions:
        print(f"disambiguated {n_collisions} fallback-id collisions (no unique registration-number "
              f"column in this export) — appended a stable suffix so every firm gets its own node")
    if connected_only:
        before = len(firms)
        firms = [f for f in firms if f["outputs"] or f["inputs"]]
        print(f"connected-only: dropped {before - len(firms)} firms with no material edge")
    enriched = sum(1 for f in firms if f["outputs"] or f["inputs"])
    print(f"built {len(firms)} firms ({enriched} with material profile); "
          f"{len(cache)} NIC codes LLM-enriched (cached)")

    # Structured registry -> Parquet (DuckDB reads this for filtered search; the mass never
    # goes into the graph). One row per firm, list columns kept as JSON strings for portability.
    reg = pd.DataFrame(firms)
    for col in ("nic_codes", "nic_classes", "sections", "activity_labels", "outputs", "inputs"):
        reg[col] = reg[col].map(json.dumps)
    reg.to_parquet(OUT / "firms.parquet", index=False)
    print(f"wrote {OUT/'firms.parquet'}")

    # Per-firm profile docs (linear). We do NOT materialise supply edges here — SELLS_TO/BUYS_FROM/
    # COMPETES_WITH are query-time graph traversals (search.py). Only these lightweight docs feed the
    # Cognee slice; promote.py enriches the promoted firms' prose with real counterparties.
    if emit_docs:
        with open(OUT / "documents.jsonl", "w") as df_out:
            for f in firms:
                df_out.write(json.dumps(firm_document(f, vocab)) + "\n")
        print(f"wrote {len(firms)} docs -> {OUT/'documents.jsonl'}")
    if http:
        http.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--no-llm", action="store_true", help="curated map + cache only, no gateway calls")
    ap.add_argument("--emit-docs", action="store_true", help="also write documents.jsonl (full corpus)")
    ap.add_argument("--enrich-only", action="store_true",
                    help="only warm the per-code material inference cache, then stop")
    ap.add_argument("--connected-only", action="store_true",
                    help="drop firms with no material edge before writing outputs")
    args = ap.parse_args()
    run(args.csv, args.limit, use_llm=not args.no_llm, emit_docs=args.emit_docs,
        enrich_only=args.enrich_only, connected_only=args.connected_only)


if __name__ == "__main__":
    main()
