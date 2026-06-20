#!/usr/bin/env python3
"""Phase 0 integrity verifier.

Walks ~/financial-data/datasets/, hashes files (with mtime-cached SHA-256),
counts JSONL rows, and writes a dated report to ~/financial-data/logs/.

Exit code: 0 clean, 1 anomalies detected, 2 script error.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HOME = Path(os.path.expanduser("~"))
DATA = HOME / "financial-data"
DATASETS = DATA / "datasets"
LOGS = DATA / "logs"
CACHE = DATASETS / ".hashes.json"

# Anomaly thresholds
ROWCOUNT_DRIFT_PCT = 1.0  # % drift vs last-known triggers a flag


def sha256(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def load_cache() -> dict[str, dict[str, Any]]:
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_cache(cache: dict[str, dict[str, Any]]) -> None:
    CACHE.write_text(json.dumps(cache, indent=2, sort_keys=True))


def count_jsonl_rows(path: Path) -> int | None:
    if path.suffix != ".jsonl":
        return None
    n = 0
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for _ in f:
            n += 1
    return n


def main() -> int:
    if not DATA.exists():
        print(f"FATAL: {DATA} missing", file=sys.stderr)
        return 2
    LOGS.mkdir(parents=True, exist_ok=True)
    if not DATASETS.exists():
        print(f"WARN: {DATASETS} missing — nothing to verify")
        DATASETS.mkdir(exist_ok=True)

    cache = load_cache()
    anomalies: list[str] = []
    entries: list[dict[str, Any]] = []

    for p in sorted(DATASETS.rglob("*")):
        if not p.is_file() or p.name.startswith(".") or p == CACHE:
            continue

        stat = p.stat()
        rel = str(p.relative_to(DATASETS))
        cached = cache.get(rel, {})
        size = stat.st_size
        mtime = stat.st_mtime

        # Zero-byte flag
        if size == 0:
            anomalies.append(f"ZERO-BYTE: {rel}")

        # Hash (cache by mtime)
        if cached.get("mtime") == mtime and "sha256" in cached:
            digest = cached["sha256"]
            hashed_now = False
        else:
            digest = sha256(p) if size > 0 else ""
            hashed_now = True

        # Row count for JSONL
        rows_now = count_jsonl_rows(p)
        rows_prev = cached.get("rows")
        if rows_now is not None and rows_prev is not None and rows_prev > 0:
            drift = abs(rows_now - rows_prev) / rows_prev * 100
            if drift > ROWCOUNT_DRIFT_PCT:
                anomalies.append(
                    f"ROW-DRIFT: {rel} {rows_prev} -> {rows_now} ({drift:.2f}%)"
                )

        entries.append({
            "path": rel,
            "size": size,
            "sha256": digest,
            "rows": rows_now,
            "hashed_now": hashed_now,
        })
        cache[rel] = {"mtime": mtime, "sha256": digest, "rows": rows_now}

    save_cache(cache)

    # Report
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%d-%H%M")
    report_path = LOGS / f"phase0-verify-{stamp}.md"

    lines = [
        f"# Phase 0 Verify — {now.isoformat()}",
        "",
        f"**Dataset root**: `{DATASETS}`",
        f"**Files scanned**: {len(entries)}",
        f"**Anomalies**:    {len(anomalies)}",
        "",
        "## Anomalies",
        "",
    ]
    lines.extend(f"- {a}" for a in anomalies) if anomalies else lines.append("_(none)_")
    lines.extend(["", "## Files", "", "| path | size | sha256 (first 12) | rows |", "|---|---:|---|---:|"])
    for e in entries:
        rows_cell = "-" if e["rows"] is None else str(e["rows"])
        sha_cell = e["sha256"][:12] if e["sha256"] else "(empty)"
        lines.append(f"| `{e['path']}` | {e['size']:,} | `{sha_cell}` | {rows_cell} |")
    lines.append("")
    report_path.write_text("\n".join(lines))

    print(f"Report: {report_path}")
    if anomalies:
        print(f"ANOMALIES: {len(anomalies)}", file=sys.stderr)
        for a in anomalies:
            print(f"  - {a}", file=sys.stderr)
        return 1
    print("Clean.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # pragma: no cover
        print(f"FATAL: {exc}", file=sys.stderr)
        sys.exit(2)
