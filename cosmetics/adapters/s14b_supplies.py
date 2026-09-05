"""S14b — ingredient supply claims from already-landed company pages.  Tier 3.

Closes the gap behind golden query 2 ("Indian suppliers of Niacinamide"), which returns
the ingredient but zero suppliers because no source in the register carries the
Company -> Ingredient edge. A raw-material supplier's own product pages do carry it.

Runs OFFLINE over `raw/s14_sites/` — no refetching. S14 lands pages; this reads them.
That separation means the matcher can be retuned and rerun for free.

Matching discipline, because a naive substring sweep over 36k INCI names against open-web
prose produces garbage:
  * an INCI name is only used as a needle if it is long enough or multi-token to be
    unambiguous — "MILK", "SAND", "CORN" and "HONEY" are real INCI names and also
    ordinary English, so they are excluded rather than guessed at;
  * matches must fall on a word boundary;
  * a page must yield at least MIN_HITS distinct ingredients before ANY of them is kept.
    A genuine supplier lists a catalogue; a single stray token is noise.
Everything kept carries the sentence it was found in.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib  # noqa: E402

SOURCE = "s14b_supplies"
TIER = 3
MIN_HITS = 3            # distinct ingredients before a page is believed at all
MIN_LEN_SINGLE = 9      # single-token INCI must be at least this long
MIN_LEN_MULTI = 7

# Single-token INCI names that are also ordinary English or generic chemistry, and would
# match prose. Excluded outright rather than resolved by guessing (spec §7.4.4).
AMBIGUOUS = {
    "WATER", "AQUA", "MILK", "SAND", "CORN", "HONEY", "SALT", "SUGAR", "WINE", "BEER",
    "COFFEE", "TEA", "RICE", "WHEAT", "OATS", "SOY", "EGG", "CREAM", "BUTTER", "CLAY",
    "CHALK", "MICA", "TALC", "SILICA", "IRON", "ZINC", "COPPER", "SILVER", "GOLD",
    "CARBON", "SULFUR", "SULPHUR", "ALCOHOL", "PERFUME", "PARFUM", "FLAVOR", "FLAVOUR",
    "PEARL", "AMBER", "MUSK", "ROSE", "MINT", "BASIL", "PEPPER", "GINGER", "GARLIC",
    "LEMON", "ORANGE", "APPLE", "GRAPE", "OLIVE", "COCOA", "VANILLA", "ALOE", "HENNA",
    "NEEM", "TULSI", "AMLA", "KAOLIN", "STARCH", "PROTEIN", "COLLAGEN", "KERATIN",
    "GELATIN", "LANOLIN", "BEESWAX", "PARAFFIN", "GLYCERIN", "GLYCERIN.", "MENTHOL",
    "CAMPHOR", "UREA", "ACID", "SODA", "LIME", "SOAP", "OIL", "WAX", "GUM", "RESIN",
}
MODE_HINTS = {
    "manufacturer": ["WE MANUFACTURE", "MANUFACTURER OF", "OUR MANUFACTURING",
                     "MANUFACTURING FACILITY", "WE PRODUCE"],
    "agent": ["DISTRIBUTOR", "AUTHORISED AGENT", "AUTHORIZED AGENT", "TRADING",
              "IMPORTER", "SOLE AGENT", "STOCKIST"],
}


def load_needles() -> dict[str, str]:
    with lib.pg() as c, c.cursor() as cur:
        cur.execute("SELECT DISTINCT inci_name FROM raw_ingredient "
                    "WHERE source_id='s06_cosing' AND inci_name IS NOT NULL")
        names = [r[0] for r in cur.fetchall()]
    out: dict[str, str] = {}
    for n in names:
        if n in AMBIGUOUS or "(" in n or len(n) > 60:
            continue
        multi = " " in n
        if len(n) < (MIN_LEN_MULTI if multi else MIN_LEN_SINGLE):
            continue
        out[n] = n
    return out


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


def page_text(path: Path) -> str:
    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="ignore"), "lxml")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    return soup.get_text(" ", strip=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-hits", type=int, default=MIN_HITS)
    a = ap.parse_args()

    needles = load_needles()
    print(f"  {len(needles)} unambiguous INCI needles "
          f"(from 35.9k; short/ambiguous names excluded)")
    idx = site_index()
    print(f"  {len(idx)} scraped hosts mapped to companies")

    pages = sorted((lib.RAW / "s14_sites").glob("*.html"))
    print(f"  {len(pages)} landed pages")

    per_host: dict[str, dict[str, str]] = defaultdict(dict)
    host_mode: dict[str, str] = {}
    for p in pages:
        host = p.name.split("_")[0].replace("www.", "").lower()
        if host not in idx:
            continue
        text = page_text(p)
        upper = " " + re.sub(r"[^A-Z0-9 ]", " ", text.upper()) + " "
        for inci in needles:
            if f" {inci} " in upper:
                if inci not in per_host[host]:
                    m = re.search(r"[^.!?\n]{0,160}" + re.escape(inci) + r"[^.!?\n]{0,160}",
                                  text, re.I)
                    per_host[host][inci] = re.sub(r"\s+", " ", m.group(0))[:300] if m else ""
        if host not in host_mode:
            up = text.upper()
            for mode, hints in MODE_HINTS.items():
                if any(h in up for h in hints):
                    host_mode[host] = mode
                    break

    rows, kept_hosts = [], 0
    for host, hits in per_host.items():
        if len(hits) < a.min_hits:
            continue
        kept_hosts += 1
        meta = idx[host]
        for inci, evidence in hits.items():
            rows.append({
                "source_id": SOURCE, "source_key": host, "entity_id": meta["entity_id"],
                "inci_name": inci, "matched_text": inci, "evidence": evidence,
                "mode": host_mode.get(host), "tier": TIER, "retrieved_at": lib.now(),
            })
    n = lib.upsert("raw_supplies", rows, "source_id, source_key, inci_name")
    dropped = len(per_host) - kept_hosts
    print(f"S14b done: {n} supply claims across {kept_hosts} suppliers "
          f"({dropped} host(s) dropped for fewer than {a.min_hits} distinct ingredients)")
    if rows:
        top = defaultdict(int)
        for r in rows:
            top[r["inci_name"]] += 1
        print("  most-offered: " + ", ".join(
            f"{k} ({v})" for k, v in sorted(top.items(), key=lambda kv: -kv[1])[:8]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
