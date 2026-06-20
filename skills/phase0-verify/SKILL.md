---
name: phase0-verify
description: Verify the integrity of Phase 0 data already on disk (SHA-256, row counts, anomaly flags) and write a status report.
version: 0.1.0
metadata:
  hermes:
    tags: [data-collection, verification, phase0]
    category: data-collection
---

## When to Use

Run this skill when:
- The Phase 0 data load is suspected stale or corrupted.
- After any manual move/rename of `/Users/hedgefund/financial-data/`.
- Weekly, as a routine integrity check (wire into cron).

Don't use this skill for fresh downloads — that's `phase0-download`.

## Procedure

1. Invoke the verifier script:
   ```
   python3 /Users/hedgefund/.hermes/skills/data-collection/phase0-verify/scripts/phase0_verify.py
   ```
2. The script walks `/Users/hedgefund/financial-data/datasets/`, computes SHA-256 for each file over 1 MB, counts JSONL rows where applicable, and writes a dated report to `/Users/hedgefund/financial-data/logs/phase0-verify-YYYYMMDD.md`.
3. Read the report. Record any anomalies (missing files, zero-byte files, row-count drift > 1%) in `MEMORY.md` under the `phase0-integrity` tag.
4. If everything is clean, no memory update needed.

## Pitfalls

- Large files (GDELT dumps, Common Crawl shards) make hashing slow. Script caches hashes in `datasets/.hashes.json` and only rehashes when mtime changes.
- If `phase0_summary.json` is missing, the script treats it as a fresh-never-run state — don't panic, it just means there's nothing to compare against yet.
- Fortress is read-only; don't try to write reports there. Report lands in `financial-data/logs/`.

## Verification

After the skill runs:
- A new file exists at `/Users/hedgefund/financial-data/logs/phase0-verify-*.md`.
- Script exit code is 0 on clean, 1 on anomalies detected.
- Any anomalies are reflected in `MEMORY.md`.
