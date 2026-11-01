"""Ordered providers per call, with a circuit breaker and a shadow sample (TRUST_PLAN §4.4).

A provider that fails `failures` times in a row is skipped for `cooldown` seconds and the
next one answers. With two or more providers, `shadow_rate` of calls also go to a second
provider, and `disagreement` names the fields where they differ — the per-vendor accuracy
the plan publishes instead of assuming. Hedged requests (fire the next provider once the
first passes its p95) need latency history and wait for a second live provider (T4).
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from trust.gst.adapter import Provider, ProviderError, Taxpayer

COMPARED = ("sts", "lgnm", "tradeNam", "dty", "ctb")


@dataclass
class _Health:
    fails: int = 0
    open_until: float = 0.0


@dataclass
class Answer:
    result: object                    # Taxpayer | None | list[Taxpayer]
    provider: Provider
    latency_ms: int
    shadow: list = field(default_factory=list)    # [(provider, result)]


class Router:
    def __init__(self, providers: list[Provider], failures: int = 3, cooldown: float = 60.0,
                 shadow_rate: float = 0.02, rng=random.random, clock=time.monotonic):
        if not providers:
            raise ValueError("no GST provider configured")
        self.providers = providers
        self.failures, self.cooldown = failures, cooldown
        self.shadow_rate, self.rng, self.clock = shadow_rate, rng, clock
        self.health = {p.name: _Health() for p in providers}

    def _healthy(self) -> list[Provider]:
        now = self.clock()
        return [p for p in self.providers if self.health[p.name].open_until <= now]

    def _call(self, method: str, *args) -> Answer:
        errors = []
        healthy = self._healthy()
        for i, p in enumerate(healthy):
            t0 = time.perf_counter()
            try:
                res = getattr(p, method)(*args)
            except ProviderError as e:
                h = self.health[p.name]
                h.fails += 1
                if h.fails >= self.failures:
                    h.open_until, h.fails = self.clock() + self.cooldown, 0
                errors.append(f"{p.name}: {e}")
                continue
            self.health[p.name].fails = 0
            ans = Answer(res, p, int((time.perf_counter() - t0) * 1000))
            rest = healthy[i + 1:]
            if rest and self.rng() < self.shadow_rate:
                try:
                    ans.shadow.append((rest[0], getattr(rest[0], method)(*args)))
                except ProviderError:
                    pass
            return ans
        raise ProviderError("; ".join(errors) or "every provider is cooling down")

    def verify(self, gstin: str) -> Answer:
        return self._call("verify", gstin)

    def search(self, name: str, state_code: str | None) -> Answer:
        return self._call("search_by_name", name, state_code)


def disagreement(a: Taxpayer | None, b: Taxpayer | None) -> list[str]:
    if a is None or b is None:
        return [] if a is b else ["found"]
    out = [f for f in COMPARED if (getattr(a, f) or None) != (getattr(b, f) or None)]
    if (a.pradr or {}).get("pncd") != (b.pradr or {}).get("pncd"):
        out.append("pradr.pncd")
    return out
