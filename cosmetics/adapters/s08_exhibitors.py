"""S8 — Indian cosmetics trade-fair exhibitor lists.  Tier 2 (company-declared).

Why this source carries weight (spec §4/S8): an exhibitor paid for a booth. That single
fact proves the company is alive, actively selling into cosmetics NOW, and looking for
customers — which MCA's tens of thousands of rows, many dormant, cannot tell you. It also
covers proprietorships that MCA misses entirely.

Role labels here are a MARKETING choice by the organiser, not a regulatory fact, so the
exhibit category lands in `roles_claimed[]` as an ADDITIVE tier-2 claim (hard rule 11) and
never as a node label or a licence assertion.

Page shapes vary by organiser; the two seen so far are paired two-column tables
("Company Name | Booth | Company Name | Booth"), so the parser walks cells in pairs
rather than assuming a fixed column index. Editions are declared in EVENTS; adding a new
edition is a one-line change, and --url ingests an ad-hoc page.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib  # noqa: E402

SOURCE = "s08_exhibitors"
TIER = 2

EVENTS = [
    {"name": "HPCI India", "edition": "Mumbai", "year": 2026, "city": "Mumbai",
     "url": "https://hpci-india.com/exhibitor-list"},
    {"name": "Cosmetics Ingredients Expo", "edition": "Chennai", "year": 2025, "city": "Chennai",
     "url": "https://cosmeticsingredientsexpo.com/chennai/list-of-exhibitors/"},
    {"name": "Cosmetics Ingredients Expo", "edition": "Mumbai", "year": 2025, "city": "Mumbai",
     "url": "https://cosmeticsingredientsexpo.com/mumbai/list-of-exhibitors/"},
    {"name": "Cosmetics Ingredients Expo", "edition": "Delhi", "year": 2025, "city": "Delhi",
     "url": "https://cosmeticsingredientsexpo.com/delhi/list-of-exhibitors/"},
]

BOOTH_RE = re.compile(r"^[A-Z]{0,3}[\s-]?\d{1,4}[A-Z]?$", re.I)
NAME_NOISE = {"company name", "booth no.", "booth no", "stall number", "stall no",
              "exhibitor", "sr.no.", "sr no", "s.no", ""}


def looks_like_booth(s: str) -> bool:
    return bool(s) and len(s) <= 8 and bool(BOOTH_RE.match(s.strip()))


def parse(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    out: list[dict] = []
    for tr in soup.find_all("tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
        cells = [re.sub(r"\s+", " ", c).strip() for c in cells]
        if not cells or all(c.lower() in NAME_NOISE for c in cells):
            continue
        # walk in (name, booth) pairs; tolerate a trailing odd cell
        i = 0
        while i < len(cells):
            name = cells[i]
            booth = cells[i + 1] if i + 1 < len(cells) else ""
            if name.lower() in NAME_NOISE or len(name) < 3 or looks_like_booth(name):
                i += 1
                continue
            out.append({"name": name, "booth": booth if looks_like_booth(booth) else None})
            i += 2 if looks_like_booth(booth) or booth.lower() in NAME_NOISE or not booth else 1
    return out


def to_rows(ev: dict, exhibitors: list[dict]) -> list[dict]:
    rows = []
    for e in exhibitors:
        name = e["name"]
        rows.append({
            "source_id": SOURCE,
            "source_key": f"{ev['name']}|{ev['edition']}|{ev['year']}|{name}"[:200],
            "raw_path": "", "retrieved_at": lib.now(), "tier": TIER,
            "legal_name": name, "trade_name": name,
            "city": ev.get("city"),
            # ingredient/raw-material fairs => the exhibitor is selling INTO cosmetics.
            # Additive claim only; it asserts activity, never a licence.
            "roles_claimed": ["cosmetics_active", "exhibitor"],
            "payload": lib.jsonb({"event": ev["name"], "edition": ev["edition"],
                                  "year": ev["year"], "booth": e.get("booth"),
                                  "event_url": ev["url"]}),
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="ad-hoc exhibitor page")
    ap.add_argument("--name", default="ad-hoc"); ap.add_argument("--edition", default="-")
    ap.add_argument("--year", type=int, default=0); ap.add_argument("--city", default=None)
    ap.add_argument("--delay", type=float, default=2.0)
    a = ap.parse_args()

    events = ([{"name": a.name, "edition": a.edition, "year": a.year,
                "city": a.city, "url": a.url}] if a.url else EVENTS)

    f = lib.Fetcher(SOURCE, delay=a.delay)
    all_rows: list[dict] = []
    seen_pages: set[str] = set()
    try:
        for ev in events:
            try:
                r = f.get(ev["url"])
            except Exception as exc:
                print(f"  ! {ev['name']} {ev['edition']}: {exc}")
                continue
            ex_preview = parse(r.text)
            # Fingerprint the PARSED LIST, not the HTML: this organiser serves one identical
            # exhibitor list at /chennai/, /mumbai/ and /delhi/, but the markup carries a nonce.
            fingerprint = "|".join(sorted(e["name"] for e in ex_preview))
            lib.land(SOURCE, ev["url"], r.content,
                     key=f"{ev['name']}_{ev['edition']}_{ev['year']}".replace(" ", "_"))
            ex = ex_preview
            dup = " (identical list to a previous edition — organiser serves one list; skipped)" \
                if fingerprint in seen_pages else ""
            seen_pages.add(fingerprint)
            print(f"  {ev['name']} {ev['edition']} {ev['year']}: {len(ex)} exhibitors{dup}")
            if dup:
                continue
            all_rows.extend(to_rows(ev, ex))
    finally:
        f.close()

    n = lib.upsert("raw_company", all_rows, "source_id, source_key")
    print(f"S8 done: {n} exhibitor company rows from {len(events)} edition(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
