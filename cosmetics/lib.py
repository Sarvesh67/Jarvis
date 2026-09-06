"""Shared plumbing for the cosmetics graph build.

Everything an adapter needs: config, a rate-limited HTTP client with retry, immutable
raw landing + fetch_manifest bookkeeping, and Postgres/Neo4j handles.

Invariants enforced here (spec §17):
  1. raw/ is append-only — land() never overwrites a payload; a second fetch of the same
     bytes is a no-op, different bytes land under a new content-hashed filename.
  9. every property and edge carries provenance — prov() builds the envelope.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx
import yaml
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw"
OUT = ROOT / "out"
CACHE = ROOT / "cache"
ONTOLOGY = ROOT / "ontology"
for _d in (RAW, OUT, CACHE):
    _d.mkdir(parents=True, exist_ok=True)

JARVIS_ROOT = ROOT.parent
UA = "cosmetics-graph/0.2 (India cosmetics supply-chain research; contact: sarveshshinde64@gmail.com)"


# --------------------------------------------------------------------------- config
def _platform_env() -> dict[str, str]:
    env: dict[str, str] = {}
    p = JARVIS_ROOT / "platform" / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def _local_env() -> dict[str, str]:
    env: dict[str, str] = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


ENV = {**_platform_env(), **_local_env(), **os.environ}

PG_DSN = (
    f"postgresql://{ENV.get('POSTGRES_USER','jarvis')}:{ENV.get('POSTGRES_PASSWORD','')}"
    f"@127.0.0.1:5432/cosmetics"
)
NEO4J_URI = ENV.get("NEO4J_URI", "bolt://127.0.0.1:7687")
NEO4J_AUTH = (ENV.get("NEO4J_USER", "neo4j"), ENV.get("NEO4J_PASSWORD", "cosmetics"))

# data.gov.in: the documented shared key is heavily rate-limited (10 rows/request).
# Set DATA_GOV_IN_KEY in cosmetics/.env to lift it.
DATA_GOV_KEY = ENV.get("DATA_GOV_IN_KEY", "579b464db66ec23bdd000001cdd3946e44ce4aad7209ff7b23ac571b")
# NOTE: every data.gov.in key begins 579b464db66ec23bdd000001 — the TAIL is what differs.
# Detect the published sample key by its full value, not by that shared prefix, or a
# genuine key gets misflagged as the demo one.
SHARED_DEMO_KEY = "579b464db66ec23bdd000001cdd3946e44ce4aad7209ff7b23ac571b"
DATA_GOV_KEY_IS_SHARED = DATA_GOV_KEY == SHARED_DEMO_KEY


def now() -> datetime:
    return datetime.now(timezone.utc)


def prov(source_id: str, tier: int, confidence: float = 1.0, **extra) -> dict[str, Any]:
    """The provenance envelope carried on every property group and edge."""
    env = {
        "source_id": source_id,
        "retrieved_at": now().isoformat(),
        "tier": tier,
        "confidence": confidence,
        "valid_from": None,
        "valid_to": None,
    }
    env.update(extra)
    return env


# --------------------------------------------------------------------------- postgres
def pg():
    import psycopg

    return psycopg.connect(PG_DSN, autocommit=True)


def neo4j_driver():
    """Neo4j driver with schema-hint notifications muted.

    Neo4j warns on every query that names a label/property/relationship it has not seen
    yet. That is correct for a typo and pure noise for a graph being built incrementally
    (a negative invariant test like `MATCH (n:Person) RETURN count(n)` MUST match nothing).
    Real errors still raise.
    """
    from neo4j import GraphDatabase, NotificationMinimumSeverity

    return GraphDatabase.driver(
        NEO4J_URI, auth=NEO4J_AUTH,
        notifications_min_severity=NotificationMinimumSeverity.WARNING,
        notifications_disabled_classifications=["UNRECOGNIZED", "UNSUPPORTED"],
    )


# --------------------------------------------------------------------------- raw landing
def land(source_id: str, url: str, content: bytes, *, suffix: str = ".html",
         http_status: int = 200, key: str | None = None) -> Path:
    """Land an immutable payload under raw/<source_id>/ and record it in fetch_manifest.

    Content-addressed: identical bytes never land twice. Returns the raw path either way.
    """
    digest = hashlib.sha256(content).hexdigest()
    d = RAW / source_id
    d.mkdir(parents=True, exist_ok=True)
    stem = (key or digest[:16]).replace("/", "_").replace(" ", "_")[:120]
    path = d / f"{stem}.{digest[:8]}{suffix}"
    if not path.exists():
        path.write_bytes(content)
    rel = str(path.relative_to(ROOT))
    try:
        with pg() as c:
            c.execute(
                "INSERT INTO fetch_manifest (source_id, url, http_status, content_hash, raw_path) "
                "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (source_id, content_hash) DO NOTHING",
                (source_id, url, http_status, digest, rel),
            )
    except Exception as e:  # a manifest failure must not lose the payload
        print(f"  ! fetch_manifest write failed ({e}); payload retained at {rel}")
    return path


# --------------------------------------------------------------------------- http
@dataclass
class Fetcher:
    """Rate-limited, retrying HTTP client. One instance per source; `delay` is the
    minimum gap between requests to that source (spec S1: 1 req / 3s for CDSCO)."""

    source_id: str
    delay: float = 1.0
    timeout: float = 60.0
    headers: dict[str, str] = field(default_factory=dict)
    max_attempts: int = 8
    backoff_base: float = 15.0
    backoff_max: float = 900.0
    _last: float = 0.0
    _client: httpx.Client | None = None

    def __post_init__(self):
        h = {"User-Agent": UA, **self.headers}
        self._client = httpx.Client(timeout=self.timeout, headers=h, follow_redirects=True)

    def _throttle(self):
        gap = time.monotonic() - self._last
        if gap < self.delay:
            time.sleep(self.delay - gap)
        self._last = time.monotonic()

    def _send(self, method: str, url: str, **kw) -> httpx.Response:
        """Rate-limit aware send. 429/5xx back off hard and honour Retry-After;
        a shared-key quota wall is a wait, not a failure."""
        for attempt in range(self.max_attempts):
            self._throttle()
            r = self._client.request(method, url, **kw)
            if r.status_code in (429, 502, 503, 504):
                ra = r.headers.get("Retry-After")
                asked = float(ra) if (ra or "").replace(".", "").isdigit() else None
                # Retry-After is CAPPED at backoff_max, which is the definition of "the
                # longest this adapter is prepared to wait". A single hostile host
                # answering 503 with Retry-After: 3600 must not stall a 2,000-site crawl;
                # for a quota-limited API, backoff_max is set high enough to honour it.
                wait = min(asked if asked is not None else
                           self.backoff_base * (2 ** attempt), self.backoff_max)
                if asked is not None and asked > self.backoff_max:
                    print(f"    ~ {r.status_code} from {self.source_id}; server asked for "
                          f"{asked:.0f}s, capped at {self.backoff_max:.0f}s and moving on",
                          flush=True)
                    raise httpx.HTTPStatusError("retry-after exceeds budget",
                                                request=r.request, response=r)
                print(f"    ~ {r.status_code} from {self.source_id}; backing off {wait:.0f}s "
                      f"(attempt {attempt+1}/{self.max_attempts})", flush=True)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r
        r.raise_for_status()
        return r

    def get(self, url: str, **kw) -> httpx.Response:
        return self._send("GET", url, **kw)

    def post(self, url: str, **kw) -> httpx.Response:
        return self._send("POST", url, **kw)

    def close(self):
        if self._client:
            self._client.close()


# --------------------------------------------------------------------------- ontology
def load_yaml(name: str) -> dict:
    return yaml.safe_load((ONTOLOGY / name).read_text())


_clusters: dict | None = None


def clusters() -> dict:
    global _clusters
    if _clusters is None:
        _clusters = load_yaml("cluster_map.yaml")["clusters"]
    return _clusters


# --------------------------------------------------------------------------- bulk upsert
def upsert(table: str, rows: Iterable[dict], conflict: str, *, chunk: int = 1000) -> int:
    """ON CONFLICT DO UPDATE upsert on the source natural key. Returns rows written."""
    rows = list(rows)
    if not rows:
        return 0
    cols = list(rows[0].keys())
    conflict_cols = {c.strip() for c in conflict.split(",")}
    setters = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols if c not in conflict_cols)
    placeholders = ", ".join(["%s"] * len(cols))
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({conflict}) DO UPDATE SET {setters}"
        if setters else
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({conflict}) DO NOTHING"
    )
    n = 0
    with pg() as c, c.cursor() as cur:
        for i in range(0, len(rows), chunk):
            batch = rows[i:i + chunk]
            cur.executemany(sql, [tuple(_adapt(r.get(k)) for k in cols) for r in batch])
            n += len(batch)
    return n


def _adapt(v):
    if isinstance(v, (dict, list)) and not (isinstance(v, list) and all(
            isinstance(x, str) or x is None for x in v)):
        return json.dumps(v, ensure_ascii=False)
    return v


def jsonb(v) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


# --------------------------------------------------------------------------- text matching
# Tokenise on WHITESPACE only, then trim punctuation from the ends of each token.
# Splitting on all punctuation turns "Sodium bis(2-methoxyethoxy)aluminium hydride" into
# separate tokens and matches ALUMINIUM inside it — a different substance. Keeping
# internal punctuation attached means a chemical name only matches when it stands alone.
_EDGE = ".,;:!?\"'()[]{}<>|/\\*"
MAX_NGRAM = 5


def tokens_of(text: str) -> list[str]:
    return [t for t in (w.strip(_EDGE) for w in text.upper().split()) if t]


def ngrams(toks: list[str], needles: set[str], max_n: int = MAX_NGRAM) -> dict[str, int]:
    """{matched name -> token index}, LONGEST match first.

    Longest-first matters: text listing "Aluminium Fluoride" should record aluminium
    fluoride, not aluminium. Once a span is consumed by a longer name, the shorter names
    inside it are not matched again.
    """
    hits: dict[str, int] = {}
    consumed: set[int] = set()
    n = len(toks)
    for k in range(max_n, 0, -1):
        for i in range(n - k + 1):
            if any(j in consumed for j in range(i, i + k)):
                continue
            cand = " ".join(toks[i:i + k])
            if len(cand) > 60:
                continue
            if cand in needles:
                hits.setdefault(cand, i)
                consumed.update(range(i, i + k))
    return hits


def embed(texts: list[str], *, model: str = "embed", batch: int = 32) -> list[list[float]]:
    """768-dim embeddings via the platform's LiteLLM gateway -> local Ollama.

    Local and free, so a full rebuild costs nothing and the spec's "avoid API cost on
    rebuilds" constraint is met without a ~2 GB torch install.
    """
    key = ENV.get("LITELLM_MASTER_KEY", "")
    out: list[list[float]] = []
    with httpx.Client(timeout=180.0) as c:
        for i in range(0, len(texts), batch):
            chunk = texts[i:i + batch]
            r = c.post("http://127.0.0.1:4000/v1/embeddings",
                       headers={"Authorization": f"Bearer {key}"},
                       json={"model": model, "input": chunk})
            r.raise_for_status()
            data = sorted(r.json()["data"], key=lambda d: d["index"])
            out.extend(d["embedding"] for d in data)
    return out
