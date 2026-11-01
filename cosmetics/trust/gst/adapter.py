"""The provider interface, normalised to GSTN's own public `searchTaxpayer` schema.

Every vendor answers in its own shape; each provider adapter maps it onto `Taxpayer`, so
nothing downstream knows which vendor spoke. Field names are GSTN's (TRUST_PLAN §2.3).
Fields GSTN shows only after login — turnover, compliance rating — do not exist here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Protocol

B36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
CLASSES = ("live_gstn", "vendor_cache", "stub")


@dataclass
class Taxpayer:
    gstin: str
    lgnm: str | None = None                 # legal name
    tradeNam: str | None = None             # trade name
    ctb: str | None = None                  # constitution of business
    sts: str | None = None                  # Active | Suspended | Cancelled | Inactive
    rgdt: str | None = None                 # registration date, dd/mm/yyyy
    cxdt: str | None = None                 # cancellation date
    dty: str | None = None                  # taxpayer type: Regular | Composition | ...
    nba: list[str] = field(default_factory=list)   # nature of business activities
    pradr: dict = field(default_factory=dict)      # principal place: {addr, pncd, stcd}
    adadr: list[dict] = field(default_factory=list)
    einvoiceStatus: str | None = None       # "Yes": turnover >= Rs 5 Cr in some year since 2017-18
    lstupdt: str | None = None

    def to_json(self) -> dict:
        return asdict(self)


class ProviderError(Exception):
    """The provider could not answer. Not-found is an answer, and is returned as None."""


class Provider(Protocol):
    name: str
    provider_class: str                     # one of CLASSES
    cost_verify_inr: float
    cost_search_inr: float

    def verify(self, gstin: str) -> Taxpayer | None: ...

    def search_by_name(self, name: str, state_code: str | None) -> list[Taxpayer]: ...


def check_char(first14: str) -> str:
    """GSTIN check character: base 36, alternating weights 1 and 2."""
    total = 0
    for i, c in enumerate(first14):
        v = B36.index(c) * (1 if i % 2 == 0 else 2)
        total += v // 36 + v % 36
    return B36[(36 - total % 36) % 36]
