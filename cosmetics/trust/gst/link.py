"""When a name-search result may be linked to a firm (TRUST_PLAN §3, rung 3).

Auto-link ONLY when the normalised legal or trade name matches ours AND the principal
place's pincode is one of ours. One of the two goes to review; neither is rejected. A
wrong link would pin another firm's GST status on this one — the worst error this layer
can make — so the rule is strict and the false-link rate is audited before it is relaxed.
"""
from __future__ import annotations

from typing import Callable

from trust.gst.adapter import Taxpayer, check_char


def decide(c: Taxpayer, names: set[str], pincodes: set[str],
           norm: Callable[[str | None], str | None]) -> tuple[str, str]:
    g = c.gstin or ""
    if len(g) != 15 or check_char(g[:14]) != g[14]:
        return "reject", "checksum fails"
    name_ok = bool(({norm(c.lgnm), norm(c.tradeNam)} - {None}) & names)
    place_ok = (c.pradr or {}).get("pncd") in pincodes
    if name_ok and place_ok:
        return "auto_link", "name and principal-place pincode both match"
    if name_ok:
        return "review", "name matches; principal place does not"
    if place_ok:
        return "review", "principal place matches; name does not"
    return "reject", "neither name nor place matches"
