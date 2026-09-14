"""NIC-2008 helpers + the material-mediated supply-chain brain.

Two curated resolvers, both materialized-from-seed like cognee/linker.py's symbols.json:
  - seeds/materials.json : the controlled material vocabulary (the JOIN KEY vocabulary)
  - seeds/nic_map.json   : NIC 5-digit -> {outputs[], inputs[]} in canonical material slugs

`enrich_nic()` is curated-first, cache-second, LLM-last: a NIC code seen in nic_map wins;
otherwise a cached LLM enrichment is reused; otherwise ONE gateway call fills it (there are
~1300 distinct NIC codes across all 500k firms, so this stays cheap and is done per *code*,
never per *firm*). Every material the LLM emits is snapped to a vocabulary slug via synonyms;
anything unrecognised is appended to cache/materials_pending.json for review (never dropped).
"""
from __future__ import annotations

import json
import os
import pathlib
import re

BASE = pathlib.Path(__file__).resolve().parent
CACHE = BASE / "cache"
CACHE.mkdir(exist_ok=True)

GATEWAY = os.environ.get("JARVIS_GATEWAY", "http://127.0.0.1:4000/v1")

# NIC enrichment is bulk + mechanical -> the cheap flash extractor. The msme project already maps
# preprocess/extractor -> deepseek/deepseek-v4-flash, but the LiteLLM gateway serves it under the
# DEDUPED POOL NAME `or--<slug>` (see cognee/routing.py), NOT the raw OpenRouter id. So resolve the
# same way cognee/ingest.py does, or the gateway 400s "model not found".
_DEFAULT_ENRICH_OR_ID = "deepseek/deepseek-v4-flash"


def _model_slug(oid: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (oid or "").lower()).strip("-") or "model"


def enrich_model() -> str:
    """Gateway model_name to call for enrichment. Env override wins; else resolve the msme
    `preprocess` role -> OpenRouter id -> pool name `or--<slug>` (mirrors routing.gateway_model)."""
    override = os.environ.get("MSME_ENRICH_MODEL")
    if override:
        return override
    roles = {}
    p = BASE.parent / "cognee" / "data" / "msme" / "models.json"
    try:
        roles = (json.loads(p.read_text()) or {}).get("roles") or {}
    except Exception:  # noqa: BLE001
        pass
    oid = roles.get("preprocess") or _DEFAULT_ENRICH_OR_ID
    return "or--" + _model_slug(oid)

# NIC-2008 division (first 2 digits) -> Section letter, by the official ranges.
_SECTIONS = [
    (1, 3, "A"), (5, 9, "B"), (10, 33, "C"), (35, 35, "D"), (36, 39, "E"),
    (41, 43, "F"), (45, 47, "G"), (49, 53, "H"), (55, 56, "I"), (58, 63, "J"),
    (64, 66, "K"), (68, 68, "L"), (69, 75, "M"), (77, 82, "N"), (84, 84, "O"),
    (85, 85, "P"), (86, 88, "Q"), (90, 93, "R"), (94, 96, "S"), (97, 98, "T"), (99, 99, "U"),
]


def division_of(nic5: str) -> str:
    return (nic5 or "").strip().zfill(5)[:2]


def section_of(nic5: str) -> str:
    try:
        d = int(division_of(nic5))
    except ValueError:
        return "?"
    for lo, hi, sec in _SECTIONS:
        if lo <= d <= hi:
            return sec
    return "?"


# ---------------------------------------------------------------- vocab / seeds

def _materialize(name: str) -> dict:
    """Read cache/<name> -> seeds/<name> -> {}. Writes the seed to cache on first access so a
    fresh run gets the curated core, then grows the cache copy in place (linker.py pattern)."""
    live, seed = CACHE / name, BASE / "seeds" / name
    for p in (live, seed):
        if p.exists():
            try:
                cfg = json.loads(p.read_text())
            except Exception:  # noqa: BLE001
                continue
            if p is seed:
                live.write_text(json.dumps(cfg, indent=2))
            return cfg
    return {}


def _singular(w: str, _min=3) -> str:
    """Cheap singularizer for word-overlap matching only (never touches the exact-match index or
    the on-disk vocab). Deliberately conservative: skips short words ('gas','dye') and double-s
    endings ('glass','brass') to avoid mangling real words into false stems."""
    if len(w) > _min and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > _min and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _load_vocab() -> dict:
    """Materials vocab is SEED-PRIORITY (unlike nic_map): the seed is the hand-curated source of
    truth, and editing it must take effect on the next run so the pending-material recovery workflow
    works. (nic_map still materializes to cache because the NIC master merge grows it on disk.)"""
    seed, live = BASE / "seeds" / "materials.json", CACHE / "materials.json"
    for p in (seed, live):
        if p.exists():
            try:
                return json.loads(p.read_text())
            except Exception:  # noqa: BLE001
                continue
    return {}


class Vocabulary:
    """Canonical material vocabulary + a synonym index for snapping free text to slugs."""

    def __init__(self):
        self.data = _load_vocab()
        self.materials: dict = self.data.get("materials", {})
        self._index: dict[str, str] = {}
        for slug, m in self.materials.items():
            self._index[slug.lower()] = slug
            self._index[(m.get("label") or "").lower()] = slug
            for syn in m.get("synonyms", []):
                self._index[syn.lower()] = slug
        self.pending: dict[str, int] = {}

    def snap(self, term: str) -> str | None:
        """Free-text material -> canonical slug, or None (and record it as pending).

        Matching is exact-then-WORD-BOUNDARY (with light singularization). A raw substring test
        used to produce false positives ('sand or gravel' -> beads via 'a'-ish overlaps, 'crude
        petroleum' -> dyes via 'dye' in 'petroleum'-adjacent text), which silently poisoned the
        graph. Requiring whole-word overlap keeps 'fancy laces' -> lace while rejecting incidental
        character matches. Singularizing each word ('fabrics'->'fabric', 'garments'->'garment')
        matters because the LLM freely mixes plural/singular and the earlier word-boundary fix
        would otherwise reject those as strictly as it rejects real false positives."""
        t = (term or "").strip().lower()
        if not t:
            return None
        if t in self._index:
            return self._index[t]
        t_words = {_singular(w) for w in re.findall(r"[a-z]+", t)}
        if t_words:
            for key, slug in self._index.items():
                k_words = {_singular(w) for w in re.findall(r"[a-z]+", key)}
                # every word of the shorter phrase must appear in the longer one
                if k_words and (k_words <= t_words or t_words <= k_words):
                    return slug
        self.pending[t] = self.pending.get(t, 0) + 1
        return None

    def flush_pending(self):
        """Merge this run's unmapped terms into the on-disk list (ACCUMULATE, never overwrite — a
        later small run must not clobber a big run's findings) and sort by frequency, high first."""
        if not self.pending:
            return
        path = CACHE / "materials_pending.json"
        merged: dict = {}
        if path.exists():
            try:
                merged = json.loads(path.read_text()) or {}
            except Exception:  # noqa: BLE001
                merged = {}
        for term, n in self.pending.items():
            merged[term] = merged.get(term, 0) + n
        ordered = dict(sorted(merged.items(), key=lambda kv: kv[1], reverse=True))
        path.write_text(json.dumps(ordered, indent=2))


# ---------------------------------------------------------------- enrichment

_ENRICH_CACHE_PATH = CACHE / "nic_enrich.json"


def _load_enrich_cache() -> dict:
    if _ENRICH_CACHE_PATH.exists():
        try:
            return json.loads(_ENRICH_CACHE_PATH.read_text())
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _save_enrich_cache(cache: dict):
    _ENRICH_CACHE_PATH.write_text(json.dumps(cache, indent=2))


_PROMPT = """You classify an Indian NIC-2008 economic activity into the materials it produces and consumes.
NIC code: {code}
Activity description: {desc}

Return STRICT JSON, no prose:
{{"outputs": ["<goods/services this activity PRODUCES>"], "inputs": ["<raw materials/components it CONSUMES>"]}}
Use short generic material nouns (e.g. "cotton yarn", "grey fabric", "zipper"), 1-6 items each.
A pure trading/retail/service activity has an empty "outputs"; its "inputs" are the goods it resells."""


def _llm_enrich(code: str, desc: str, http) -> dict:
    """One gateway call. DeepSeek is a reasoning model -> reasoning disabled or it burns the
    token cap thinking (the gotcha documented in cognee/preprocess.py)."""
    key = os.environ.get("MSME_LLM_KEY", "")
    r = http.post(
        f"{GATEWAY}/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": enrich_model(),
            "messages": [{"role": "user", "content": _PROMPT.format(code=code, desc=desc)}],
            "reasoning": {"enabled": False},
            "temperature": 0,
            "max_tokens": 300,
        },
        timeout=60,
    )
    r.raise_for_status()
    txt = r.json()["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", txt, re.DOTALL)
    return json.loads(m.group(0)) if m else {"outputs": [], "inputs": []}


def _snapped(raw_list, vocab: Vocabulary) -> list:
    return sorted({s for s in (vocab.snap(x) for x in (raw_list or [])) if s})


def enrich_nic(nic5: str, desc: str, nic_map: dict, vocab: Vocabulary,
               cache: dict, http=None) -> dict:
    """Return {label, section, division, outputs[slugs], inputs[slugs], source}. Curated wins,
    then cache, then a single LLM call (only if `http` is provided; else it fails open, empty).

    The cache stores the LLM's RAW materials (`outputs_raw`/`inputs_raw`) and we re-snap them against
    the CURRENT vocabulary on every call — so editing seeds/materials.json and re-running recovers
    previously-dropped ("pending") materials with ZERO new LLM calls. Legacy cache entries without
    raw are returned as-is (a one-time re-enrich captures their raw)."""
    code = (nic5 or "").strip()
    div, sec = division_of(code), section_of(code)

    # A nic_map entry may be a real CURATED mapping (has materials) OR just a label stub merged from
    # the NIC master (empty outputs/inputs). Only the former wins; a bare stub keeps its authoritative
    # label but falls through to cache/LLM so materials still get inferred.
    label = desc
    entry = nic_map.get("nic", {}).get(code)
    if entry:
        label = entry.get("label") or desc
        outs = _snapped(entry.get("outputs", []), vocab)
        ins = _snapped(entry.get("inputs", []), vocab)
        if outs or ins:
            return {"label": label, "section": sec, "division": div,
                    "outputs": outs, "inputs": ins, "source": "curated"}

    if code in cache:
        c = cache[code]
        if "outputs_raw" in c or "inputs_raw" in c:   # re-snap raw against the current vocabulary
            return {"label": c.get("label") or label, "section": sec, "division": div,
                    "outputs": _snapped(c.get("outputs_raw", []), vocab),
                    "inputs": _snapped(c.get("inputs_raw", []), vocab),
                    "source": c.get("source", "llm")}
        return c                                       # legacy entry (no raw) — as-is

    outs_raw, ins_raw = [], []
    source = "empty"
    if http is not None:
        try:
            res = _llm_enrich(code, label or "", http)  # best label available (master or Udyam desc)
            outs_raw, ins_raw = res.get("outputs", []), res.get("inputs", [])
            source = "llm"
        except Exception:  # noqa: BLE001
            source = "llm_error"

    rec = {
        "label": label or code, "section": sec, "division": div,
        "outputs": _snapped(outs_raw, vocab),
        "inputs": _snapped(ins_raw, vocab),
        "source": source,
    }
    if source == "llm":       # cache successful LLM enrichments, keeping RAW for re-snapping
        cache[code] = {**rec, "outputs_raw": outs_raw, "inputs_raw": ins_raw}
        _save_enrich_cache(cache)
    return rec


def enrich_all(codes: dict[str, str], nic_map: dict, vocab: "Vocabulary",
               cache: dict, http=None, log=print) -> dict:
    """Pre-warm the material inference for EVERY distinct NIC code in the data (unary, per code —
    never pairwise). `codes` maps code -> a representative description. Returns a coverage summary.
    Run once up front so the 500k firm pass is pure lookups."""
    tally = {"curated": 0, "cache": 0, "llm": 0, "llm_error": 0, "empty": 0}
    for i, (code, desc) in enumerate(sorted(codes.items()), 1):
        rec = enrich_nic(code, desc, nic_map, vocab, cache, http=http)
        tally[rec.get("source", "empty")] = tally.get(rec.get("source", "empty"), 0) + 1
        if i % 200 == 0:
            log(f"  enriched {i}/{len(codes)} codes…")
    vocab.flush_pending()
    log(f"enrichment coverage over {len(codes)} codes: " +
        ", ".join(f"{k}={v}" for k, v in tally.items() if v))
    return tally
