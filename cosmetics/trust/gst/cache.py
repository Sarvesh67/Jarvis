"""Freshness per purpose, and one call per GSTIN at a time (TRUST_PLAN §4.2-§4.3).

Stale-while-revalidate: past its TTL a fact is still shown, with its date, and never as
"verified". `single_flight` holds an in-process lock and a session-level Postgres advisory
lock (lib.pg() is autocommit, so a transaction-level one would release at once), so two
tabs opening the same supplier pay once.
"""
from __future__ import annotations

import threading
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

# How old a status may be before this trigger re-checks it. `claim` always checks.
TTL = {
    "render": timedelta(days=30), "batch": timedelta(days=30), "contact": timedelta(days=30),
    "detail": timedelta(days=7), "shortlist": timedelta(days=7), "free_check": timedelta(days=7),
    "rfq": timedelta(hours=24), "claim": timedelta(0),
}
NEGATIVE_TTL = timedelta(days=30)          # "not found" stops repeated paid misses
_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)


def latest(cur, gstin: str, classes: tuple[str, ...]) -> dict | None:
    cur.execute("""SELECT id, status, legal_name, trade_name, provider, provider_class,
                          verified_at, name_match, state_match
                   FROM gst_record WHERE gstin = %s AND provider_class = ANY(%s)
                   ORDER BY verified_at DESC LIMIT 1""", (gstin, list(classes)))
    row = cur.fetchone()
    if not row:
        return None
    return dict(zip([d[0] for d in cur.description], row))


def fresh(rec: dict | None, trigger: str, now: datetime | None = None) -> bool:
    if not rec:
        return False
    now = now or datetime.now(timezone.utc)
    ttl = NEGATIVE_TTL if rec["status"] == "not_found" else TTL[trigger]
    return now - rec["verified_at"] < ttl


@contextmanager
def single_flight(cur, gstin: str):
    with _locks[gstin]:
        cur.execute("SELECT pg_advisory_lock(hashtext(%s))", (gstin,))
        try:
            yield
        finally:
            cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (gstin,))
