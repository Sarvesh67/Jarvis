"""S1 — CDSCO SUGAM approved manufacturing sites.  Tier 1 (regulator).

Reconnaissance result (differs from the spec's assumption of an opaque form-POST):
the listing is a plain server-rendered JSP that accepts `page_no` as a GET parameter
once a JSESSIONID exists, so no DevTools payload capture is needed. The page carries
its own row count in a hidden field (`num_total_records`), which we use as the
completeness check rather than trusting the page-number select.

Columns: Sr.No | License No | Premise Name | Loan Premises Name | Premise Address |
         Site Type (Own/Loan) | Form No | Issue Date | Expiry Date

Form numbers are the pre-Cosmetics-Rules-2020 Drugs & Cosmetics Rules forms:
  Form 32   -> licence to MANUFACTURE COSMETICS for sale      => regime cosmetics, COS-8 successor
  Form 32-A -> LOAN licence to manufacture cosmetics          => regime cosmetics, COS-9 successor
  Form 25/28/… -> drug manufacturing licences                 => regime drugs (kept, tagged, not
                 conflated: a drug site is still a real facility and a real address)

Emits: raw_facility (facility + licence) and a raw_company row for the firm name.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib  # noqa: E402

SOURCE = "s01_cdsco"
TIER = 1
BASE = "https://cdscoonline.gov.in/CDSCO"
ROOT_URL = f"{BASE}/manuf_site"
LIST_URL = f"{BASE}/app_srv/cdsco/global/jsp/Approved_Manuf_Site.jsp"

PIN_RE = re.compile(r"\b[1-9][0-9]{5}\b")


def _form_meta(form_no: str, site_type: str) -> tuple[str, str, bool]:
    """(form_type, regime, is_loan_licence)"""
    f = (form_no or "").upper().replace(" ", "").replace(".", "")
    loan = "LOAN" in (site_type or "").upper() or f.endswith("32A") or f.endswith("32-A")
    if "32" in f:
        return ("COS-9" if loan else "COS-8"), "cosmetics", loan
    if any(x in f for x in ("25", "28", "29", "27")):
        return form_no.strip().upper(), "drugs", loan
    return (form_no or "OTHER").strip().upper(), "other", loan


# The register carries obvious test rows ("Testing123", "abc") left by portal users.
# They arrive tier 1 and would otherwise be indistinguishable from a real licence.
JUNK_NAME_RE = re.compile(r"^(test|testing|abc|xyz|asd|qwe|demo|sample)[\W\d]*$", re.I)


def is_junk_name(name: str) -> bool:
    n = (name or "").strip()
    return not n or len(n) < 3 or bool(JUNK_NAME_RE.match(n))


def _roles(regime: str, loan: bool) -> list[str]:
    """Roles a CDSCO row may legitimately claim.

    On a LOAN row the register names two firms but does NOT reliably say which one
    owns the site: state FDAs fill 'Premise Name' / 'Loan Premises Name' in opposite
    conventions (Pure&Cure->Dr.Reddy's puts the factory first; GSK->Encube puts it
    second, and P&G Health appears on BOTH sides in different rows). So neither party
    may claim `manufacturer` off a loan row -- we record the pairing and stay silent
    on direction. Regime is stamped onto the role so a DRUG licence can never satisfy
    a cosmetics-manufacturer question (907 of the 911 rows are drugs).
    """
    if loan:
        return ["loan_licence_party", f"{regime}_loan_licence_party"]
    return ["manufacturer", f"{regime}_manufacturer"]


# "Loan Premises Name" is free text. Observed shapes:
#   "-"                                              -> absent
#   "M/s. Goa Antibiotics & Pharmaceuticals Ltd."    -> plain, needs M/s. stripped
#   "Merck Specialities Pvt Ltd C/o ATRA Pharma..."  -> firm, then its host after C/o
#   "C/o M/s.Pure & Cure Healthcare (P) Ltd.,"       -> leading C/o
#   "Cipla Limited, L-139 to L-146, Verna Ind Est"   -> firm, then a postal address
ADDR_SEG_RE = re.compile(r"^(plot|survey|sy|khasra|gat|shed|unit|block|sector|phase|no\b|"
                         r"[a-z]?-?\d)", re.I)
MS_RE = re.compile(r"^m/s[.\s]+", re.I)
COF_RE = re.compile(r"\s+c/o\s+", re.I)


def _counterparty(raw: str | None) -> str | None:
    """Firm name only: drop the host-of clause and any postal address tail."""
    n = (raw or "").strip().strip(",.")
    if not n or n in {"-", "--"}:
        return None
    n = re.sub(r"^c/o\s+", "", n, flags=re.I)     # leading "C/o M/s.Foo"
    n = COF_RE.split(n)[0]                         # "Foo C/o Bar" -> "Foo"
    keep = []
    for seg in n.split(","):
        seg = seg.strip()
        if not seg:
            continue
        if keep and ADDR_SEG_RE.match(seg):        # address tail begins
            break
        keep.append(seg)
    n = MS_RE.sub("", ", ".join(keep)).strip().strip(",.")
    return None if is_junk_name(n) else n


def _date(s: str):
    s = (s or "").strip()
    for fmt in ("%d-%b-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_page(html: str) -> tuple[list[dict], int, int]:
    soup = BeautifulSoup(html, "lxml")
    total = int((soup.find("input", {"name": "num_total_records"}) or {}).get("value") or 0)
    pages = int((soup.find("input", {"name": "num_pages"}) or {}).get("value") or 0)
    rows = []
    for tr in soup.find_all("tr"):
        tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(tds) < 9 or not tds[0].isdigit():
            continue
        sr, lic, premise, loan_premise, addr, site_type, form_no, issued, expires = tds[:9]
        rows.append({
            "sr": sr, "licence_no": lic, "premise": premise, "loan_premise": loan_premise,
            "address": addr, "site_type": site_type, "form_no": form_no,
            "issued": issued, "expires": expires,
        })
    return rows, total, pages


def to_rows(records: list[dict]) -> tuple[list[dict], list[dict]]:
    facilities, companies, skipped = [], [], []
    for r in records:
        # address tail is ",<district>,<state>,India,<phone>,<phone>"
        parts = [p.strip() for p in (r["address"] or "").split(",")]
        district = state = phone = None
        if "India" in parts:
            i = parts.index("India")
            state = parts[i - 1] if i >= 1 else None
            district = parts[i - 2] if i >= 2 else None
            tail = [p for p in parts[i + 1:] if p.isdigit()]
            phone = tail[0] if tail else None
        pins = PIN_RE.findall(r["address"] or "")
        pincode = pins[-1] if pins else None

        form_type, regime, loan = _form_meta(r["form_no"], r["site_type"])
        firm = (r["premise"] or "").strip()
        if is_junk_name(firm):
            skipped.append(firm)
            continue
        key = f"{r['licence_no']}|{firm}"[:200]
        facilities.append({
            "source_id": SOURCE, "source_key": key, "raw_path": "",
            "retrieved_at": lib.now(), "tier": TIER,
            "company_source_key": firm,
            "firm_name": firm,
            "address_raw": r["address"], "pincode": pincode, "state": state,
            "licence_form": form_type, "licence_no": r["licence_no"],
            "licence_status": "unknown",           # source shows no status field (spec §6 S1/S2)
            "granted_on": _date(r["issued"]), "expires_on": _date(r["expires"]),
            "is_loan_licence": loan,
            "loan_premises_name": _counterparty(r["loan_premise"]),
            "categories": [],
            "district": district,
            "payload": lib.jsonb({"regime": regime, "site_type": r["site_type"],
                                  "form_no_raw": r["form_no"], "sr": r["sr"], "phone": phone}),
        })
        companies.append({
            "source_id": SOURCE, "source_key": firm, "raw_path": "",
            "retrieved_at": lib.now(), "tier": TIER,
            "legal_name": firm, "address_raw": r["address"], "pincode": pincode,
            "state": state, "phone": phone,
            "roles_claimed": _roles(regime, loan),
            "payload": lib.jsonb({"regime": regime, "licence_no": r["licence_no"]}),
            "district": district,
        })
        counterparty = _counterparty(r["loan_premise"])
        if loan and counterparty:
            # Deliberately NO address/pincode/district here. The row's address is the
            # licensed premises; the counterparty may sit somewhere else entirely
            # (Meditab@Kundaim paired with Cipla@Verna), and copying it would invent a
            # second firm at an address it has never been observed at -- which then
            # corroborates a false merge in resolve.py. State is kept: a loan licence
            # is issued by one State Licensing Authority and binds both parties.
            companies.append({
                "source_id": SOURCE, "source_key": counterparty, "raw_path": "",
                "retrieved_at": lib.now(), "tier": TIER,
                "legal_name": counterparty, "address_raw": None,
                "pincode": None, "state": state, "phone": None,
                "roles_claimed": _roles(regime, True),
                "payload": lib.jsonb({"regime": regime, "loan_counterparty_of": firm,
                                      "licence_no": r["licence_no"],
                                      "direction_known": False}),
                "district": None,
            })
    # Dedupe on source_key, but UNION the roles rather than keeping the first row's.
    # A firm can appear as the named premise on an OWN row and as the counterparty on
    # a LOAN row; roles[] is additive (hard rule 1), so dropping the later row whole
    # would silently discard a tier-1 claim.
    by_key: dict[str, dict] = {}
    for c in companies:
        if not c["source_key"]:
            continue
        prev = by_key.get(c["source_key"])
        if prev is None:
            by_key[c["source_key"]] = c
            continue
        prev["roles_claimed"] = sorted(set(prev["roles_claimed"]) | set(c["roles_claimed"]))
        for fld in ("address_raw", "pincode", "district", "phone"):
            prev[fld] = prev.get(fld) or c.get(fld)
    if skipped:
        print(f"  skipped {len(skipped)} junk firm name(s): {sorted(set(skipped))[:5]}")
    return facilities, list(by_key.values())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay", type=float, default=3.0, help="spec S1: 1 req / 3s")
    ap.add_argument("--max-pages", type=int, default=100)
    ap.add_argument("--replay", action="store_true",
                    help="re-parse already-landed pages instead of refetching CDSCO")
    a = ap.parse_args()

    if a.replay:
        records = []
        for path in sorted((lib.RAW / SOURCE).glob("approved_manuf_site_p*.html")):
            rows, _, _ = parse_page(path.read_text(encoding="utf-8", errors="replace"))
            records.extend(rows)
        print(f"  replayed {len(records)} rows from landed pages")
        return _emit(records)

    f = lib.Fetcher(SOURCE, delay=a.delay)
    try:
        f.get(ROOT_URL)                       # establish JSESSIONID
        records: list[dict] = []
        page, pages, total = 1, 1, 0
        while page <= min(pages, a.max_pages):
            r = f.get(LIST_URL, params={"page_no": page})
            lib.land(SOURCE, str(r.url), r.content, key=f"approved_manuf_site_p{page}")
            rows, total, pages = parse_page(r.text)
            records.extend(rows)
            print(f"  page {page}/{pages}: +{len(rows)} rows ({len(records)}/{total})", flush=True)
            if not rows:
                break
            page += 1
        if total and len(records) != total:
            print(f"  ! collected {len(records)} but page reports {total} total")
    finally:
        f.close()

    return _emit(records)


def _emit(records: list[dict]) -> int:
    facilities, companies = to_rows(records)
    n1 = lib.upsert("raw_facility", facilities, "source_id, source_key")
    n2 = lib.upsert("raw_company", companies, "source_id, source_key")
    cos = sum(1 for x in facilities if x["licence_form"].startswith("COS"))
    pairs = sum(1 for x in facilities if x["is_loan_licence"] and x["loan_premises_name"])
    print(f"S1 done: {n1} facilities ({cos} cosmetics-regime, {pairs} loan pairs), "
          f"{n2} companies")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
