"""A deterministic stand-in provider (TRUST_PLAN §10.3).

Same GSTIN in, same answer out, so tests and the end-to-end run are repeatable. Names and
principal places are drawn from OUR record (via `lookup`) and then perturbed for a fixed
share of GSTINs, so the name and place checks see real matches and real mismatches. Every
record it returns is `provider_class = "stub"`, and nothing graded ever reads one.
"""
from __future__ import annotations

import hashlib
from datetime import date
from typing import Callable

from trust.gst.adapter import Taxpayer, check_char

CTB = {"C": "Private Limited Company", "F": "Partnership", "P": "Proprietorship",
       "H": "Hindu Undivided Family", "T": "Trust"}


def _h(s: str) -> bytes:
    return hashlib.sha256(s.encode()).digest()


class StubProvider:
    name = "stub"
    provider_class = "stub"
    cost_verify_inr = 0.0
    cost_search_inr = 0.0

    def __init__(self, lookup: Callable[[str], dict | None] = lambda key: None):
        self.lookup = lookup          # GSTIN or name -> {"name", "pincode"} from our record

    def verify(self, gstin: str) -> Taxpayer | None:
        h = _h(gstin)
        if h[0] < 8:                                     # ~3%: not found
            return None
        status = ("Active" if h[1] < 208 else "Suspended" if h[1] < 228
                  else "Cancelled" if h[1] < 244 else "Inactive")
        ours = self.lookup(gstin) or {}
        name = ours.get("name") or f"STUB TAXPAYER {gstin[2:7]}"
        if h[2] < 26:                                    # ~10%: the GST name differs from ours
            name += " TRADERS"
        pincode = ours.get("pincode") or f"{gstin[:2]}{h[3]:04d}"[:6]
        if h[3] < 16:                                    # ~6%: a different principal place
            pincode = str(int(pincode) + 1).zfill(6)
        return Taxpayer(
            gstin=gstin, lgnm=name, tradeNam=name if h[4] % 2 else None,
            ctb=CTB.get(gstin[5], "Others"), sts=status,
            rgdt=f"01/07/{2017 + h[5] % 7}",
            cxdt="01/04/2025" if status == "Cancelled" else None,
            dty="Composition" if h[6] < 32 else "Regular",
            nba=["Manufacturing"], pradr={"addr": "STUB", "pncd": pincode, "stcd": gstin[:2]},
            einvoiceStatus="Yes" if h[7] < 48 else "No", lstupdt=date.today().strftime("%d/%m/%Y"))

    def search_by_name(self, name: str, state_code: str | None) -> list[Taxpayer]:
        sc = state_code or "27"
        h = _h(f"{name}|{sc}")
        ours = self.lookup(name) or {}
        out = []
        for i in range(h[0] % 4):                         # 0-3 candidates
            hh = _h(f"{name}|{sc}|{i}")
            pan = "".join(chr(65 + b % 26) for b in hh[:3]) + "C" + chr(65 + hh[3] % 26) \
                + f"{int.from_bytes(hh[4:6], 'big') % 10000:04d}" + chr(65 + hh[6] % 26)
            g = sc + pan + "1Z"
            g += check_char(g)
            # candidate 0 is the firm itself; 1 a near-miss name elsewhere; 2 unrelated
            nm = name if i == 0 else f"{name} ENTERPRISES" if i == 1 else f"STUB {pan}"
            pin = (ours.get("pincode") if i == 0 and h[1] % 3 else None) or f"{sc}{hh[7]:04d}"[:6]
            out.append(Taxpayer(gstin=g, lgnm=nm, sts="Active", ctb=CTB["C"], dty="Regular",
                                pradr={"addr": "STUB", "pncd": pin, "stcd": sc}))
        return out
