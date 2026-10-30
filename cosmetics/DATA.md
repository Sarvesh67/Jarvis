# Data: where it lives, how it is backed up, how to rebuild the graph

The graph is derived. Everything in Neo4j is rebuilt from Postgres by `make rebuild`, and
the document index is rebuilt from `raw/` by `make index`. So "can we rebuild the graph"
means "do we still hold what Postgres and `raw/` hold". This file says what that is, how
it is backed up, and how to fetch each source again from its origin if a backup is ever
not enough.

Measured 2026-10-30, at commit `e954fcf`.

## 1. What the graph is built from

| Store | Size | What it holds | If lost |
|---|---|---|---|
| `cosmetics/raw/` | 7,439 files, 1.35 GB | Every payload a source landed, content-addressed and append-only (`lib.land`, recorded in `fetch_manifest`) | Re-fetch from origin (§4); some sources no longer serve the same pages |
| Postgres `cosmetics` | 18 tables, 1.69M rows, 785 MB | Parsed source rows (`raw_*`), resolution (`crosswalk`, `review_queue`, `property_claim`), `trust_state`, `person` | See below: part of it exists nowhere else |
| Postgres `jarvis` | 23 MB | The platform's own tables | Platform setup state |
| `msme/out/firms.parquet` | 31 MB | Gujarat Udyam firms, the input S17 reads | The Udyam CSV it was built from (`udyam_500k.csv`) is no longer on this Mac. This file is the only copy |
| `msme/cache/` | 0.8 MB | Cached LLM outputs for the NIC-to-material snap (1,161 codes) | Re-run costs LLM calls |
| Neo4j `cosmetics-neo4j` | 174,448 nodes, 280,587 edges | Derived | `make rebuild` + `make index` |

**What exists only in Postgres.** These rows have no payload under `raw/`, so they cannot
be re-parsed. Getting them back means re-fetching for days, or never:

- `raw_company` for **s17b** (119,120 Udyam firms). The data.gov.in sweep kept matching
  rows and threw the pages away (decisions/0006, amended).
- `raw_company` for **s03** (1,609 MCA companies) and **s11** (6,682 NABL labs), and the
  6,682 NABL lab contacts in `person`. Both were read from APIs without landing.
- **`crosswalk`** (228,790 rows) and **`review_queue`** (15,603): append-only resolution
  history and the queue of decisions. A fresh `make resolve` produces a new crosswalk, not
  this one.

Everything else in Postgres is either parsed from `raw/` or computed by the pipeline.

## 2. Backups

```bash
make backup                 # snapshot to ~/Backups/lattix-cosmetics, then encrypt to iCloud Drive
make backup-verify          # restore the newest local snapshot into a scratch database and check it
make backup-verify-offsite  # the same check on the iCloud copy alone, plus its upload state
make backup-passphrase      # once per Mac: put the passphrase in the login keychain
```

`COSMETICS_BACKUP_DIR` and `COSMETICS_OFFSITE_DIR` override the two locations.

### 2a. The local copy

`pipeline/backup.py` writes:

```
~/Backups/lattix-cosmetics/
  raw/                         mirror of cosmetics/raw; only ever grows, never deletes
  msme/out/firms.parquet       the S17 input
  msme/cache/                  cached LLM outputs
  snapshots/<UTC time>/
    cosmetics.pgdump           pg_dump custom format, both databases
    jarvis.pgdump
    raw.sha256                 a checksum for every raw file
    MANIFEST.json              git commit, row counts per table and source, graph counts,
                               checksums of the dumps and the parquet
```

`make backup-verify` restores `cosmetics.pgdump` into a scratch database
(`cosmetics_restore_check`), compares every table's row count with the manifest, drops
the scratch database, then re-hashes the raw mirror and the parquet. First run,
2026-10-30: 18 tables, 1,692,807 rows restored exactly, and 7,439 of 7,439 raw files
matched. A snapshot takes about 15 seconds; the dump is 47 MB.

**Not in the backup, on purpose:**
- Secrets: `platform/.env` and `cosmetics/.env`. §5 names what they must hold.
- The Neo4j store. Community edition cannot dump while running, and the graph is derived.
- The msme FalkorDB graph and its 310 MB `documents.jsonl`. They belong to msme/, not to
  this graph.

### 2b. The off-machine copy: iCloud Drive, encrypted

The local copy protects against a dropped database or a bad pipeline run, not against
losing the Mac. So after the local snapshot, `make backup` writes encrypted chunks to
`iCloud Drive/Backups/lattix-cosmetics/` (the owner's choice, 2026-10-30):

```
files-<UTC>.tar.gz.enc       raw/ and msme/ files new or changed since the last files chunk
snapshot-<UTC>.tar.gz.enc    one snapshots/<UTC>/ directory
SHA256SUMS                   checksums of the encrypted files
KEYCHECK.enc                 refuses a passphrase that differs from the one this copy uses
README.txt                   how to restore with nothing but openssl and tar
```

- **Encryption.** tar.gz through `openssl enc -aes-256-cbc -pbkdf2 -iter 600000 -md
  sha256`. That is OpenSSL's own salted format, readable by OpenSSL 1.1.1+ or LibreSSL
  with no code from this repo. iCloud holds only ciphertext; Apple has no key to it.
- **Incremental.** `~/Backups/lattix-cosmetics/offsite.json` records which file went
  into which chunk. A run sends only new pages plus one ~48 MB snapshot. The first full
  copy is about 390 MB, because the pages compress 4×. If a chunk is deleted from iCloud,
  the next run sends its files again.
- **Nothing is deleted automatically**, in iCloud or locally. Each run adds about 50 MB
  plus new pages, and 81 GB of iCloud quota was free on 2026-10-30. To prune, delete old
  `snapshot-*` chunks. Never delete a `files-*` chunk: until the next run, the copy
  would be missing pages.
- **The passphrase has two copies, both the owner's:**
  1. The owner's password manager. The keychain is lost with the Mac, and **without the
     passphrase the iCloud copy cannot be read by anyone.**
  2. The login keychain item `lattix-cosmetics-backup`, which `make backup` reads.
     `make backup-passphrase` creates it by prompting. The passphrase never appears on
     a command line, in a file, or in output. On a machine without a keychain, set
     `COSMETICS_BACKUP_PASSFILE` to a file that holds it.
- **`make backup` fails** after taking the local snapshot when it could not copy
  off-machine: no passphrase, iCloud Drive off, or a different passphrase.
- **iCloud uploads in the background**, after `make backup` returns.
  `python -m pipeline.backup offsite-status` shows which chunks have finished uploading.
  "Optimize Mac Storage" is on for this account, so iCloud may later remove the local
  copies; reading one downloads it again.

**Tested 2026-10-30** with a throwaway passphrase, into a scratch folder:
- The copy alone restored 18 tables (1,692,807 rows) and 7,439 of 7,439 raw files.
- A second run sent nothing. A new or changed file was sent alone. A chunk deleted from
  the destination had its files sent again.
- A wrong passphrase and a tampered chunk were both refused.
- The README's openssl + tar recipe decrypted a snapshot with no repo code.

**Not yet done:**
- **Nothing runs on a schedule.** Run `make backup` after every ingest or pipeline run
  that changes data.
- **FileVault is off.** The iCloud copy is encrypted. The local backup, `raw/` and the
  Postgres volume on this disk are not, and they hold personal data: proprietors'
  GSTINs, NABL lab contacts, firm phones.

## 3. Restore and rebuild from a backup

On a machine where `setup/00_run_all.sh` has run (Docker, the containers, the venv,
`.env` files):

If the Mac is gone, first rebuild the backup root from iCloud. Signing in to iCloud Drive
brings the chunks; the passphrase comes from the password manager:

```bash
cd ~/Documents/Jarvis/cosmetics
make backup-passphrase                                            # type it in
.venv/bin/python -m pipeline.backup restore-offsite ~/Backups/lattix-cosmetics
make backup-verify                                                # restore check on what came back
```

`offsite.json` is not part of the copy, so the first `make backup` after this sends one
full files chunk again (about 390 MB). Then:

```bash
B=~/Backups/lattix-cosmetics; S=$B/snapshots/<the snapshot>
cd ~/Documents/Jarvis/cosmetics
make bootstrap                                                    # containers, schema, constraints
docker exec -i jarvis-postgres pg_restore -U jarvis -d cosmetics --clean --if-exists --no-owner < $S/cosmetics.pgdump
docker exec -i jarvis-postgres pg_restore -U jarvis -d jarvis --clean --if-exists --no-owner < $S/jarvis.pgdump
rsync -a $B/raw/ raw/                                             # payloads back
mkdir -p ../msme/out && cp -p $B/msme/out/firms.parquet ../msme/out/
rsync -a $B/msme/cache/ ../msme/cache/
make rebuild                                                      # wipe Neo4j, load, derive, trust, validate
make index                                                        # chunk + embed the documents (local Ollama, free; slow)
make catalog-check && make test                                   # stores agree with the cards; invariants hold
launchctl kickstart -k gui/$(id -u)/com.lattix.demo               # restart the demo on :8099
```

`make rebuild` runs Stages 5-8 only. Stages 2-4 (normalize, resolve, reconcile) wrote
their results to Postgres, and the dump restores them as they were. Run `make pipeline`
instead only if you want them recomputed.

`make rebuild` wipes and reloads the whole graph. Neo4j runs near the 4 GB Colima limit;
if it is killed mid-load, check `docker inspect cosmetics-neo4j --format
'{{.RestartCount}}'` and run the same command again.

## 4. Fetching every source again from its origin

Only needed if a backup is lost or a source must be refreshed. Run from `cosmetics/`.
Every adapter appends to `raw/` and upserts its Postgres rows, so a re-run never
destroys data. Afterwards: `make pipeline`, `make index`, `make catalog`, and fix any card
`make catalog-check` flags.

| Source | Command | Needs | Lands a payload? | Notes |
|---|---|---|---|---|
| s15 GeoNames pincodes | `make ingest SOURCE=s15` | nothing | yes, zip | Seconds. Gives `ref_pincode` lat, lon and places (0028) |
| s06 EU CosIng | `make ingest SOURCE=s06 ARGS="--item-type both"` | nothing | yes, JSON | Paged search API, 0.35 s between requests. Re-fetches every run |
| s05 Cosmetics Rules 2020 | put the gazette at `raw/s05_rules/cosmetics_rules_2020.pdf` (from cdsco.gov.in), then `make ingest SOURCE=s05` | the PDF | the PDF is the payload | The schedule is hand-verified constants; the PDF checks them |
| s07 Open Beauty Facts | download the export to `raw/s07_obf/openbeautyfacts-products.jsonl.gz` (world.openbeautyfacts.org/data), then `make ingest SOURCE=s07` | the export | the export is the payload | Run after s06: it needs the INCI vocabulary |
| s01 CDSCO manufacturing sites | `make ingest SOURCE=s01` | nothing | yes | 1 request per 3 s. `ARGS=--replay` re-parses landed pages offline |
| s08 trade-fair exhibitors | `make ingest SOURCE=s08` | nothing | yes | Four edition URLs in the adapter; old editions may vanish |
| s09 CHEMEXCIL members | `make ingest SOURCE=s09` | nothing | yes | 74 alphabet pages |
| s11 NABL testing labs | `make ingest SOURCE=s11` | nothing | **no**, rows only | Every state; states over the 100-row cap are split by city. Lab contacts go to `person` |
| s03 MCA master | `make ingest SOURCE=s03 ARGS="--mode bulk"` | `DATA_GOV_IN_KEY` with MCA access | **no**, rows only | Card says blocked: the MCA resource is gated separately from Udyam. Checkpoints in `cache/s03_offsets.json` |
| s17 Udyam, Gujarat | `make ingest SOURCE=s17` | `msme/out/firms.parquet` | no (reads the parquet) | To rebuild the parquet: `bash msme/run.sh --csv <Udyam CSV>`. The CSV must be obtained again |
| s17b Udyam, national | `make ingest SOURCE=s17b ARGS="--scope wedge --delay 1.2"`, then `make ingest SOURCE=s17b ARGS="--scope all-clusters --delay 1.2 --max-requests 9000"` | `DATA_GOV_IN_KEY` | **no**, rows only | ~12,300 requests, 4-5 h, resumable. 3,264 of 4,842 cluster pincodes were swept; Kerala, West Bengal and Assam were not. Phase 1 must make it land pages (0006) |
| s14 company websites | `make ingest SOURCE=s14 ARGS="--all-roles --limit <N>"`, repeated until the remaining sites time out, then `make ingest SOURCE=s14 ARGS="--follow-landed"` | nothing | yes, HTML | Hours. The September passes visited 113, 600 and 2,011 sites (`cache/s14_*.log`); their exact flags were not recorded. The catalogue pass ran on 2026-10-29. Sites change, so a re-crawl will not reproduce the same pages |
| s14b supply claims | `make ingest SOURCE=s14b` | the S14 pages | reads S14's | Offline, about a minute. Rewrites `raw_supplies` from the matching rules |

Blocked or not built, so there is nothing to fetch: s02 (no bulk source), s10 (IndiaMART,
decisions/0022 forbids it), s16 (GST verification provider), s19 (SEBI filings).

Order matters in three places. s07 needs s06's vocabulary. s14b needs s14's pages. Run
`make pipeline` after the last adapter, because resolution compares every source with
every other.

**Five sources keep a payload but cannot parse it back**: s06, s08, s09, s14 (its company
rows) and s15. Re-running them re-fetches from the web. Their payloads in `raw/` are
evidence of what was fetched, not yet a replay path. Only s01 (`--replay`), s05, s07, s14b
and s17 rebuild their rows from local files.

## 5. Credentials a rebuild needs

Names only; the values live in `.env` files that are never committed or backed up.

- `platform/.env`:
  - `POSTGRES_USER`, `POSTGRES_PASSWORD` (the `jarvis-postgres` container);
  - `LITELLM_MASTER_KEY` and `COSMETICS_LLM_KEY` (the router's `fast` model, and `embed`
    for `make index`);
  - `OPENROUTER_API_KEY`, which the gateway itself needs.
- `cosmetics/.env`:
  - `DATA_GOV_IN_KEY` (s03, s17b);
  - `COSMETICS_BI_PASSWORD` (the Metabase role).
- Neo4j uses `lib.py`'s defaults, which match `docker-compose.yml`. Set `NEO4J_USER` and
  `NEO4J_PASSWORD` to override them.

All of these can be issued again:
- the Postgres, LiteLLM and BI values are chosen at setup;
- the API keys come from the data.gov.in and OpenRouter accounts.

The one secret that cannot be issued again is the **backup passphrase**. It lives in the
keychain item `lattix-cosmetics-backup` and in the owner's password manager (§2b).
Without it, the iCloud copy cannot be read.
