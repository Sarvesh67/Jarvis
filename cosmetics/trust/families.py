"""Publisher families — who actually published a row (TRUST_PLAN §6.2).

Corroboration is counted by publisher, never by source id. S17 and S17b are both the
Udyam registry, and counting them as two sources once produced 1,090 "corroborated"
firms. A firm's own website is the firm talking about itself, so it never corroborates.

The mapping lives on the source cards (`publisher.family`, `publisher.kind`) so that a
new source declares its publisher where `make catalog-check` can see it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

import yaml

CARDS = Path(__file__).resolve().parents[1] / "catalog" / "sources"

# Shown to buyers in reasons and on the card. A family missing here shows its own key.
DISPLAY = {
    "udyam": "Udyam MSME registry",
    "mca": "MCA company register",
    "cdsco": "CDSCO licence register",
    "state_fda": "state drug-regulator licence list",
    "nabl": "NABL accreditation register",
    "gstn": "GST Network",
    "chemexcil": "CHEMEXCIL member directory",
    "fair": "trade-fair exhibitor list",
    "marketplace": "marketplace listing",
    "self": "the company's own website",
    # SOURCES_PLAN Phase 2-3 publishers, named before their cards exist (TRUST_PLAN §10.4)
    "zed": "ZED certification register",
    "pcb": "pollution control board consent register",
    "ec": "environmental clearance register",
    "cosmos": "COSMOS certification register",
    "association": "industry association directory",
}

# A registry row establishes identity only while it is in force (TRUST_PLAN §1.2, §10.1).
MCA_LIVE = {"Active"}
MCA_DEAD = {"Strike Off": "Struck off", "Dissolved": "Dissolved", "Liquidated": "Liquidated",
            "Converted to LLP and dissolved": "Converted to LLP and dissolved"}


@dataclass(frozen=True)
class Publisher:
    source_id: str          # the raw_company source_id
    family: str
    kind: str               # registry | directory | self | reference
    split_by: str | None


@lru_cache(maxsize=1)
def by_source() -> dict[str, Publisher]:
    """raw_company source_id -> its publisher, from every card that keys raw_company."""
    out: dict[str, Publisher] = {}
    for p in sorted(CARDS.glob("*.yaml")):
        card = yaml.safe_load(p.read_text()) or {}
        sid = (card.get("keys") or {}).get("raw_company")
        pub = card.get("publisher") or {}
        if sid and pub.get("family"):
            out[sid] = Publisher(sid, pub["family"], pub.get("kind") or "reference",
                                 pub.get("family_split_by"))
    return out


def family_of(source_id: str, payload: dict | None) -> str | None:
    """The independent publisher of one row, or None if it can never corroborate."""
    pub = by_source().get(source_id)
    if pub is None or pub.kind in ("self", "reference"):
        return None
    if pub.split_by:
        part = (payload or {}).get(pub.split_by)
        return f"{pub.family}:{part}" if part else pub.family
    return pub.family


def kind_of(source_id: str) -> str | None:
    pub = by_source().get(source_id)
    return pub.kind if pub else None


def display(family: str) -> str:
    base, _, part = family.partition(":")
    name = DISPLAY.get(base, base)
    return f"{name} ({part})" if part else name


def _ddmmyyyy(s: str | None) -> date | None:
    if not s:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def registry_in_force(source_id: str, payload: dict | None, today: date | None = None) -> bool:
    """Does this row still establish that the firm legally exists?

    MCA: only while the company is Active — a struck-off company's CIN once read
    "Legally exists" on 826 cards. NABL: only while the certificate is in date.
    """
    if kind_of(source_id) != "registry":
        return False
    p = payload or {}
    today = today or date.today()
    fam = by_source()[source_id].family
    if fam == "mca":
        return p.get("company_status") in MCA_LIVE
    if fam == "nabl":
        till = _ddmmyyyy(p.get("certificate_valid_till"))
        return till is None or till >= today
    return True
