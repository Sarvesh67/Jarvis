"""The three facets beside the grade (TRUST_PLAN §6.3): what the grade doesn't answer.

  licence    may it make THIS category?   always per category; consent/EC is a ceiling
  activity   is it operating now?         every item expires (18 months; fairs 12)
  track      has a buyer dealt with it?   first-party only, so unknown for everyone today

Each has a state — verified (tier 1) · claimed (tier 2-3) · unknown · adverse — and the
line a buyer reads. Unknown is not failed: the default lines say so.
"""
from __future__ import annotations

from datetime import date

FACETS = ("licence", "activity", "track")
UNKNOWN = {
    "licence": "No licence on file in the sources we hold",
    "activity": "No recent activity on record",
    "track": "No completed order recorded yet",
}


def facet(rows: list[dict], name: str, today: date | None = None) -> dict:
    today = today or date.today()
    live = [r for r in rows if r["facet"] == name and r["counts"]
            and (r["expiry_at"] is None or r["expiry_at"] >= today)]
    if not live:
        return {"state": "unknown", "note": UNKNOWN[name]}
    live.sort(key=lambda r: (r["tier"] or 9, -(r["observed_at"] or date.min).toordinal()))
    best = live[0]
    return {"state": "verified" if best["tier"] == 1 else "claimed",
            "note": best["public_text"] or UNKNOWN[name]}


def all_facets(rows: list[dict], today: date | None = None) -> dict[str, dict]:
    return {f: facet(rows, f, today) for f in FACETS}


def public_lines(rows: list[dict]) -> list[str]:
    """Every public fact as "facet: text", in reading order, for the evidence drawer."""
    order = {f: i for i, f in enumerate(("identity", "adverse", "licence", "activity", "track"))}
    seen, out = set(), []
    for r in sorted(rows, key=lambda r: (order[r["facet"]], r["tier"] or 9)):
        if r["visibility"] == "public" and r["public_text"]:
            line = f"{r['facet']}: {r['public_text']}"
            if line not in seen:
                seen.add(line)
                out.append(line)
    return out
