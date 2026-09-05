"""S14 — company websites: contact, identifiers, certifications, capabilities.

Tier 2 where the page states the company's own claim (and the sentence is quoted as
evidence); tier 4 where anything is inferred. NEVER tier 1 (spec §6/S14).

This adapter does three jobs at once:

  1. CONTACT — email, phone, address, pincode. The coverage table's contact row is 0%
     because no structured source carries it; the company's own site does.
  2. GSTIN HARVEST — rung 2 of the trust acquisition ladder (spec §9): "company
     websites — footers, contact, terms, invoice templates". Every candidate is
     CHECKSUM-VALIDATED before it is kept, because a regex alone matches PAN-shaped
     noise and a wrong GSTIN poisons the resolution spine.
  3. CAPABILITY / CERTIFICATION — what the factory says it can do, with the sentence
     it said it in.

Extraction is deterministic (regex + keyword with quoted evidence) rather than LLM:
every claim carries the exact sentence that supports it, the pass is free and repeatable,
and re-running cannot change what a company was recorded as claiming. An LLM pass can be
layered on later for the long-tail prose — `evidence` is already the right shape for it.

Politeness: robots.txt is honoured per host, one request per host at a time, and a
short page budget per company. This is a compliance product; its provenance chain should
not contain anything a site owner asked us not to fetch.
"""
from __future__ import annotations

import argparse
import re
import sys
import urllib.robotparser as robotparser
from collections import defaultdict
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib  # noqa: E402

SOURCE = "s14_sites"
PATHS = ["", "/about", "/about-us", "/contact", "/contact-us", "/products",
         "/infrastructure", "/manufacturing", "/facilities", "/capabilities",
         "/quality", "/certifications"]

GSTIN_RE = re.compile(r"\b[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][A-Z0-9]Z[A-Z0-9]\b")
CIN_RE = re.compile(r"\b[LU][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}\b")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(r"(?:\+91[\s-]?)?\b[6-9]\d{9}\b")
PIN_RE = re.compile(r"\b[1-9][0-9]{5}\b")
VOLUME_RE = re.compile(r"\b(\d{1,5})\s?(ml|ML|gm|GM|g|kg|KG|ltr|litre|liter|L)\b")
MOQ_RE = re.compile(r"\b(?:MOQ|minimum order(?:\s+quantity)?)\D{0,20}(\d[\d,]*)\s*"
                    r"(pcs|pieces|units|kg|ltr|litres|nos)?", re.I)

CERTIFICATIONS = {
    "WHO-GMP": ["WHO GMP", "WHO-GMP", "WHO CGMP"],
    "GMP": ["GMP CERTIFIED", "GOOD MANUFACTURING PRACTICE", "SCHEDULE M"],
    "ISO 22716": ["ISO 22716", "ISO22716", "COSMETIC GMP"],
    "ISO 9001": ["ISO 9001", "ISO9001"],
    "ISO 14001": ["ISO 14001"],
    "GMPC": ["GMPC"],
    "HALAL": ["HALAL"],
    "KOSHER": ["KOSHER"],
    "ECOCERT": ["ECOCERT"],
    "COSMOS": ["COSMOS ORGANIC", "COSMOS NATURAL"],
    "USFDA": ["US FDA", "USFDA", "FDA REGISTERED", "FDA APPROVED"],
    "CRUELTY-FREE": ["CRUELTY FREE", "CRUELTY-FREE", "PETA CERTIFIED"],
    "AYUSH": ["AYUSH LICENSE", "AYUSH LICENCE", "AYUSH GMP"],
    "HACCP": ["HACCP"],
}

CAPABILITIES = {
    "third_party_manufacturing": ["THIRD PARTY MANUFACTUR", "THIRD-PARTY MANUFACTUR",
                                  "CONTRACT MANUFACTUR", "PRIVATE LABEL", "WHITE LABEL",
                                  "OEM", "ODM", "LOAN LICENC", "LOAN LICENS"],
    "cream_lotion": ["CREAM", "LOTION", "MOISTURIS", "MOISTURIZ", "EMULSION"],
    "serum_gel": ["SERUM", "GEL "],
    "liquid_filling": ["LIQUID FILLING", "BOTTLE FILLING", "FILLING LINE"],
    "powder": ["POWDER", "TALC", "COMPACT"],
    "soap_bar": ["SOAP", "BATHING BAR", "SAPONIF"],
    "shampoo_hair": ["SHAMPOO", "HAIR OIL", "CONDITIONER", "HAIR SERUM"],
    "colour_cosmetics": ["LIPSTICK", "NAIL POLISH", "NAIL ENAMEL", "KAJAL",
                         "EYELINER", "MASCARA", "FOUNDATION"],
    "oral_care": ["TOOTHPASTE", "TOOTH POWDER", "MOUTHWASH"],
    "sachet_tube": ["SACHET", "TUBE FILLING", "LAMI TUBE", "ALUMINIUM TUBE"],
    "aerosol": ["AEROSOL", "PROPELLANT"],
    "rnd_formulation": ["R&D", "RESEARCH AND DEVELOPMENT", "FORMULATION SUPPORT",
                        "IN-HOUSE LAB", "OWN LABORATORY"],
    "export": ["EXPORT", "WE EXPORT", "EXPORTING TO"],
}

B36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def gstin_valid(g: str) -> bool:
    """GSTIN check digit (base-36, alternating weights 1/2).

    Cheap, and it is what separates a real GSTIN from a PAN-shaped string that happens
    to sit next to two digits. An unvalidated GSTIN entering the resolution spine merges
    two unrelated companies.
    """
    if len(g) != 15 or any(c not in B36 for c in g):
        return False
    total = 0
    for i, c in enumerate(g[:14]):
        v = B36.index(c) * (1 if i % 2 == 0 else 2)
        total += v // 36 + v % 36
    return B36[(36 - total % 36) % 36] == g[14]


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if 15 < len(s.strip()) < 400]


def evidence_for(sents: list[str], needles: list[str]) -> str | None:
    for s in sents:
        up = s.upper()
        if any(n in up for n in needles):
            return re.sub(r"\s+", " ", s)[:300]
    return None


class Robots:
    def __init__(self):
        self._cache: dict[str, robotparser.RobotFileParser | None] = {}

    def allowed(self, url: str, fetcher: lib.Fetcher) -> bool:
        host = urlparse(url).netloc
        if host not in self._cache:
            rp = robotparser.RobotFileParser()
            try:
                r = fetcher.get(urljoin(url, "/robots.txt"))
                rp.parse(r.text.splitlines())
            except Exception:
                rp = None                    # no robots.txt => no restriction stated
            self._cache[host] = rp
        rp = self._cache[host]
        return True if rp is None else rp.can_fetch(lib.UA, url)


_JUNK_HOST = re.compile(r"^(?:[-_.x*m]+|\d+)$", re.I)


def normalize_site(url: str | None) -> str | None:
    """Reject unresolvable junk before it costs a DNS timeout.

    Directory 'website' fields are free text. Defence in depth: S9 filters at ingest,
    this filters at use, so a bad value from any future source cannot burn the budget.
    """
    if not url:
        return None
    url = url.strip().strip("'\"")
    if not url or url in {"-", "--", "NA", "N/A"}:
        return None
    if not url.startswith(("http://", "https://")):
        url = "https://" + url.lstrip("/")
    p = urlparse(url)
    host = (p.netloc or "").lower().split(":")[0]
    if not host or "." not in host or _JUNK_HOST.match(host.replace(".", "")):
        return None
    _, _, tld = host.rpartition(".")
    if len(tld) < 2 or not tld.isalpha() or len(set(host.replace(".", ""))) <= 2:
        return None
    return f"{p.scheme}://{host}"


def targets(limit: int, only_cosmetics: bool) -> list[dict]:
    where = "r.website IS NOT NULL AND r.website <> ''"
    if only_cosmetics:
        where += (" AND (r.roles_claimed && ARRAY['cosmetics_name_candidate',"
                  "'cosmetics_core','cosmetics_active','manufacturer_candidate']::text[])")
    sql = f"""
        SELECT DISTINCT ON (r.website) x.entity_id, r.source_id, r.source_key,
               r.legal_name, r.website, r.roles_claimed
        FROM raw_company r
        JOIN crosswalk x ON x.source_id = r.source_id AND x.source_key = r.source_key
         AND x.entity_type = 'company' AND x.superseded_by IS NULL
        WHERE {where}
        ORDER BY r.website, r.tier
        LIMIT {limit}
    """
    with lib.pg() as c, c.cursor() as cur:
        cur.execute(sql)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]


STOP_TOKENS = {"PRIVATE", "LIMITED", "PVT", "LTD", "LLP", "INDIA", "INDIAN", "CO",
               "COMPANY", "AND", "THE", "INDS", "INTL", "ENT", "PROD", "CHEM", "LABS"}


def name_on_site(legal_name: str | None, text_upper: str) -> bool:
    """Does the company's own name actually appear on the site it is listed against?

    Directories list a group or parent site against a subsidiary (CHEMEXCIL lists
    BOMBAY PERFUMERY against aacipl.com, which is genuine, not a parse error). A GSTIN
    lifted from the wrong company's site would merge two unrelated entities, so anything
    identifier-shaped is only promoted to a spine field when the name checks out.
    """
    toks = [t for t in re.sub(r"[^A-Z0-9 ]", " ", (legal_name or "").upper()).split()
            if len(t) > 2 and t not in STOP_TOKENS]
    if not toks:
        return False
    hits = sum(1 for t in toks if t in text_upper)
    return hits >= max(1, min(2, len(toks)))


def scrape(company: dict, f: lib.Fetcher, robots: Robots, max_pages: int) -> dict | None:
    base = normalize_site(company["website"])
    if not base:
        return None
    text_parts, fetched = [], []
    for i, path in enumerate(PATHS[:max_pages]):
        url = base + path
        try:
            if not robots.allowed(url, f):
                continue
            r = f.get(url)
        except Exception:
            if i == 0:
                return None      # home page unreachable -> the host is dead, stop here
            continue
        if "text/html" not in r.headers.get("content-type", ""):
            continue
        lib.land(SOURCE, url, r.content,
                 key=f"{urlparse(base).netloc}{path.replace('/', '_') or '_home'}")
        soup = BeautifulSoup(r.text, "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text_parts.append(soup.get_text(" ", strip=True))
        fetched.append(url)
    if not text_parts:
        return None

    text = "\n".join(text_parts)
    sents = sentences(text)
    upper = text.upper()

    gstins = sorted({g for g in GSTIN_RE.findall(upper) if gstin_valid(g)})
    cins = sorted(set(CIN_RE.findall(upper)))
    emails = sorted({e.lower() for e in EMAIL_RE.findall(text)
                     if not e.lower().endswith((".png", ".jpg", ".gif", ".webp"))})
    phones = sorted({re.sub(r"\D", "", p)[-10:] for p in PHONE_RE.findall(text)})
    pins = PIN_RE.findall(text)

    certs = {}
    for name, needles in CERTIFICATIONS.items():
        ev = evidence_for(sents, needles)
        if ev:
            certs[name] = ev
    caps = {}
    for name, needles in CAPABILITIES.items():
        ev = evidence_for(sents, needles)
        if ev:
            caps[name] = ev

    volumes = sorted({f"{n} {u.lower()}" for n, u in VOLUME_RE.findall(text)})[:20]
    moq = MOQ_RE.search(text)

    return {
        "base": base, "pages_fetched": fetched,
        "name_on_site": name_on_site(company.get("legal_name"), upper),
        "gstins": gstins, "cins": cins, "emails": emails[:5], "phones": phones[:5],
        "pincode": pins[-1] if pins else None,
        "certifications": certs, "capabilities": caps,
        "fill_volumes": volumes,
        "moq": moq.group(0)[:80] if moq else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=300, help="spec §4/S14: top N companies")
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--max-pages", type=int, default=6)
    ap.add_argument("--timeout", type=float, default=12.0)
    ap.add_argument("--all-roles", action="store_true",
                    help="do not restrict to cosmetics-relevant companies")
    a = ap.parse_args()

    cands = targets(a.limit, not a.all_roles)
    print(f"  {len(cands)} companies with a website to visit")
    # Retry policy for the open web, not for a rate-limited API: a dead or parked
    # company site must cost seconds, not the 8-attempt / 900s backoff the gateway
    # adapters need. There are thousands of these and many will not resolve.
    f = lib.Fetcher(SOURCE, delay=a.delay, timeout=a.timeout,
                    max_attempts=2, backoff_base=2.0, backoff_max=8.0)
    robots = Robots()
    rows, stats = [], defaultdict(int)
    try:
        for i, co in enumerate(cands, 1):
            try:
                res = scrape(co, f, robots, a.max_pages)
            except Exception as exc:
                print(f"    [{i}/{len(cands)}] {co['legal_name'][:40]}: {exc}")
                res = None
            if not res:
                stats["unreachable"] += 1
                continue
            stats["scraped"] += 1
            stats["gstin"] += bool(res["gstins"])
            stats["gstin_confirmed"] += bool(res["gstins"] and res["name_on_site"])
            stats["name_on_site"] += bool(res["name_on_site"])
            stats["contact"] += bool(res["emails"] or res["phones"])
            stats["certs"] += bool(res["certifications"])
            stats["caps"] += bool(res["capabilities"])
            rows.append({
                "source_id": SOURCE, "source_key": res["base"], "raw_path": "",
                "retrieved_at": lib.now(),
                # tier 2: the company's own claim about itself, sentence quoted
                "tier": 2,
                "legal_name": co["legal_name"],
                "website": res["base"],
                # identifiers are promoted to spine fields ONLY when the site is
                # confirmed to be this company's own; otherwise they stay in payload
                "gstin": res["gstins"][0] if (res["gstins"] and res["name_on_site"]) else None,
                "cin": res["cins"][0] if (res["cins"] and res["name_on_site"]) else None,
                "email": res["emails"][0] if res["emails"] else None,
                "phone": res["phones"][0] if res["phones"] else None,
                "pincode": res["pincode"],
                "roles_claimed": (["third_party_manufacturer"]
                                  if "third_party_manufacturing" in res["capabilities"] else []),
                "payload": lib.jsonb({
                    "linked_entity_id": co["entity_id"],
                    "name_on_site": res["name_on_site"],
                    "gstins_all": res["gstins"], "cins_all": res["cins"],
                    "emails": res["emails"], "phones": res["phones"],
                    "certifications": res["certifications"],   # {name: quoted sentence}
                    "capabilities": res["capabilities"],       # {name: quoted sentence}
                    "fill_volumes": res["fill_volumes"], "moq": res["moq"],
                    "pages_fetched": res["pages_fetched"],
                    "extraction": "deterministic_keyword_with_quoted_evidence",
                }),
            })
            if i % 10 == 0:
                print(f"    [{i}/{len(cands)}] scraped {stats['scraped']}, "
                      f"{stats['gstin']} GSTIN, {stats['contact']} contact", flush=True)
    finally:
        f.close()

    n = lib.upsert("raw_company", rows, "source_id, source_key")
    print(f"S14 done: {n} company sites")
    print(f"  reachable {stats['scraped']}/{len(cands)}, unreachable {stats['unreachable']}")
    print(f"  name confirmed on site {stats['name_on_site']}/{stats['scraped']}")
    print(f"  checksum-valid GSTIN {stats['gstin']} "
          f"({stats['gstin_confirmed']} on a name-confirmed site -> promoted to the spine)")
    print(f"  contact {stats['contact']} | certifications {stats['certs']} | "
          f"capabilities {stats['caps']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
