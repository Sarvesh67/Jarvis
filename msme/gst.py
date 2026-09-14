"""GST verification — cached, joined at preprocess (never written back to the raw Udyam CSV).

The raw registry stays immutable. Verification results live in cache/gst.json keyed by GSTIN, so
500k rows cost only distinct-GSTIN API calls and re-runs are free. `verify_gst` wraps a SWAPPABLE
client — drop your provider's call into `_call_api`. Fail-open to 'unknown' so a flaky API never
blocks the pipeline.

    from msme import gst
    status = gst.verify_gst("24ABCDE1234F1Z5")   # -> "verified" | "invalid" | "unknown"

Preprocess joins this in: a firm's GSTIN (from a CSV column or a Udyam→GSTIN map — TBD with the
user) → verify_gst → the `gst_status` field + `gst:verified` search tag.
"""
from __future__ import annotations

import json
import os
import pathlib

BASE = pathlib.Path(__file__).resolve().parent
CACHE = BASE / "cache"
CACHE.mkdir(exist_ok=True)
_CACHE_PATH = CACHE / "gst.json"

# Point these at your provider (e.g. a GST verification API / GSTN gateway). Left blank => stub mode.
GST_API_URL = os.environ.get("MSME_GST_API_URL", "")
GST_API_KEY = os.environ.get("MSME_GST_API_KEY", "")


def _load() -> dict:
    if _CACHE_PATH.exists():
        try:
            return json.loads(_CACHE_PATH.read_text())
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _save(cache: dict):
    _CACHE_PATH.write_text(json.dumps(cache, indent=2))


def _call_api(gstin: str, http) -> str:
    """SWAP THIS for the real provider call. Must return 'verified' | 'invalid' | 'unknown'.
    Stub: with no API configured, treat a well-formed 15-char GSTIN as 'verified' so the rest of
    the pipeline (filters, tags) can be exercised before the real API is wired."""
    if not GST_API_URL:
        return "verified" if (gstin and len(gstin) == 15) else "unknown"
    # --- example shape; adapt to your provider ---
    # r = http.get(f"{GST_API_URL}/{gstin}", headers={"Authorization": f"Bearer {GST_API_KEY}"}, timeout=20)
    # r.raise_for_status()
    # return "verified" if r.json().get("valid") else "invalid"
    raise NotImplementedError("wire the real GST API in gst._call_api")


def verify_gst(gstin: str, cache: dict | None = None, http=None) -> str:
    """Cached verification. `cache` lets a batch run share one dict; else the on-disk cache is used."""
    gstin = (gstin or "").strip().upper()
    if not gstin:
        return "unknown"
    owns = cache is None
    cache = _load() if owns else cache
    if gstin in cache:
        return cache[gstin]
    try:
        status = _call_api(gstin, http)
    except Exception:  # noqa: BLE001 — fail open
        status = "unknown"
    cache[gstin] = status
    if owns:
        _save(cache)
    return status
