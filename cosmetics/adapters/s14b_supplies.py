"""S14b — ingredient supply claims from already-landed company pages.  Tier 3.

Closes the gap behind golden query 2 ("Indian suppliers of Niacinamide"), which returns
the ingredient but zero suppliers because no source in the register carries the
Company -> Ingredient edge. A raw-material supplier's own product pages do carry it.

Runs OFFLINE over `raw/s14_sites/` — no refetching. S14 lands pages; this reads them.
That separation means the matcher can be retuned and rerun for free.

Matching discipline, because a naive substring sweep over 36k INCI names against open-web
prose produces garbage:
  * the needles are pipeline/materials.matching_keys(): INCI names, their CosIng synonyms
    ("Glycerine", "glycerol") and CAS numbers, each resolving to exactly one INCI name.
    "MILK", "SAND", "CORN" and "HONEY" are real INCI names and also ordinary English, so
    they are excluded rather than guessed at (decisions/0011, 0018);
  * matches must fall on a word boundary;
  * a name counts only as an ENTRY — an element of its own (list item, cell, heading,
    link) or listed beside another substance or a CAS number — never as a mention in a
    sentence; and not where a prefix or suffix makes it another substance or product
    ("4-NITRO ANISOLE", "GLYCERIN SOAP BASE", "FORMALDEHYDE FREE", "EX BENZALDEHYDE");
  * a SITE must yield at least MIN_HITS distinct ingredients across its landed pages
    before ANY of them is kept, and must read as a catalogue rather than a storefront.
    A genuine supplier lists a catalogue; a single stray token is noise. The rule is per
    site, not per page: a catalogue split one product per page is still a catalogue.
Everything kept carries the sentence it was found in, and the string that matched.
"""
from __future__ import annotations

import argparse
import bisect
import re
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib  # noqa: E402
from pipeline import materials  # noqa: E402

SOURCE = "s14b_supplies"
TIER = 3
MIN_HITS = 3            # distinct ingredients before a site is believed at all

# A page must read like a SUPPLY offer before Company -[:SUPPLIES]-> Ingredient is
# asserted. Two failure modes made this necessary, both caught by reading the evidence:
#   * a cosmetics BRAND listing "Kojic Acid & Turmeric Face Wash — Rs 333" was being
#     recorded as a supplier of kojic acid. It uses the ingredient; it does not sell it.
#     That relationship is MARKETS -> Product -> CONTAINS, and asserting SUPPLIES for it
#     is simply a different claim from the one the page supports.
#   * a chemical company's own catalogue is unmistakable: "manufacturer & supplier",
#     CAS numbers, grades, pack sizes.
SUPPLY_CUES = ["MANUFACTURER", "SUPPLIER", "WE SUPPLY", "RAW MATERIAL", "CAS NO",
               "CAS NUMBER", "SPECIFICATION", "TECHNICAL GRADE", "BP GRADE", "IP GRADE",
               "USP", "PURITY", "MOQ", "BULK", "EXPORTER", "DISTRIBUTOR", "TRADER",
               "PRODUCT LIST", "PRODUCT RANGE", "OUR PRODUCTS", "CHEMICALS"]
RETAIL_CUES = ["ADD TO CART", "BUY NOW", "SHOP NOW", "WISHLIST", "CHECKOUT", "MRP",
               "FREE SHIPPING", "OUT OF STOCK", "CUSTOMER REVIEWS", "RATED", "OFFER PRICE"]
# A third failure mode, from the glycerin answers after the 2026-10-29 crawl: a brand or a
# private-label house that lists "Shea Butter Glycerin Body Cleanser" reads as a supplier,
# because it also says "manufacturer". It sells formulations, not glycerin. One phrase
# proves nothing ("contract manufacturing" is ordinary custom-synthesis language for a
# chemical maker); a site that talks to consumers and offers to make your brand does.
# Counted as distinct phrases across the whole site, hyphens read as spaces.
FINISHED_GOODS_CUES = ["PRIVATE LABEL", "WHITE LABEL", "THIRD PARTY MANUFACTUR",
                       "YOUR BRAND", "OWN BRAND", "LAUNCH YOUR", "PARABEN FREE",
                       "SULFATE FREE", "SULPHATE FREE", "CRUELTY FREE",
                       "DERMATOLOGICALLY TESTED", "CLINICALLY TESTED", "FOR ALL SKIN TYPES",
                       "BODY WASH", "FACE WASH", "SUNSCREEN"]
FINISHED_GOODS_MIN = 4

# tokens_of / ngram_spans live in lib so the Stage 7 doc linker uses the same matcher
tokens_of = lib.tokens_of

# A substance name that runs straight on into another word is usually naming a different
# product. Found by reading the glycerin evidence once GLYCERIN stopped being excluded:
# "GLYCEROL MONO STEARATE" is glyceryl stearate, "GLYCERIN SOAP BASE" is a soap base,
# "GLYCERINE CREAM" is a brand's cream, "FORMALDEHYDE FREE" is the opposite of a claim.
CONTINUED_BY = {
    "MONO", "DI", "TRI", "MONOSTEARATE", "DISTEARATE", "STEARATE", "OLEATE", "ESTER",
    "ESTERS", "DERIVATIVE", "DERIVATIVES", "BASED", "BASE", "SOAP", "CREAM", "LOTION",
    "GEL", "SHAMPOO", "SERUM", "FACE", "BODY", "HAIR", "TONER", "WASH", "BAR", "FREE",
    "ETHOXYLATE", "ETHOXYLATES", "DISPERSION", "EMULSION", "CONDENSATE", "BOOSTER",
    "SYRUP",
}
# ...and a following ester or salt word makes it that ester or salt: "GLYCEROL
# HYDROXYSTEARATE" is not glycerol, "CASTOR OIL SULPHATE" is not castor oil. A hydrate is
# the same substance ("DEXTROSE MONOHYDRATE" is glucose), and a word that starts the next
# listed substance ("POLYSORBATE 80 POLYSORBATE 85") ends this one rather than renaming it.
SALT_OR_ESTER = re.compile(r"^(?!.*HYDRATES?$)[A-Z]{4,}ATES?$")
NOT_A_SALT = {"CONCENTRATE", "INTERMEDIATE", "SUBSTRATE", "PRIVATE"}
# "NO PARABEN" is not an offer of paraben; "ROSE CRYSTAL EX BENZALDEHYDE" and "DEVELOPED
# FROM GLYCERIN" name what the product is made from, not the product.
NEGATED_BY = {"NO", "NON", "WITHOUT", "EX", "FROM"}
# The same from the other side: a substituent in front makes it another substance.
# "3-5 DIAMINO BENZOIC ACID", "4-CHLORO-3-NITRO ANISOLE", "OCTYL METHYL CINNAMATE",
# "CETO STEARYL ALCOHOL" (cetearyl alcohol). Isomer prefixes (ALPHA, D-, CIS) are not
# substituents and do not block: alpha-pinene is pinene.
SUBSTITUTED_BY = re.compile(
    r"(AMINO|NITRO|CHLORO|BROMO|FLUORO|IODO|HYDROXY|METHOXY|ETHOXY|PHENYL|BENZYL|"
    r"METHYL|ETHYL|PROPYL|BUTYL|OCTYL|BORNYL|ACETYL|HYDRO|CETO|SULPHO|SULFO|^ISO)-?$")

# A name counts only where it is an ENTRY, not a mention. Found by reading the claims the
# 2026-10-29 catalogue crawl added: a third of the names on smaller sites sat in
# sentences — "palmarosa is high in geraniol", "a mordant like tannic acid", "used in the
# production of polyvinyl chloride", "suitable for paraffin, soy and gel wax". A
# catalogue gives each product an element of its own (list item, table cell, heading,
# link: a text fragment of at most ENTRY_MAX words), or, where it dumps its range into one
# long paragraph, names each substance next to another one or to a CAS number (within
# LIST_WINDOW words). Requiring a recognised neighbour alone was tried and lost as many
# real claims as it removed: dye and aroma catalogues whose other entries are not in
# CosIng, and pages given over to a single product.
ENTRY_MAX = 12
LIST_WINDOW = 12


def listed(at: int, inci: str, anchors: list[tuple[int, str]]) -> bool:
    """Is another substance (not this one again) named within LIST_WINDOW words of `at`?"""
    lo = bisect.bisect_left(anchors, (at - LIST_WINDOW, ""))
    for pos, other in anchors[lo:]:
        if pos > at + LIST_WINDOW:
            return False
        if other != inci:
            return True
    return False

MODE_HINTS = {
    "manufacturer": ["WE MANUFACTURE", "MANUFACTURER OF", "OUR MANUFACTURING",
                     "MANUFACTURING FACILITY", "WE PRODUCE"],
    "agent": ["DISTRIBUTOR", "AUTHORISED AGENT", "AUTHORIZED AGENT", "TRADING",
              "IMPORTER", "SOLE AGENT", "STOCKIST"],
}


# Functions that mean the ingredient does something IN a cosmetic product. A supplier of
# hydrochloric acid is correctly recorded, but nobody formulating a face serum is
# shopping for it; the flag lets a query ask for the cosmetics-relevant slice without
# the matcher silently deciding what counts (spec: surface thin answers, never hide them).
COSMETIC_FUNCTIONS = {
    "SKIN CONDITIONING", "SKIN PROTECTING", "MOISTURISING", "HUMECTANT", "EMOLLIENT",
    "EMULSIFYING", "SURFACTANT", "SURFACTANT - CLEANSING", "SURFACTANT - EMULSIFYING",
    "SURFACTANT - FOAM BOOSTING", "CLEANSING", "FOAMING", "PRESERVATIVE", "ANTIOXIDANT",
    "UV FILTER", "UV ABSORBER", "HAIR CONDITIONING", "ANTISTATIC", "VISCOSITY CONTROLLING",
    "FILM FORMING", "SOOTHING", "SMOOTHING", "ANTI-SEBORRHEIC", "KERATOLYTIC", "BLEACHING",
    "ABRASIVE", "OPACIFYING", "PEARLESCENT", "COSMETIC COLORANT", "HAIR DYEING",
    "ANTIPERSPIRANT", "DEODORANT", "ANTI-ACNE", "ANTIDANDRUFF", "EXFOLIATING",
    "MASKING", "PERFUMING", "FRAGRANCE", "SOLVENT", "EMULSION STABILISING",
}


def cosmetic_ingredients() -> set[str]:
    with lib.pg() as c, c.cursor() as cur:
        cur.execute("""SELECT DISTINCT inci_name FROM raw_ingredient
                       WHERE source_id='s06_cosing' AND functions && %s""",
                    (sorted(COSMETIC_FUNCTIONS),))
        return {r[0] for r in cur.fetchall()}


def site_index() -> dict[str, dict]:
    """host -> the company entity S14 recorded for it."""
    with lib.pg() as c, c.cursor() as cur:
        cur.execute("""SELECT x.entity_id, r.website, r.legal_name, r.payload
                       FROM raw_company r
                       JOIN crosswalk x ON x.source_id=r.source_id AND x.source_key=r.source_key
                        AND x.entity_type='company' AND x.superseded_by IS NULL
                       WHERE r.source_id='s14_sites' AND r.website IS NOT NULL""")
        idx = {}
        for eid, site, name, payload in cur.fetchall():
            host = (urlparse(site).netloc or "").lower().replace("www.", "")
            if host:
                idx[host] = {"entity_id": eid, "website": site, "legal_name": name,
                             "name_on_site": (payload or {}).get("name_on_site")}
        return idx




def classify(text_upper: str) -> tuple[str, int, int]:
    """(verdict, supply_hits, retail_hits) — is this a catalogue or a storefront?"""
    sup = sum(1 for c in SUPPLY_CUES if c in text_upper)
    ret = sum(1 for c in RETAIL_CUES if c in text_upper)
    if sup >= 2 and sup > ret:
        return "supplier", sup, ret
    if ret >= 2 and ret >= sup:
        return "retailer_or_brand", sup, ret
    return "unclear", sup, ret


def page_fragments(path: Path) -> list[str]:
    """The page's text, one string per text node: a list item, a cell, a paragraph."""
    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="ignore"), "lxml")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    return list(soup.stripped_strings)


def page_text(path: Path) -> str:
    return " ".join(page_fragments(path))


def claims_on_page(frags: list[str], keys: dict[str, str], cas_keys: dict[str, str],
                   needle_set: set[str] | None = None) -> dict[str, tuple[str, str]]:
    """INCI -> (the string that matched, the words around it) for one page's fragments."""
    found: dict[str, tuple[str, str]] = {}
    # tokens of the whole page, and the size of the text fragment each one sits in
    toks, frag_len = [], []
    for f in frags:
        ft = tokens_of(f)
        toks += ft
        frag_len += [len(ft)] * len(ft)
    spans = lib.ngram_spans(toks, needle_set or set(keys))
    # where each substance is named on this page, CAS numbers included
    anchors = sorted([(i, keys[key]) for i, _k, key in spans]
                     + [(i, cas_keys.get(t, t)) for i, t in enumerate(toks)
                        if materials.cas_ok(t)])
    starts = {i for i, _k, _key in spans}
    for at, k, key in sorted(spans):
        inci = keys[key]
        if inci in found:
            continue
        nxt = toks[at + k] if at + k < len(toks) else ""
        if nxt in CONTINUED_BY or (SALT_OR_ESTER.match(nxt) and nxt not in NOT_A_SALT
                                   and at + k not in starts):
            continue
        if at > 0 and (toks[at - 1] in NEGATED_BY or SUBSTITUTED_BY.search(toks[at - 1])):
            continue
        entry = max(frag_len[at:at + k]) <= ENTRY_MAX
        if not (entry or listed(at, inci, anchors)):
            continue
        lo, hi = max(0, at - 22), min(len(toks), at + 26)
        found[inci] = (key, " ".join(toks[lo:hi])[:300])
    # A CAS number on a catalogue page is the least ambiguous name a substance has.
    text = " ".join(frags)
    for cas, off in materials.cas_in(text):
        inci = cas_keys.get(cas)
        if inci and inci not in found:
            ev = re.sub(r"\s+", " ", text[max(0, off - 140):off + 160])
            found[inci] = (cas, ev[:300])
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-hits", type=int, default=MIN_HITS)
    a = ap.parse_args()

    keys = materials.matching_keys()          # needle -> canonical INCI name
    cas_keys = materials.cas_index()          # CAS -> canonical INCI name
    cosmetic = cosmetic_ingredients()
    print(f"  {len(keys)} matching keys over {len(set(keys.values()))} INCI names "
          f"(INCI, synonyms; ambiguous and too-short names excluded), "
          f"{len(cas_keys)} unambiguous CAS numbers; {len(cosmetic)} carry a cosmetic "
          f"function")
    idx = site_index()
    print(f"  {len(idx)} scraped hosts mapped to companies")

    pages = sorted((lib.RAW / "s14_sites").glob("*.html"))
    print(f"  {len(pages)} landed pages")

    needle_set = set(keys)
    # host -> INCI -> (the string that matched, the sentence around it)
    per_host: dict[str, dict[str, tuple[str, str]]] = defaultdict(dict)
    host_mode: dict[str, str] = {}
    host_kind: dict[str, tuple[str, int, int]] = {}
    host_goods: dict[str, set[str]] = defaultdict(set)
    for p in pages:
        host = p.name.split("_")[0].replace("www.", "").lower()
        if host not in idx:
            continue
        frags = page_fragments(p)
        text = " ".join(frags)
        upper = text.upper()
        verdict, sup, ret = classify(upper)
        prev = host_kind.get(host)
        # a host is judged on its strongest page: one catalogue page is enough
        if prev is None or (verdict == "supplier" and prev[0] != "supplier"):
            host_kind[host] = (verdict, sup, ret)
        spaced = upper.replace("-", " ")
        host_goods[host].update(c for c in FINISHED_GOODS_CUES if c in spaced)
        for inci, hit in claims_on_page(frags, keys, cas_keys, needle_set).items():
            per_host[host].setdefault(inci, hit)
        if host not in host_mode:
            for mode, hints in MODE_HINTS.items():
                if any(h in upper for h in hints):
                    host_mode[host] = mode
                    break

    rows, kept_hosts, dropped_kind, dropped_goods = [], 0, 0, 0
    for host, hits in per_host.items():
        if len(hits) < a.min_hits:
            continue
        verdict, sup, ret = host_kind.get(host, ("unclear", 0, 0))
        if verdict != "supplier":
            # a brand that formulates WITH an ingredient is not a supplier OF it
            dropped_kind += 1
            continue
        if len(host_goods[host]) >= FINISHED_GOODS_MIN:
            dropped_goods += 1
            continue
        kept_hosts += 1
        meta = idx[host]
        for inci, (matched, evidence) in hits.items():
            rows.append({
                "source_id": SOURCE, "source_key": host, "entity_id": meta["entity_id"],
                "inci_name": inci, "matched_text": matched, "evidence": evidence,
                "mode": host_mode.get(host), "tier": TIER, "retrieved_at": lib.now(),
                "cosmetics_relevant": inci in cosmetic,
            })
    # This table is a pure function of raw/s14_sites and the matching rules, so it is
    # replaced, not merged: a claim the current rules no longer support must not survive
    # from an earlier run. raw/ itself is untouched.
    with lib.pg() as c:
        c.execute("DELETE FROM raw_supplies WHERE source_id = %s", (SOURCE,))
    n = lib.upsert("raw_supplies", rows, "source_id, source_key, inci_name")
    thin = sum(1 for h in per_host.values() if len(h) < a.min_hits)
    rel = sum(1 for r in rows if r["cosmetics_relevant"])
    by = defaultdict(int)
    for r in rows:
        by["inci" if r["matched_text"] == r["inci_name"]
           else "cas" if materials.cas_ok(r["matched_text"]) else "synonym"] += 1
    print(f"S14b done: {n} supply claims across {kept_hosts} suppliers "
          f"({rel} for ingredients with a cosmetic function)")
    print(f"  matched on: INCI name {by['inci']}, synonym {by['synonym']}, CAS {by['cas']}")
    print(f"  dropped: {thin} host(s) with fewer than {a.min_hits} distinct ingredients, "
          f"{dropped_kind} host(s) that read as a brand/storefront rather than a catalogue, "
          f"{dropped_goods} that sell finished or private-label cosmetics")
    if rows:
        top = defaultdict(int)
        for r in rows:
            top[r["inci_name"]] += 1
        print("  most-offered: " + ", ".join(
            f"{k} ({v})" for k, v in sorted(top.items(), key=lambda kv: -kv[1])[:8]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
