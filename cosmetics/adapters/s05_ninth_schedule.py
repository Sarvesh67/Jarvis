"""S5 — Cosmetics Rules 2020: category spine + India regulatory rules.  Tier 1.

The category spine comes from the regulator (hard rule 10). Reconnaissance found a
better spine than the spec assumed: the **Fourth Schedule** ("List of categories of
cosmetics for import", 80 entries) is CDSCO's own category vocabulary, while the
**Ninth Schedule** (37 entries) attaches BIS standards to finished forms. So:

    Fourth Schedule -> ProductCategory nodes (the vocabulary)
    Ninth  Schedule -> RegulatoryRule{jurisdiction: IN, scope: category} (the standards)
    Rules 18/39 body text + Tenth Schedule -> RegulatoryRule{scope: ingredient}

Both rule shapes the spec demands are therefore expressible and are asserted in main().

The tables below are HAND-VERIFIED against the gazette text, as the spec requires — a
shifted row would assert the wrong BIS standard for a category and the customer would
find out at a testing lab. The PDF is still parsed on every run and compared against
these constants, so a changed gazette surfaces as a drift warning instead of silence.
Two OCR defects in the source PDF are corrected here and recorded:
  * entries 29/30 render as "2C Other face make-up products" / "3f. Mascara"
  * entries 55/56 line-wrap into "Products for temporary hair" / "Styling Permanent wave
    products"; the true reading is "…temporary hair styling" / "Permanent wave products"
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib  # noqa: E402

SOURCE = "s05_rules"
TIER = 1
PDF = lib.RAW / "s05_rules" / "cosmetics_rules_2020.pdf"
DOC = "Cosmetics Rules, 2020 (G.S.R. 763(E))"

# --- Fourth Schedule: (serial, category name, spine code) --------------------------
FOURTH: list[tuple[int, str, str]] = [
    (1, "Face care products other than face mask", "SKIN_CARE"),
    (2, "Face mask", "SKIN_CARE"),
    (3, "Eye contour products", "SKIN_CARE"),
    (4, "Lip care products", "SKIN_CARE"),
    (5, "Hand care products", "SKIN_CARE"),
    (6, "Foot care products", "SKIN_CARE"),
    (7, "Body care products", "SKIN_CARE"),
    (8, "External intimate care products", "SKIN_CARE"),
    (9, "Chemical exfoliation products", "SKIN_CARE"),
    (10, "Mechanical exfoliation products", "SKIN_CARE"),
    (11, "Skin lightening products", "SKIN_CARE"),
    (12, "Other skin care products", "SKIN_CARE"),
    (13, "Soap products", "SOAP"),
    (14, "Bath / shower products", "SKIN_CLEANSING"),
    (15, "Make-up remover products", "SKIN_CLEANSING"),
    (16, "External intimate hygiene products", "SKIN_CLEANSING"),
    (17, "Other skin cleansing products", "SKIN_CLEANSING"),
    (18, "Chemical depilatories", "HAIR_REMOVAL"),
    (19, "Physical epilation products", "HAIR_REMOVAL"),
    (20, "Other body hair removal products", "HAIR_REMOVAL"),
    (21, "Bleach for body hair", "HAIR_REMOVAL"),
    (22, "Products with antiperspirant activity", "DEODORANT"),
    (23, "Products without antiperspirant activity", "DEODORANT"),
    (24, "Shaving products", "SHAVING"),
    (25, "Pre- / after-shaving products", "SHAVING"),
    (26, "Other shaving and pre- / after-shaving products", "SHAVING"),
    (27, "Foundation", "MAKEUP"),
    (28, "Concealer", "MAKEUP"),
    (29, "Other face make-up products", "MAKEUP"),          # OCR: "2C"
    (30, "Mascara", "MAKEUP"),                              # OCR: "3f."
    (31, "Eye shadow", "MAKEUP"),
    (32, "Eye pencil", "MAKEUP"),
    (33, "Eye liner", "MAKEUP"),
    (34, "Other eye make-up products", "MAKEUP"),
    (35, "Lip stick", "MAKEUP"),
    (36, "Lipstick sealer", "MAKEUP"),
    (37, "Other lip make-up products", "MAKEUP"),
    (38, 'Body or face paint, including "carneval make-up"', "MAKEUP"),
    (39, "Other make-up products", "MAKEUP"),
    (40, "Hydro alcoholic perfumes", "FRAGRANCE"),
    (41, "Non hydro alcoholic perfumes", "FRAGRANCE"),
    (42, "Before and after sun products / sun protection products", "SUN_CARE"),
    (43, "Self-tanning products", "SUN_CARE"),
    (44, "Other sun and self-tanning products", "SUN_CARE"),
    (45, "Other skin products", "SKIN_CARE"),
    (46, "Hair conditioner", "HAIR_CARE"),
    (47, "Scalp and hair roots care products", "HAIR_CARE"),
    (48, "Anti hair loss products", "HAIR_CARE"),
    (49, "Other hair and scalp care and cleansing products", "HAIR_CARE"),
    (50, "Antidandruff products", "HAIR_CARE"),
    (51, "Oxidative hair colour products", "HAIR_COLOUR"),
    (52, "Non-oxidative hair colour products", "HAIR_COLOUR"),
    (53, "Hair bleaching and dye remover products", "HAIR_COLOUR"),
    (54, "Other hair colouring products", "HAIR_COLOUR"),
    (55, "Products for temporary hair styling", "HAIR_STYLING"),   # line-wrap corrected
    (56, "Permanent wave products", "HAIR_STYLING"),               # line-wrap corrected
    (57, "Hair relaxer / straightener products", "HAIR_STYLING"),
    (58, "Other hair styling products", "HAIR_STYLING"),
    (59, "Hair sun protection products", "HAIR_CARE"),
    (60, "Other hair and scalp products", "HAIR_CARE"),
    (61, "Nail varnish / nail make-up", "NAIL_CARE"),
    (62, "Nail varnish remover", "NAIL_CARE"),
    (63, "Nail varnish thinner", "NAIL_CARE"),
    (64, "Nail bleach", "NAIL_CARE"),
    (65, "Other nail varnish and remover products", "NAIL_CARE"),
    (66, "Nail care products", "NAIL_CARE"),
    (67, "Nail hardener", "NAIL_CARE"),
    (68, "Other nail care / nail hardener products", "NAIL_CARE"),
    (69, "Nail glue remover", "NAIL_CARE"),
    (70, "Cuticle remover / softener", "NAIL_CARE"),
    (71, "Nail sculpting products", "NAIL_CARE"),
    (72, "Other nail and cuticle products", "NAIL_CARE"),
    (73, "Toothpaste", "ORAL_CARE"),
    (74, "Tooth cleansing powder / salt", "ORAL_CARE"),
    (75, "Other tooth care products", "ORAL_CARE"),
    (76, "Mouth wash", "ORAL_CARE"),
    (77, "Breath spray", "ORAL_CARE"),
    (78, "Other mouth wash / breath spray products", "ORAL_CARE"),
    (79, "Tooth whiteners", "ORAL_CARE"),
    (80, "Other oral hygiene products", "ORAL_CARE"),
]

# --- Ninth Schedule: (serial, finished form, IS number, spine code) ----------------
NINTH: list[tuple[int, str, str, str]] = [
    (1, "Skin Powders", "IS 3959", "SKIN_CARE"),
    (2, "Skin Powder for infants", "IS 5339", "BABY_CARE"),
    (3, "Tooth Powder", "IS 5383", "ORAL_CARE"),
    (4, "Toothpaste", "IS 6356", "ORAL_CARE"),
    (5, "Skin Creams", "IS 6608", "SKIN_CARE"),
    (6, "Hair Oils", "IS 7123", "HAIR_CARE"),
    (7, "Shampoo, Soap-based", "IS 7669", "HAIR_CARE"),
    (8, "Shampoo Specification", "IS 7884", "HAIR_CARE"),
    (9, "Hair Creams", "IS 7679", "HAIR_CARE"),
    (10, "Oxidation hair dyes, Liquid", "IS 8481", "HAIR_COLOUR"),
    (11, "Cologne", "IS 8482", "FRAGRANCE"),
    (12, "Nail Polish (Nail Enamel)", "IS 9245", "NAIL_CARE"),
    (13, "Aftershave Lotion", "IS 9255", "SHAVING"),
    (14, "Pomades and Brilliantines", "IS 9339", "HAIR_STYLING"),
    (15, "Depilatories Chemicals", "IS 9636", "HAIR_REMOVAL"),
    (16, "Shaving Creams", "IS 9740", "SHAVING"),
    (17, "Cosmetic Pencils", "IS 9832", "MAKEUP"),
    (18, "Lipstick", "IS 9875", "MAKEUP"),
    (19, "Toilet Soap", "IS 2888", "SOAP"),
    (20, "Liquid Toilet Soap", "IS 4199", "SOAP"),
    (21, "Baby Toilet Soap", "IS 10523", "BABY_CARE"),
    (22, "Shaving Soap", "IS 5784", "SHAVING"),
    (23, "Transparent Toilet Soap", "IS 11303", "SOAP"),
    (24, "Lipsalve", "IS 10284", "SKIN_CARE"),
    (25, "Powder Hair Dye", "IS 10350", "HAIR_COLOUR"),
    (26, "Bindi (Liquid)", "IS 10998", "TRADITIONAL"),
    (27, "KumKum Powder", "IS 10999", "TRADITIONAL"),
    (28, "Henna Powder", "IS 11142", "HAIR_COLOUR"),
    (29, "Bathing Bars", "IS 13498", "SOAP"),
    (30, "Sindoor", "IS 14649", "TRADITIONAL"),
    (31, "Liquid foundation make-up", "IS 14318", "MAKEUP"),
    (32, "Coldwax-Hair remover", "IS 15152", "HAIR_REMOVAL"),
    (33, "Face pack", "IS 15153", "SKIN_CARE"),
    (34, "Kajal", "IS 15154", "TRADITIONAL"),
    (35, "Oxidation Hair Dyes (Emulsion type)", "IS 15205", "HAIR_COLOUR"),
    (36, "Cream Bleach", "IS 15608", "HAIR_REMOVAL"),
    (37, "Hair Shampoo for Babies", "IS 17117", "BABY_CARE"),
]

# --- ingredient-scoped India rules, from the rule body + Tenth Schedule ------------
INGREDIENT_RULES: list[dict] = [
    {"subject": "HEXACHLOROPHENE", "clause": "Rule 39(4)", "status": "prohibited",
     "max_concentration": "0.1 %",
     "conditions": "No cosmetic containing hexachlorophene shall be manufactured, except "
                   "soaps, where it shall not exceed 0.1 per cent and the wrapper must carry "
                   '"Contains hexachlorophene - not to be used on babies".'},
    {"subject": "HEXACHLOROPHENE", "clause": "Rule 18(3)", "status": "prohibited",
     "max_concentration": None,
     "conditions": "No cosmetic containing hexachlorophene shall be imported."},
    {"subject": "MERCURY", "clause": "Rule 39(5)(a)", "status": "restricted",
     "max_concentration": "70 ppm (0.007 %)",
     "conditions": "Permitted only as a preservative in cosmetics intended for use in the "
                   "area of the eye, calculated as the metal."},
    {"subject": "MERCURY", "clause": "Rule 39(5)(b)", "status": "restricted",
     "max_concentration": "1 ppm",
     "conditions": "In other finished cosmetic products, only unintentional trace mercury "
                   "is permitted."},
    {"subject": "ARSENIC", "clause": "Rule 39(3)(i)", "status": "restricted",
     "max_concentration": "2 ppm",
     "conditions": "In permitted synthetic and natural organic colours, calculated as "
                   "arsenic trioxide."},
    {"subject": "LEAD", "clause": "Rule 39(3)(ii)", "status": "restricted",
     "max_concentration": "20 ppm",
     "conditions": "In permitted synthetic and natural organic colours, calculated as lead."},
    {"subject": "HEAVY METALS (OTHER THAN LEAD)", "clause": "Rule 39(3)(iii)",
     "status": "restricted", "max_concentration": "100 ppm",
     "conditions": "Total of the respective metals, in permitted colours."},
    {"subject": "P-PHENYLENEDIAMINE", "clause": "Rule 37", "status": "restricted",
     "max_concentration": None,
     "conditions": "Hair dyes containing paraphenylenediamine or other dyes, colours and "
                   "pigments must carry the prescribed caution legend on inner and outer "
                   "labels, in English and local languages."},
]

COLOURANT_RULE = {
    "subject": "COLOURANTS (ALL)", "clause": "Rule 39(1) / Tenth Schedule",
    "status": "restricted", "max_concentration": None,
    "conditions": "Only dyes, colours and pigments specified by the Bureau of Indian "
                  "Standards in IS 4707 (Part 1) / IS 4707 (Part 2), as amended, and "
                  "listed in the Tenth Schedule, may be used.",
}


def verify_against_pdf() -> list[str]:
    """Re-parse the gazette and report drift from the hand-verified constants."""
    warnings: list[str] = []
    if not PDF.exists():
        return [f"PDF not present at {PDF}; constants used unverified"]
    import pdfplumber

    with pdfplumber.open(PDF) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    for serial, form, is_no, _ in NINTH:
        num = is_no.split()[1]
        # the gazette writes "IS :6608", "IS: 10284", "IS 14318" — all variants
        if not re.search(rf"IS\s*[:\s]\s*{num}\b", text):
            warnings.append(f"Ninth Schedule row {serial} ({form}): {is_no} not found in PDF")
    found_is = set(re.findall(r"IS\s*[:\s]\s*(\d{3,5})\b", text))
    ours = {n.split()[1] for _, _, n, _ in NINTH}
    for extra in sorted(found_is - ours - {"4707", "4011"}):
        warnings.append(f"PDF mentions IS {extra} which is not in the hand-verified table")
    return warnings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-verify", action="store_true")
    a = ap.parse_args()

    if not a.skip_verify:
        for w in verify_against_pdf():
            print(f"  ~ drift: {w}")

    spine = lib.load_yaml("category_map.yaml")["spine"]
    rules: list[dict] = []

    # Fourth Schedule -> categories. Written as rules rows too so that the PERMITS
    # vocabulary and its provenance live in one place; load_neo4j mints the nodes.
    for serial, name, code in FOURTH:
        assert code in spine, f"Fourth Schedule row {serial} maps to unknown spine code {code}"
        rules.append({
            "source_id": SOURCE, "source_key": f"fourth:{serial}", "tier": TIER,
            "jurisdiction": "IN", "source_doc": DOC, "clause": "Fourth Schedule",
            "rule_scope": "category", "subject": code, "applies_to_group": False,
            "status": "listed_category", "max_concentration": None,
            "conditions": None, "standard_ref": None,
            "payload": lib.jsonb({"serial": serial, "category_name": name,
                                  "vocab": "CDSCO Fourth Schedule",
                                  "category_code": f"IN4S_{serial:02d}"}),
        })

    # Ninth Schedule -> category-scoped standards
    for serial, form, is_no, code in NINTH:
        assert code in spine, f"Ninth Schedule row {serial} maps to unknown spine code {code}"
        rules.append({
            "source_id": SOURCE, "source_key": f"ninth:{serial}", "tier": TIER,
            "jurisdiction": "IN", "source_doc": DOC, "clause": "Ninth Schedule",
            "rule_scope": "category", "subject": code, "applies_to_group": False,
            "status": "standard", "max_concentration": None,
            "conditions": f"{form} in finished form shall conform to {is_no} as laid down "
                          f"by the Bureau of Indian Standards.",
            "standard_ref": is_no,
            "payload": lib.jsonb({"serial": serial, "finished_form": form,
                                  "standard_contents_available": False,
                                  "note": "The schedule gives the pointer; the standard "
                                          "itself must be purchased from BIS."}),
        })

    # ingredient-scoped India rules
    for i, r in enumerate(INGREDIENT_RULES + [COLOURANT_RULE], 1):
        rules.append({
            "source_id": SOURCE, "source_key": f"ingredient:{i}", "tier": TIER,
            "jurisdiction": "IN", "source_doc": DOC, "clause": r["clause"],
            "rule_scope": "ingredient", "subject": r["subject"],
            "applies_to_group": r["subject"].endswith(")") or "ALL" in r["subject"],
            "status": r["status"], "max_concentration": r["max_concentration"],
            "conditions": r["conditions"], "standard_ref": None,
            "payload": lib.jsonb({}),
        })

    n = lib.upsert("raw_rule", rules, "source_id, source_key")

    # spec §6/S5: BOTH rule shapes must be expressible — assert it, don't assume it.
    cat = sum(1 for r in rules if r["rule_scope"] == "category")
    ing = sum(1 for r in rules if r["rule_scope"] == "ingredient")
    assert cat and ing, "both rule shapes must be present"
    print(f"S5 done: {n} India rules written — {cat} category-scoped, {ing} ingredient-scoped")
    print(f"  category spine: {len(spine)} codes, {len(FOURTH)} Fourth Schedule entries, "
          f"{len(NINTH)} BIS standards")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
