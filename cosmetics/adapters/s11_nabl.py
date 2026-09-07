"""S11 — NABL accredited testing laboratories.  Tier 1 (the accreditation register).

Answers a real buyer question the rest of the graph cannot: "who can TEST this, and are
they accredited to do it?" The Ninth Schedule says a category must conform to an IS
standard; a NABL-accredited lab is who certifies that it does. This is the compliance
path's last hop.

THE SEARCH IS AN ASP.NET WEBFORMS APP with three traps worth recording:

  1. `ddlcountry=1` is AFGHANISTAN, not India. India is 101. With the wrong country the
     state filter is silently ignored and every query returns the unfiltered national set
     — which looks like success and is not.
  2. The public results grid renders at most 100 rows regardless of the reported count,
     and there is no pager (the Excel export link is `display:none`). So the only way to
     enumerate is to narrow filters until each bucket is under 100 — state first, then
     city for the dense states. Buckets still over 100 are reported as TRUNCATED.
  3. Each response embeds its result set in __VIEWSTATE, so chaining searches grows the
     viewstate to ~20 MB and every subsequent POST uploads it. Fetching a FRESH form per
     query keeps it near 100 KB — the difference between minutes and hours.

DPDP: the grid carries Contact Person, Email and Mobile. Those are personal data and go to
the `person` table with a consent basis, never to raw_company and never to Neo4j
(hard rule 4). Only the lab's institutional identity reaches the graph.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
import time
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib  # noqa: E402

SOURCE = "s11_nabl"
TIER = 1
URL = "https://nablwp.qci.org.in/laboratorysearchone"
INDIA = "101"
LAB_TESTING = "1"
STATUS_ACCREDITED = "2"
RENDER_CAP = 100

# NABL's actual Group/Activity vocabulary, read off the register rather than guessed.
# The earlier list included "CHEMICAL" and "DRUGS AND PHARMACEUTICAL", which is how a
# CENTRAL FORENSIC SCIENCE LABORATORY ended up flagged as a cosmetics testing lab.
# Scope accreditations that actually cover cosmetics (185 + 95 + 13 labs nationally):
COSMETIC_GROUPS = ("COSMETICS & ESSENTIAL OILS", "COSMETICS AND ESSENTIAL OILS",
                   "SOAP DETERGENT & TOILETRIES", "AYUSH PRODUCTS")
# Adjacent capability — a pharma-accredited lab often CAN test a cosmetic, but the
# accreditation does not say so. Kept as a separate, weaker claim.
ADJACENT_GROUPS = ("DRUGS & PHARMACEUTICALS", "DRUGS AND PHARMACEUTICALS")
# The two disciplines the spec asks for.
RELEVANT_DISCIPLINES = ("CHEMICAL", "BIOLOGICAL")


def _hid(name: str, html: str) -> str:
    m = re.search(r'name="%s"[^>]*value="([^"]*)"' % name, html)
    return m.group(1) if m else ""


def _state(html: str) -> dict:
    return {"__VIEWSTATE": _hid("__VIEWSTATE", html),
            "__VIEWSTATEGENERATOR": _hid("__VIEWSTATEGENERATOR", html),
            "__EVENTVALIDATION": _hid("__EVENTVALIDATION", html)}


def _options(html: str, ddl: str) -> list[tuple[str, str]]:
    m = re.search(r'<select[^>]*name="[^"]*%s"[^>]*>(.*?)</select>' % ddl, html, re.S)
    if not m:
        return []
    return [(v, t.strip()) for v, t in
            re.findall(r'<option[^>]*value="([^"]*)"[^>]*>([^<]*)</option>', m.group(1))
            if v not in ("", "0")]


class Nabl:
    """Fresh form per query — see trap 3 in the module docstring."""

    def __init__(self, f: lib.Fetcher):
        self.f = f

    def _fresh(self) -> str:
        return self.f.get(URL).text

    def _india(self, html: str) -> str:
        d = {**_state(html), "__EVENTTARGET": "ctl00$MainContent$ddlcountry",
             "__EVENTARGUMENT": "", "ctl00$MainContent$ddlcountry": INDIA,
             "ctl00$MainContent$ddlLabType": LAB_TESTING,
             "ctl00$MainContent$ddlLabStatus": STATUS_ACCREDITED}
        return self.f.post(URL, data=d).text

    def states(self) -> list[tuple[str, str]]:
        return _options(self._india(self._fresh()), "ddlstate")

    def state_form(self, state_id: str) -> str:
        """Form HTML with the city dropdown POPULATED for this state.

        A city id posted against a form whose city control was never cascaded is not in
        that control's valid list, so ASP.NET discards it and the search silently returns
        the unfiltered state result. That failure is invisible — it looks like the city
        simply had no labs — and it cost 93 wasted requests before it was spotted.
        """
        html = self._india(self._fresh())
        d = {**_state(html), "__EVENTTARGET": "ctl00$MainContent$ddlstate",
             "__EVENTARGUMENT": "", "ctl00$MainContent$ddlcountry": INDIA,
             "ctl00$MainContent$ddlstate": state_id,
             "ctl00$MainContent$ddlLabType": LAB_TESTING,
             "ctl00$MainContent$ddlLabStatus": STATUS_ACCREDITED}
        return self.f.post(URL, data=d).text

    def cities(self, state_form_html: str) -> list[tuple[str, str]]:
        return _options(state_form_html, "ddlcity")

    def disciplines(self, form_html: str) -> list[tuple[str, str]]:
        """ddlDiscipline is NOT cascaded — its options are present on the base form."""
        return _options(form_html, "ddlDiscipline")

    def search(self, state_id: str, city_id: str | None = None,
               form_html: str | None = None,
               discipline_id: str | None = None) -> tuple[int, list[dict]]:
        """One search. `form_html` lets a caller reuse a cascaded form across many city
        queries instead of re-cascading (2 extra requests) for each one."""
        html = form_html or self._india(self._fresh())
        d = {**_state(html), "__EVENTTARGET": "", "__EVENTARGUMENT": "",
             "ctl00$MainContent$ddlcountry": INDIA,
             "ctl00$MainContent$ddlstate": state_id,
             "ctl00$MainContent$ddlLabType": LAB_TESTING,
             "ctl00$MainContent$ddlLabStatus": STATUS_ACCREDITED,
             "ctl00$MainContent$btnSearch": "Search"}
        if city_id:
            d["ctl00$MainContent$ddlcity"] = city_id
        if discipline_id:
            d["ctl00$MainContent$ddlDiscipline"] = discipline_id
        s = self.f.post(URL, data=d).text
        m = re.search(r'lblcount">(\d+)<', s)
        total = int(m.group(1)) if m else 0
        return total, parse_rows(s)


def parse_rows(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", id="dynamic-table1")
    if not table:
        return []
    trs = table.find_all("tr")
    if not trs:
        return []
    head = [th.get_text(" ", strip=True) for th in trs[0].find_all(["th", "td"])]
    out = []
    for tr in trs[1:]:
        cells = [re.sub(r"\s+", " ", td.get_text(" ", strip=True)) for td in tr.find_all("td")]
        if len(cells) < len(head) - 4:
            continue
        out.append(dict(zip(head, cells)))
    return out


def to_rows(rec: dict, state_label: str) -> tuple[dict | None, dict | None]:
    """(company_row, person_row). Person data NEVER enters the company row."""
    name = (rec.get("CAB Name") or "").strip()
    if not name:
        return None, None
    cab = (rec.get("CAB Id") or "").strip().upper()
    disciplines = [d.strip().upper() for d in (rec.get("Discipline/Category") or "").split(",") if d.strip()]
    groups_raw = rec.get("Group/Activity(s)") or ""
    groups = [g.strip() for g in groups_raw.split(",") if g.strip()]
    up = groups_raw.upper()
    matched = sorted({k for k in COSMETIC_GROUPS if k in up})
    tests_cosmetics = bool(matched)
    tests_adjacent = any(k in up for k in ADJACENT_GROUPS)
    relevant = any(d in RELEVANT_DISCIPLINES for d in disciplines)
    cert_raw = (rec.get("Certificate Number") or "").split(" View")[0].strip()
    addr = (rec.get("Address") or "").strip()
    pins = re.findall(r"\b[1-9][0-9]{5}\b", addr)
    key = cab or hashlib.blake2b(f"{name}|{addr[:60]}".encode(), digest_size=8).hexdigest()

    company = {
        "source_id": SOURCE, "source_key": key, "raw_path": "",
        "retrieved_at": lib.now(), "tier": TIER,
        "legal_name": name,
        "address_raw": addr or None,
        "pincode": (rec.get("Pincode") or "").strip() or (pins[-1] if pins else None),
        "state": (rec.get("State") or "").strip() or state_label,
        "city": (rec.get("City") or "").strip() or None,
        # a lab is not a manufacturer; the role says exactly what it is
        "roles_claimed": (["testing_lab"]
                          + (["cosmetics_testing"] if tests_cosmetics else [])
                          + (["pharma_testing"] if tests_adjacent else [])),
        "payload": lib.jsonb({
            "cab_id": cab,
            "accreditation": "NABL",
            "standard": (rec.get("Standard") or "").strip(),
            "certificate_number": cert_raw,
            "certificate_issued": (rec.get("Certificate Issue Date") or "").strip(),
            "certificate_valid_till": (rec.get("Certificate Valid Till") or "").strip(),
            "disciplines": disciplines,
            # Store enough scope to AUDIT the flag. Capping at 12 silently hid the
            # matching scope for 166 labs whose cosmetics group sat further down the
            # list: the flag was right but unverifiable from the stored payload, which
            # in a compliance product is the same as not having the evidence.
            "groups": groups[:40],
            "groups_matched": matched,
            "tests_cosmetics": tests_cosmetics,
            "tests_adjacent_pharma": tests_adjacent,
            "discipline_relevant": relevant,
        }),
    }
    person = None
    contact = (rec.get("Contact Person") or "").strip()
    email = (rec.get("Email") or "").strip()
    mobile = (rec.get("Mobile") or "").strip()
    if contact or email or mobile:
        person = {"name": contact or None, "role": "NABL contact person",
                  "email": email or None, "phone": mobile or None,
                  "source_id": SOURCE, "retrieved_at": lib.now(),
                  "consent_basis": "published in the NABL public accreditation register",
                  "lab_key": key}
    return company, person


def reclassify() -> int:
    """Recompute the cosmetics flags from stored `groups` without touching the network."""
    with lib.pg() as c, c.cursor() as cur:
        cur.execute("SELECT source_key, payload, roles_claimed FROM raw_company "
                    "WHERE source_id=%s", (SOURCE,))
        rows = cur.fetchall()
        changed = 0
        for key, payload, roles in rows:
            groups_raw = ", ".join(payload.get("groups") or [])
            up = groups_raw.upper()
            matched = sorted({k for k in COSMETIC_GROUPS if k in up})
            # never downgrade a flag set from the FULL scope text by re-reading a
            # truncated copy of it; only upgrade
            cos = bool(matched) or bool(payload.get("tests_cosmetics"))
            adj = any(k in up for k in ADJACENT_GROUPS)
            new_roles = (["testing_lab"] + (["cosmetics_testing"] if cos else [])
                         + (["pharma_testing"] if adj else []))
            if payload.get("tests_cosmetics") == cos and sorted(roles or []) == sorted(new_roles):
                continue
            payload["tests_cosmetics"] = cos
            payload["tests_adjacent_pharma"] = adj
            if matched:
                payload["groups_matched"] = matched
            cur.execute("UPDATE raw_company SET payload=%s, roles_claimed=%s "
                        "WHERE source_id=%s AND source_key=%s",
                        (lib.jsonb(payload), new_roles, SOURCE, key))
            changed += 1
    print(f"S11 reclassify: {changed} of {len(rows)} rows updated")
    with lib.pg() as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FILTER (WHERE 'cosmetics_testing' = ANY(roles_claimed)), "
                    "count(*) FILTER (WHERE 'pharma_testing' = ANY(roles_claimed)), count(*) "
                    "FROM raw_company WHERE source_id=%s", (SOURCE,))
        cos, adj, tot = cur.fetchone()
    print(f"  {cos} accredited for cosmetics, {adj} for pharma (adjacent), {tot} labs total")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reclassify", action="store_true",
                    help="recompute flags from stored groups; no network")
    ap.add_argument("--states", nargs="*", help="state labels; default every Indian state")
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--split-cities", action="store_true", default=True,
                    help="split a state that exceeds the 100-row render cap")
    a = ap.parse_args()
    if a.reclassify:
        return reclassify()

    f = lib.Fetcher(SOURCE, delay=a.delay, timeout=180.0,
                    max_attempts=3, backoff_base=5.0, backoff_max=40.0)
    n = Nabl(f)
    t0 = time.time()
    try:
        states = n.states()
        if a.states:
            want = {s.lower() for s in a.states}
            states = [s for s in states if s[1].lower() in want]
        print(f"  {len(states)} state(s) to sweep")

        companies: dict[str, dict] = {}
        persons: list[dict] = []
        truncated: list[str] = []
        for sid, label in states:
            total, rows = n.search(sid)
            if total > RENDER_CAP and a.split_cities:
                got = len(rows)
                form = n.state_form(sid)          # cascade ONCE, reuse for every city
                cities = n.cities(form)
                print(f"  {label}: {total} labs > {RENDER_CAP} cap — splitting "
                      f"{len(cities)} cities", flush=True)
                seen_keys = set()
                discs = n.disciplines(form)
                for cid, clabel in cities:
                    ctotal, crows = n.search(sid, cid, form_html=form)
                    for cr in crows:
                        k = (cr.get("CAB Id") or cr.get("CAB Name") or "").strip()
                        if k and k not in seen_keys:
                            seen_keys.add(k)
                            rows.append(cr)
                    if ctotal <= RENDER_CAP:
                        continue
                    # a metro city can exceed the render cap on its own (Bengaluru 303).
                    # Discipline is the last available axis and is not cascaded, so it
                    # costs one request per discipline and no extra round trips.
                    before = len(seen_keys)
                    for did, dlabel in discs:
                        _, drows = n.search(sid, cid, form_html=form, discipline_id=did)
                        for dr in drows:
                            k = (dr.get("CAB Id") or dr.get("CAB Name") or "").strip()
                            if k and k not in seen_keys:
                                seen_keys.add(k)
                                rows.append(dr)
                    recovered = len(seen_keys) - before
                    print(f"    {label}/{clabel}: {ctotal} > cap, discipline split "
                          f"recovered {recovered} more", flush=True)
                    if len(seen_keys) < ctotal:
                        truncated.append(f"{label}/{clabel} ({ctotal}, got "
                                         f"{len(seen_keys)})")
                print(f"  {label}: {got} -> {len(rows)} rows after city split "
                      f"(of {total} reported)", flush=True)
            elif total > RENDER_CAP:
                truncated.append(f"{label} ({total})")
                print(f"  {label}: {total} labs, only {len(rows)} rendered (TRUNCATED)",
                      flush=True)
            else:
                print(f"  {label}: {total} labs, {len(rows)} parsed", flush=True)
            for rec in rows:
                comp, per = to_rows(rec, label)
                if comp and comp["source_key"] not in companies:
                    companies[comp["source_key"]] = comp
                    if per:
                        persons.append(per)
    finally:
        f.close()

    rows = list(companies.values())
    written = lib.upsert("raw_company", rows, "source_id, source_key")
    # Person rows land in `person` ONLY (hard rule 4) — no entity_id yet, they attach to
    # the lab after Stage 3 resolves it.
    if persons:
        with lib.pg() as c, c.cursor() as cur:
            # this source is re-runnable; replace its person rows rather than duplicating
            cur.execute("DELETE FROM person WHERE source_id = %s", (SOURCE,))
            cur.executemany(
                "INSERT INTO person (entity_id, name, role, email, phone, source_id, "
                "retrieved_at, consent_basis) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                [(None, p["name"], p["role"], p["email"], p["phone"], p["source_id"],
                  p["retrieved_at"], p["consent_basis"]) for p in persons])
    cos = sum(1 for r in rows if "cosmetics_testing" in r["roles_claimed"])
    print(f"S11 done in {time.time()-t0:.0f}s: {written} accredited testing labs")
    print(f"  {cos} can test cosmetics or their inputs")
    print(f"  {len(persons)} contact persons -> `person` table only (DPDP, hard rule 4)")
    if truncated:
        print(f"  ! still over the {RENDER_CAP}-row render cap: {', '.join(truncated[:8])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
