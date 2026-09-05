"""S9 — CHEMEXCIL (Basic Chemicals, Cosmetics & Dyes Export Promotion Council).  Tier 2.

Unique contribution (spec §4/S9): EXPORT ACTIVITY. MCA will not tell you a company
exports; council membership does. It also covers proprietorships and partnerships that
MCA cannot see.

The member directory enumerates completely at /members-alpha/{A-Z,0-9} — roughly 4,000
members, two columns per row: company name (linking to a profile) and website. It is the
largest free source of Indian chemical/cosmetics company WEBSITES in the build, which is
what makes S14 (capability extraction from company sites) possible at all.

Two limits, stated rather than worked around:
  * Member PROFILES require a council login ("Details are available only to Chemexcil
    members"), so address, contact person, panel and membership type are not available.
    Membership TYPE is what would let us split manufacturer-exporter from merchant-
    exporter and assign the trader role directly, so that split is not made here — the
    role recorded is `exporter`, which is what the directory actually evidences.
  * The council spans all basic chemicals, not just cosmetics. Every member is landed;
    a cosmetics-relevance flag is computed from the name, and nothing downstream treats
    council membership as a cosmetics claim.

Do NOT parse the /members.html search results for this: that page wraps the matched term
in markup, splitting company names mid-word. The alpha pages are clean.
"""
from __future__ import annotations

import argparse
import re
import string
import sys
from pathlib import Path

from bs4 import BeautifulSoup
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib  # noqa: E402

SOURCE = "s09_chemexcil"
TIER = 2
BASE = "https://chemexcil.in/members-alpha/{}"
KEYS = list(string.ascii_uppercase) + [str(d) for d in range(10)]

COSMETICS_HINTS = (
    "COSMETIC", "COSMO", "BEAUTY", "PERSONAL CARE", "SKIN", "HAIR", "PERFUM",
    "FRAGRANC", "AROMA", "ESSENTIAL OIL", "TOILETRIES", "TOILETRY", "SOAP",
    "AYURVED", "HERBAL", "SURFACTANT", "EMULSI", "DERMA", "SHAMPOO", "OLEO",
)


# Members type whatever they like into the website field and the council linkifies it
# verbatim, so the column contains "-", "----------", "XXXXXXXXXX", "MMMMMMMMMMM" and
# bare phone numbers alongside real URLs. Storing those poisons S14, which would then
# spend its budget resolving nonexistent hosts.
_JUNK_HOST = re.compile(r"^(?:[-_.x*m]+|\d+)$", re.I)


def clean_website(raw: str | None) -> str | None:
    if not raw:
        return None
    v = raw.strip().strip("'\"")
    if not v or v in {"-", "--", "NA", "N/A", "nil", "none"}:
        return None
    if not v.startswith(("http://", "https://")):
        v = "https://" + v.lstrip("/")
    host = (urlparse(v).netloc or "").lower().split(":")[0]
    if not host or "." not in host or _JUNK_HOST.match(host.replace(".", "")):
        return None
    label, _, tld = host.rpartition(".")
    if not label or len(tld) < 2 or not tld.isalpha():
        return None
    if len(set(host.replace(".", ""))) <= 2:      # "----------", "XXXXXXXXXX"
        return None
    return f"{urlparse(v).scheme}://{host}"


def parse(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    out: list[dict] = []
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        name_a = tds[0].find("a", href=True)
        if not name_a or "viewprofile" not in name_a["href"]:
            continue
        name = re.sub(r"\s+", " ", name_a.get_text(" ", strip=True)).strip()
        if not name:
            continue
        site_a = tds[1].find("a", href=True)
        website = clean_website(site_a["href"]) if site_a else None
        if website and "chemexcil.in" in website:
            website = None
        m = re.search(r"/viewprofile/(\d+)/", name_a["href"])
        out.append({"name": name, "website": website,
                    "member_id": m.group(1) if m else None,
                    "profile_url": name_a["href"]})
    return out


def to_rows(members: list[dict]) -> list[dict]:
    rows, seen = [], set()
    for m in members:
        key = m["member_id"] or m["name"]
        if key in seen:
            continue
        seen.add(key)
        up = m["name"].upper()
        cosmetics = any(h in up for h in COSMETICS_HINTS)
        rows.append({
            "source_id": SOURCE, "source_key": key, "raw_path": "",
            "retrieved_at": lib.now(), "tier": TIER,
            "legal_name": m["name"], "website": m["website"],
            # what the directory actually evidences: the company exports and is a
            # council member. NOT that it manufactures, and NOT that it is cosmetics.
            "roles_claimed": ["exporter", "chemexcil_member"] +
                             (["cosmetics_name_candidate"] if cosmetics else []),
            "payload": lib.jsonb({
                "association": "CHEMEXCIL",
                "association_full": "Basic Chemicals, Cosmetics & Dyes Export "
                                    "Promotion Council",
                "member_id": m["member_id"], "profile_url": m["profile_url"],
                "cosmetics_relevant_by_name": cosmetics,
                "membership_type": None,   # profile pages are members-only
            }),
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay", type=float, default=1.5)
    ap.add_argument("--keys", nargs="*", default=KEYS)
    a = ap.parse_args()

    f = lib.Fetcher(SOURCE, delay=a.delay)
    members: list[dict] = []
    try:
        for k in a.keys:
            url = BASE.format(k)
            try:
                r = f.get(url)
            except Exception as exc:
                print(f"  ! {k}: {exc}")
                continue
            lib.land(SOURCE, url, r.content, key=f"members_alpha_{k}")
            got = parse(r.text)
            members.extend(got)
            print(f"  {k}: {len(got)} members ({len(members)} total)", flush=True)
    finally:
        f.close()

    rows = to_rows(members)
    n = lib.upsert("raw_company", rows, "source_id, source_key")
    sites = sum(1 for r in rows if r["website"])
    cos = sum(1 for r in rows if "cosmetics_name_candidate" in r["roles_claimed"])
    print(f"S9 done: {n} CHEMEXCIL members — {sites} with a website "
          f"({100*sites/max(n,1):.0f}%), {cos} cosmetics-relevant by name")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
