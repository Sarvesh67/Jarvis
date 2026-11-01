"""GST verification for cosmetics firms: the triggers of TRUST_PLAN §4.2 over gst/.

    verifier = Verifier.from_env()        # COSMETICS_GST_PROVIDERS=stub, or nothing
    verifier.on_trigger(entity_id, gstin, "detail")   # waits <= 2.5 s, then "checking…"

The order of a check never changes:
  1. rung 0 (free): checksum, state code against our record, PAN type. A failure blocks
     the paid call. A personal-PAN GSTIN is checked only on a buyer's action, never on
     render (purpose-bound processing of personal data, §4.2).
  2. cache: a status younger than the trigger's TTL is served, with its date.
  3. budget: over a cap, cached facts are served and the check is "paused".
  4. provider, through the router; the record lands in gst_record with its provider class.

With no provider configured nothing is called, and the drawer keeps saying "GST evidence
unavailable — verification is not connected yet". That is the public demo's state, and it
has no Postgres credentials anyway: this runs in the pipeline or an internal service.
"""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError as Timeout
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import lib  # noqa: E402
from pipeline import gstin as G  # noqa: E402
from pipeline.normalize import norm_name  # noqa: E402
from trust.gst import budget, cache, link  # noqa: E402
from trust.gst.adapter import Provider, ProviderError, Taxpayer  # noqa: E402
from trust.gst.router import Router, disagreement  # noqa: E402

# How long the user's action may wait (§4.2). 0 = never waits: queued, badge upgrades later.
WAIT = {"render": 0, "batch": None, "detail": 2.5, "shortlist": 0, "contact": 0,
        "rfq": 10.0, "claim": 10.0, "free_check": 2.5}
BUYER_ACTIONS = {"detail", "shortlist", "contact", "rfq", "free_check", "claim"}
RENDER_LIMIT = 3            # cards per answer that may queue a check
CODE_OF_STATE = {G.norm_state(v): k for k, v in G.STATE_CODES.items() if k not in G.NOT_A_PLACE}


@dataclass
class Outcome:
    gstin: str
    status: str            # active | not_active | not_found | unavailable | blocked | skipped | queued
    reason: str | None = None
    provider_class: str | None = None
    from_cache: bool = False
    record: dict = field(default_factory=dict)


def providers_from_env(lookup) -> list[Provider]:
    names = [n.strip() for n in os.environ.get("COSMETICS_GST_PROVIDERS", "").split(",") if n.strip()]
    out: list[Provider] = []
    for n in names:
        if n == "stub":
            from trust.gst.providers.stub import StubProvider
            out.append(StubProvider(lookup))
        else:
            raise ValueError(f"unknown GST provider {n!r}: add its adapter under trust/gst/providers/")
    return out


def _ours(cur, entity_id: str) -> dict:
    cur.execute("""SELECT r.legal_name, r.norm_name, r.pincode, r.state FROM raw_company r
                   JOIN crosswalk x ON x.source_id = r.source_id AND x.source_key = r.source_key
                    AND x.entity_type = 'company' AND x.superseded_by IS NULL
                   WHERE x.entity_id = %s""", (entity_id,))
    rows = cur.fetchall()
    return {"names": {n for r in rows for n in (r[1], norm_name(r[0])) if n},
            "legal_name": next((r[0] for r in rows if r[0]), None),
            "pincodes": {r[2] for r in rows if r[2]},
            "states": {r[3] for r in rows if r[3]}}


def _lookup(key: str) -> dict | None:
    """What the stub draws names and places from: our own record for a GSTIN or a name."""
    with lib.pg() as c, c.cursor() as cur:
        col = "gstin" if G.GSTIN_RE.match(key or "") else "norm_name"
        cur.execute(f"SELECT legal_name, pincode FROM raw_company WHERE {col} = %s "
                    "AND legal_name IS NOT NULL LIMIT 1",
                    (key if col == "gstin" else norm_name(key),))
        r = cur.fetchone()
    return {"name": r[0], "pincode": r[1]} if r else None


class Verifier:
    def __init__(self, providers: list[Provider], *, shadow_rate: float = 0.02):
        self.router = Router(providers, shadow_rate=shadow_rate) if providers else None
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="gst")

    @classmethod
    def from_env(cls) -> "Verifier":
        return cls(providers_from_env(_lookup))

    # ------------------------------------------------------------------ verify

    def verify(self, entity_id: str, gstin: str, trigger: str) -> Outcome:
        gstin = (gstin or "").strip().upper()
        if self.router is None:
            return Outcome(gstin, "unavailable", "verification is not connected yet")
        with lib.pg() as conn, conn.cursor() as cur:
            ours = _ours(cur, entity_id)
            c0 = G.check(gstin, ours["states"])
            if not (c0["well_formed"] and c0["checksum_ok"]):
                return Outcome(gstin, "blocked", "checksum fails")
            if c0["state_match"] is False:
                return Outcome(gstin, "blocked",
                               f"state code {c0['gst_state']} disagrees with our record")
            if c0["is_personal"] and trigger not in BUYER_ACTIONS:
                return Outcome(gstin, "skipped", "personal-PAN GSTIN: checked only on a buyer's action")

            classes = tuple(p.provider_class for p in self.router.providers)
            with cache.single_flight(cur, gstin):
                rec = cache.latest(cur, gstin, classes)
                if cache.fresh(rec, trigger):
                    return self._outcome(gstin, rec, from_cache=True)
                cost = self.router.providers[0].cost_verify_inr
                ok, why = budget.allow(cur, cost)
                if not ok:
                    return self._outcome(gstin, rec, from_cache=True, reason=why) if rec \
                        else Outcome(gstin, "unavailable", why)
                try:
                    ans = self.router.verify(gstin)
                except ProviderError as e:
                    budget.record(cur, provider="router", provider_class="none", call="verify",
                                  gstin=gstin, entity_id=entity_id, trigger=trigger, ok=False)
                    return Outcome(gstin, "unavailable", f"could not verify right now ({e})")
                p, t = ans.provider, ans.result
                budget.record(cur, provider=p.name, provider_class=p.provider_class, call="verify",
                              gstin=gstin, entity_id=entity_id, trigger=trigger,
                              cost_inr=p.cost_verify_inr)
                rec = self._write(cur, entity_id, gstin, t, p, trigger, ans.latency_ms, ours, c0)
                for sp, st in ans.shadow:
                    self._write(cur, entity_id, gstin, st, sp, "shadow", None, ours, c0,
                                disagree=disagreement(t, st))
                return self._outcome(gstin, rec)

    def _write(self, cur, entity_id, gstin, t: Taxpayer | None, p: Provider, trigger,
               latency_ms, ours, c0, disagree=None) -> dict:
        place = (t.pradr or {}) if t else {}
        name_match = bool(t) and bool(({norm_name(t.lgnm), norm_name(t.tradeNam)} - {None})
                                      & ours["names"])
        state_ok = None
        if t and place.get("stcd") and ours["states"]:
            state_ok = place["stcd"] in {CODE_OF_STATE.get(G.norm_state(s)) for s in ours["states"]}
        payload = t.to_json() if t else {"found": False}
        if disagree is not None:
            payload["_shadow_disagreement"] = disagree
        cur.execute("""
            INSERT INTO gst_record (entity_id, gstin, legal_name, trade_name, constitution, status,
                taxpayer_type, address, pincode, nature_of_business, is_proprietorship, provider,
                verified_at, provider_class, einvoice_enabled, trigger, cost_inr, latency_ms,
                name_match, state_match, payload)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, clock_timestamp(),%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id, status, legal_name, trade_name, provider, provider_class, verified_at,
                      name_match, state_match""",
            (entity_id, gstin, t.lgnm if t else None, t.tradeNam if t else None,
             t.ctb if t else None, t.sts if t else "not_found", t.dty if t else None,
             place.get("addr"), place.get("pncd"), t.nba if t else None,
             c0["is_proprietorship"], p.name, p.provider_class,
             (t.einvoiceStatus == "Yes") if t and t.einvoiceStatus else None, trigger,
             p.cost_verify_inr, latency_ms, name_match if t else None, state_ok,
             json.dumps(payload)))
        return dict(zip([d[0] for d in cur.description], cur.fetchone()))

    @staticmethod
    def _outcome(gstin, rec, from_cache=False, reason=None) -> Outcome:
        st = rec["status"]
        status = "not_found" if st == "not_found" else "active" if st == "Active" else "not_active"
        return Outcome(gstin, status, reason, rec["provider_class"], from_cache, rec)

    # ------------------------------------------------------------------ triggers

    def on_trigger(self, entity_id: str, gstin: str, trigger: str) -> Outcome:
        """Never blocks longer than the trigger allows (§4.2). Cards never wait."""
        wait = WAIT[trigger]
        fut = self.pool.submit(self.verify, entity_id, gstin, trigger)
        if wait is None:
            return fut.result()
        if wait == 0:
            return Outcome(gstin, "queued", "the badge upgrades when the check returns")
        try:
            return fut.result(timeout=wait)
        except Timeout:
            return Outcome(gstin, "queued", "checking…" if trigger != "rfq"
                           else "could not verify right now — send anyway?")

    def on_render(self, cards: list[tuple[str, str]]) -> list[Outcome]:
        """Queue checks for at most RENDER_LIMIT visible cards whose status is stale."""
        if self.router is None:
            return []
        classes = tuple(p.provider_class for p in self.router.providers)
        stale = []
        with lib.pg() as c, c.cursor() as cur:
            for eid, g in cards:
                if not cache.fresh(cache.latest(cur, g, classes), "render"):
                    stale.append((eid, g))
        return [self.on_trigger(eid, g, "render") for eid, g in stale[:RENDER_LIMIT]]

    # ------------------------------------------------------------------ name search

    def search(self, entity_id: str, trigger: str = "detail") -> list[dict]:
        """Rung 3: name + state -> candidates -> the link rule. Stub candidates are recorded
        in gstin_candidate only; they never reach crosswalk or review_queue."""
        if self.router is None:
            return []
        with lib.pg() as conn, conn.cursor() as cur:
            ours = _ours(cur, entity_id)
            if not ours["legal_name"]:
                return []
            codes = sorted({CODE_OF_STATE.get(G.norm_state(s)) for s in ours["states"]} - {None})
            cost = self.router.providers[0].cost_search_inr
            ok, why = budget.allow(cur, cost)
            if not ok:
                return []
            ans = self.router.search(ours["legal_name"], codes[0] if codes else None)
            p = ans.provider
            budget.record(cur, provider=p.name, provider_class=p.provider_class, call="search",
                          entity_id=entity_id, trigger=trigger, cost_inr=p.cost_search_inr)
            out = []
            for cand in ans.result:
                decision, why = link.decide(cand, ours["names"], ours["pincodes"], norm_name)
                cur.execute("""INSERT INTO gstin_candidate (entity_id, gstin, legal_name, trade_name,
                               pincode, state, provider, provider_class, decision, why)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            (entity_id, cand.gstin, cand.lgnm, cand.tradeNam,
                             (cand.pradr or {}).get("pncd"), (cand.pradr or {}).get("stcd"),
                             p.name, p.provider_class, decision, why))
                if p.provider_class != "stub" and decision == "review":
                    cur.execute("""INSERT INTO review_queue (entity_type, left_key, right_key, features)
                                   VALUES ('gstin', %s, %s, %s)""",
                                (entity_id, cand.gstin,
                                 json.dumps({"reason": "name_search_candidate", "why": why})))
                # auto_link from a live provider becomes a crosswalk row in T4, with the
                # S16 source card live; until then a link is only a recorded decision.
                out.append({"gstin": cand.gstin, "decision": decision, "why": why})
            return out
