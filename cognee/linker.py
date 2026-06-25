"""Entity ↔ ticker linker — the join key between the knowledge graph and the timeseries store.

The graph extracts messy entity strings ("Infosys", "Tata Motors Ltd"); the backtester and the
price-relevance feature need a canonical NSE ticker + an `as_of` date. This module resolves both
deterministically from a curated per-project `symbols.json` (NEVER guesses a ticker — an unmapped
entity yields no event, fail closed).

`symbols.json` (per project, materialized from seed like pipeline/ontology/models):
    {
      "defaultBenchmark": "^NSEI",
      "tickers": {
        "INFY.NS": {"aliases": ["Infosys","INFY"], "benchmark": "^CNXIT", "sector": "IT"},
        ...
      }
    }

Stamping: at ingest the resolved tickers + as_of ride into the graph as namespaced node_set tags
(`ticker:INFY.NS`, `asof:2024-06-03`) — the same node_set mechanism the dashboard already uses for
tags — so doc/entity nodes are reachable by ticker and carry a date the backtester reads.
"""
from __future__ import annotations

import json
import pathlib
import re
from datetime import date, datetime

BASE = pathlib.Path(__file__).resolve().parent
DEFAULT_BENCHMARK = "^NSEI"

TICKER_TAG = "ticker:"   # namespaced node_set prefixes (kept out of routing `tags`)
ASOF_TAG = "asof:"


def _paths(project: str):
    return (BASE / "data" / project / "symbols.json",
            BASE / "seeds" / project / "symbols.json")


def load_symbols(project: str) -> dict:
    """Read the project's symbols.json: local data/ → seed → empty. Materializes the local copy
    from seed on first access (mirrors the dashboard's pipeline/ontology/models pattern)."""
    local, seed = _paths(project)
    for p in (local, seed):
        if p.exists():
            try:
                cfg = json.loads(p.read_text())
            except Exception:  # noqa: BLE001
                continue
            if p is seed:  # materialize so a fresh device gets the curated map, gitignored
                try:
                    local.parent.mkdir(parents=True, exist_ok=True)
                    local.write_text(json.dumps(cfg, indent=2))
                except Exception:  # noqa: BLE001
                    pass
            return cfg
    return {"defaultBenchmark": DEFAULT_BENCHMARK, "tickers": {}}


def _alias_patterns(symbols: dict):
    """Yield (ticker, compiled word-boundary regex) for every alias and the ticker itself."""
    for ticker, meta in (symbols.get("tickers") or {}).items():
        names = [ticker] + list((meta or {}).get("aliases") or [])
        for name in names:
            name = (name or "").strip()
            if not name:
                continue
            yield ticker, re.compile(r"\b" + re.escape(name) + r"\b", re.IGNORECASE)


def resolve_tickers(text: str, symbols: dict, *, explicit=None) -> list[str]:
    """Tickers a doc concerns: explicit job-provided ones + alias matches in the text. Deduped,
    order-stable. Unknown explicit tickers are kept verbatim (caller curates), unmatched text is
    simply not resolved (never guessed)."""
    found: list[str] = []
    seen: set[str] = set()

    def _add(t: str):
        if t and t not in seen:
            seen.add(t)
            found.append(t)

    valid = set((symbols.get("tickers") or {}).keys())
    for t in explicit or []:
        _add(t if t in valid else t.strip())
    blob = text or ""
    for ticker, pat in _alias_patterns(symbols):
        if pat.search(blob):
            _add(ticker)
    return found


def benchmark_for(ticker: str, symbols: dict) -> str:
    meta = (symbols.get("tickers") or {}).get(ticker) or {}
    return meta.get("benchmark") or symbols.get("defaultBenchmark") or DEFAULT_BENCHMARK


def normalize_asof(value) -> str | None:
    """Coerce a job-provided as_of to an ISO date string (YYYY-MM-DD), or None."""
    if value is None or value == "":
        return None
    if isinstance(value, (datetime, date)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y", "%d %b %Y", "%d %B %Y",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    # last resort: ISO-ish prefix
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    return m.group(1) if m else None


def provenance_tags(tickers, as_of) -> list[str]:
    """Namespaced node_set tags that stamp ticker(s) + as_of onto the graph nodes for an ingest."""
    tags = [f"{TICKER_TAG}{t}" for t in (tickers or [])]
    if as_of:
        tags.append(f"{ASOF_TAG}{as_of}")
    return tags
