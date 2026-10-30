"""Material vocabulary — which strings may be used to find an ingredient in free text.

One module decides it, because three readers must agree (decisions/0018): S14b reading
supplier pages, the Stage 7 linker reading the rules corpus, and the router reading a
question. They used to share only S14b's AMBIGUOUS set and re-derive the rest, so a
length rule changed in one place silently disagreed with the others.

Matching keys are of three kinds, all resolving to ONE canonical INCI name:
  * the INCI name itself, and its parenthesis-free token form;
  * synonyms CosIng already carries — the US INCI name ("Triticum Aestivum (Wheat) Germ
    Extract"), the INN ("glycerol") and the chemical-name list ("Glycerine; Glycerol
    (INN); Glycyl Alcohol; 1,2,3-Propanetriol");
  * CAS numbers, check-digit validated.
A key that resolves to more than one INCI name is dropped rather than guessed at
(decisions/0011), and an INCI name always outranks a synonym that happens to equal it.
Keys find the INCI name; they are never shown as a fact about it.
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib  # noqa: E402

# Single-token INCI names that are also ordinary English, everyday materials, food or bare
# elements, and would match prose. Excluded outright rather than resolved by guessing
# (spec §7.4.4, decisions/0011). Multi-token names are consumed longest-first, so
# "TITANIUM DIOXIDE" still matches with TITANIUM listed here.
#
# Deliberately NOT here any more: GLYCERIN, MENTHOL, CAMPHOR, LANOLIN, BEESWAX, PARAFFIN,
# KAOLIN, GELATIN, UREA. They are substances, not prose; they were excluded because they
# also appear on brand pages, and a brand page is now rejected by S14b's catalogue-vs-
# storefront test before any of its matches is kept. Excluding GLYCERIN is what made
# "glycerin suppliers" return nothing (session 2026-10-08).
AMBIGUOUS = {
    # ordinary English and food
    "WATER", "AQUA", "MILK", "SAND", "CORN", "HONEY", "SALT", "SUGAR", "WINE", "BEER",
    "COFFEE", "TEA", "RICE", "WHEAT", "OATS", "SOY", "EGG", "CREAM", "BUTTER", "CLAY",
    "CHALK", "PEARL", "AMBER", "MUSK", "ROSE", "MINT", "BASIL", "PEPPER", "GINGER",
    "GARLIC", "LEMON", "ORANGE", "APPLE", "GRAPE", "OLIVE", "COCOA", "VANILLA", "ALOE",
    "HENNA", "NEEM", "TULSI", "AMLA", "AGAR", "LARD", "WHEY", "YEAST", "VINEGAR",
    "MOLASSES", "CARAMEL", "YOGURT", "SOYMILK", "ALGAE", "POLLEN", "OVUM", "BACILLUS",
    "BITTERN", "SPONGE", "AROMA", "PERFUME", "PARFUM", "FLAVOR", "FLAVOUR",
    "GRAPEFRUIT", "SOYBEAN", "AMARANTH", "MELT",
    # colours as often as dyes
    "INDIGO", "CARMINE",
    # generic chemistry and marketing words
    "ACID", "SODA", "LIME", "SOAP", "OIL", "WAX", "GUM", "RESIN", "ALCOHOL", "STARCH",
    "PROTEIN", "COLLAGEN", "KERATIN", "GLYCOL", "PETROLEUM", "ANTIBIOTICS",
    # what a chemical is PACKED in, not what is sold: "supplied in polyethylene bags"
    "POLYETHYLENE", "POLYPROPYLENE", "POLYSTYRENE",
    # minerals, gems, textiles and other materials
    "MICA", "TALC", "SILICA", "GLASS", "COTTON", "SILK", "WOOL", "RAYON", "CHARCOAL",
    "GRANITE", "QUARTZ", "PUMICE", "SCHIST", "SILT", "LOESS", "PEAT", "TOPAZ", "EMERALD",
    "CITRINE", "UMBER", "LIMUS", "FELDSPAR",
    # bare elements
    "IRON", "ZINC", "COPPER", "SILVER", "GOLD", "CARBON", "SULFUR", "SULPHUR", "CALCIUM",
    "CHLORINE", "CHROMIUM", "COBALT", "HYDROGEN", "NITROGEN", "OXYGEN", "OZONE", "ARGON",
    "PLATINUM", "TITANIUM", "SILICON", "ALUMINUM", "ALUMINIUM", "IODINE", "MAGNESIUM",
    "PHOSPHORUS", "PALLADIUM",
    # a different word in English
    "RETINAL",
}

MIN_LEN_SINGLE = 4      # PEG-8, EDTA, DMSO, SQUALANE, CARBOMER; was 9, which dropped 571
MIN_LEN_MULTI = 7
MAX_KEY_LEN = 60

# CosIng tags a synonym with where it comes from: "Glycerol (INN)", "Glycerolum (EP)".
_SOURCE_TAG = re.compile(r"\s*\((?:INN|INNM|RIFM|EP|USAN|BAN|JAN|USP|IUPAC|CAS|DCF|"
                         r"FCC|INCI|EU|PH\.? ?EUR\.?)\)\s*$", re.I)

CAS_RE = re.compile(r"(?<![\d-])(\d{2,7})-(\d{2})-(\d)(?![\d-])")


def key_form(text: str) -> str:
    """The token form a page is matched in — lib.tokens_of, so both sides agree."""
    return " ".join(lib.tokens_of(text))


def needle_ok(key: str) -> bool:
    """May this (already key_form'd) string be used to find an ingredient in prose?"""
    if not key or key in AMBIGUOUS or len(key) > MAX_KEY_LEN:
        return False
    toks = key.split()
    if len(toks) > lib.MAX_NGRAM:            # the matcher never looks at longer spans
        return False
    return len(key) >= (MIN_LEN_MULTI if len(toks) > 1 else MIN_LEN_SINGLE)


def cas_ok(cas: str) -> bool:
    """CAS check digit: sum of digits weighted 1, 2, 3… from the right, mod 10."""
    m = re.fullmatch(r"(\d{2,7})-(\d{2})-(\d)", cas or "")
    if not m:
        return False
    body = m.group(1) + m.group(2)
    return sum(i * int(d) for i, d in enumerate(reversed(body), 1)) % 10 == int(m.group(3))


def cas_in(text: str) -> list[tuple[str, int]]:
    """[(cas, char offset)] for every check-digit-valid CAS number in the text."""
    out = []
    for m in CAS_RE.finditer(text):
        cas = m.group(0)
        if cas_ok(cas):
            out.append((cas, m.start()))
    return out


def _synonyms(row: dict) -> list[str]:
    p = row.get("payload") or {}
    raw = [p.get("inci_usa_name"), p.get("inn_name")]
    raw += (p.get("chemical_name") or "").split(";")
    out = []
    for s in raw:
        s = _SOURCE_TAG.sub("", (s or "").strip())
        if s:
            out.append(s)
    return out


@lru_cache(maxsize=1)
def _cosing() -> list[dict]:
    with lib.pg() as c, c.cursor() as cur:
        cur.execute("SELECT inci_name, cas, payload FROM raw_ingredient "
                    "WHERE source_id='s06_cosing' AND inci_name IS NOT NULL")
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]


@lru_cache(maxsize=1)
def matching_keys() -> dict[str, str]:
    """{key -> canonical INCI name} for every usable, unambiguous key."""
    rows = _cosing()
    inci_names = {r["inci_name"] for r in rows}
    claims: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        inci = r["inci_name"]
        if inci in AMBIGUOUS:
            continue                     # its synonyms are as ambiguous as it is
        for cand in [inci] + _synonyms(r):
            k = key_form(cand)
            if needle_ok(k):
                claims[k].add(inci)
    out = {}
    for k, owners in claims.items():
        if k in inci_names:
            if k in owners:
                out[k] = k               # an INCI name always means itself
            continue                     # a synonym may not hijack another INCI name
        if len(owners) == 1:
            out[k] = next(iter(owners))
    return out


@lru_cache(maxsize=1)
def cas_index() -> dict[str, str]:
    """{CAS -> canonical INCI name}, only where the CAS names exactly one INCI."""
    owners: dict[str, set[str]] = defaultdict(set)
    for r in _cosing():
        inci = r["inci_name"]
        if inci in AMBIGUOUS:
            continue
        p = r.get("payload") or {}
        for cas in {r.get("cas"), *(p.get("cas_all") or [])}:
            cas = (cas or "").strip()
            if cas_ok(cas):
                owners[cas].add(inci)
    return {cas: next(iter(s)) for cas, s in owners.items() if len(s) == 1}


def resolve_name(text: str) -> str | None:
    """A free-text material name or CAS -> its INCI name, or None. Exact keys only."""
    t = (text or "").strip()
    if cas_ok(t):
        return cas_index().get(t)
    return matching_keys().get(key_form(t))
