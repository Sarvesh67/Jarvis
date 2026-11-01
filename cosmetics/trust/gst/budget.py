"""The budget guard (TRUST_PLAN §4.4): a monthly and a daily cap over a spend ledger.

When a cap is hit the system serves cached facts with their dates and says "verification
paused for today". It never fabricates. Caps are env-configurable; the defaults are the
plan's starting budget.
"""
from __future__ import annotations

import os


def caps() -> tuple[float, float]:
    return (float(os.environ.get("COSMETICS_GST_MONTHLY_CAP_INR", 5000)),
            float(os.environ.get("COSMETICS_GST_DAILY_CAP_INR", 500)))


def allow(cur, cost_inr: float) -> tuple[bool, str | None]:
    if cost_inr <= 0:
        return True, None
    monthly, daily = caps()
    cur.execute("""SELECT coalesce(sum(cost_inr) FILTER (WHERE at >= date_trunc('month', now())), 0),
                          coalesce(sum(cost_inr) FILTER (WHERE at >= date_trunc('day', now())), 0)
                   FROM verification_spend""")
    month, day = (float(x) for x in cur.fetchone())
    if month + cost_inr > monthly:
        return False, "verification paused: the monthly budget is spent"
    if day + cost_inr > daily:
        return False, "verification paused for today"
    return True, None


def record(cur, *, provider, provider_class, call, gstin=None, entity_id=None, trigger=None,
           cost_inr=0.0, ok=True) -> None:
    cur.execute("""INSERT INTO verification_spend (provider, provider_class, call, gstin,
                   entity_id, trigger, cost_inr, ok) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (provider, provider_class, call, gstin, entity_id, trigger, cost_inr, ok))
