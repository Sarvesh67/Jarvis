"""Back up everything the graph is built from, and prove the backup restores.  DATA.md.

The graph is derived: `make rebuild` replays it from Postgres, and `make index` re-embeds
the documents from raw/. So a backup is what Postgres holds plus what raw/ holds, and
three things that live nowhere else:

  * Postgres rows from sources that never landed a payload: the Udyam API sweep (s17b,
    119k firms), MCA (s03) and NABL (s11). Re-fetching them is days of API budget.
  * resolution state: `crosswalk` is append-only history and `review_queue` holds
    decisions; neither can be re-derived once lost.
  * msme/out/firms.parquet, the only copy left of the Udyam CSV behind s17.

Layout under BACKUP_DIR (default ~/Backups/lattix-cosmetics):

  raw/                      mirror of cosmetics/raw. Payloads are content-addressed and
                            immutable, so this only ever grows; nothing is deleted from it.
  msme/out/firms.parquet    the s17 input
  msme/cache/               cached LLM outputs for the NIC -> material snap
  snapshots/<UTC time>/     cosmetics.pgdump, jarvis.pgdump (pg_dump custom format),
                            raw.sha256, MANIFEST.json (git commit, row counts, graph
                            counts, checksums)

  python -m pipeline.backup                 take a snapshot
  python -m pipeline.backup verify [DIR]    restore the newest (or DIR) snapshot into a
                                            scratch database and check it against the
                                            manifest; re-hash the raw mirror

Secrets (.env files) are deliberately not copied. DATA.md lists the names they need.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib  # noqa: E402

PG_CONTAINER = "jarvis-postgres"
PG_USER = lib.ENV.get("POSTGRES_USER", "jarvis")
DATABASES = ("cosmetics", "jarvis")      # jarvis is the platform's, and small
SCRATCH_DB = "cosmetics_restore_check"
DEFAULT_DIR = Path(lib.ENV.get("COSMETICS_BACKUP_DIR", "~/Backups/lattix-cosmetics")).expanduser()
MSME = lib.JARVIS_ROOT / "msme"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def rsync(src: Path, dst: Path) -> None:
    """Copy new files only. Never --delete: the mirror is append-only, like raw/ itself."""
    dst.mkdir(parents=True, exist_ok=True)
    subprocess.run(["rsync", "-a", "--ignore-existing", f"{src}/", f"{dst}/"], check=True)


def pg_dump(db: str, out: Path) -> None:
    with out.open("wb") as f:
        subprocess.run(["docker", "exec", PG_CONTAINER, "pg_dump", "-U", PG_USER, "-Fc",
                        "-Z", "6", "-d", db], stdout=f, check=True)


def table_counts(dsn_db: str | None = None) -> dict[str, dict]:
    """{table: {"rows": n, "by_source": {...}}} for every base table in public."""
    import psycopg
    dsn = lib.PG_DSN if dsn_db is None else lib.PG_DSN.rsplit("/", 1)[0] + "/" + dsn_db
    out = {}
    with psycopg.connect(dsn, autocommit=True) as c, c.cursor() as cur:
        cur.execute("""SELECT table_name FROM information_schema.tables
                       WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY 1""")
        for (t,) in cur.fetchall():
            cur.execute(f'SELECT count(*) FROM public."{t}"')
            rec = {"rows": cur.fetchone()[0]}
            cur.execute("""SELECT 1 FROM information_schema.columns WHERE table_schema='public'
                           AND table_name=%s AND column_name='source_id'""", (t,))
            if cur.fetchone():
                cur.execute(f'SELECT source_id, count(*) FROM public."{t}" GROUP BY 1 ORDER BY 1')
                rec["by_source"] = dict(cur.fetchall())
            out[t] = rec
    return out


def graph_counts() -> dict:
    """Node and edge counts from Neo4j's count store: cheap, no scan."""
    drv = lib.neo4j_driver()
    try:
        with drv.session() as s:
            labels = [r[0] for r in s.run("CALL db.labels()")]
            types = [r[0] for r in s.run("CALL db.relationshipTypes()")]
            nodes = {l: s.run(f"MATCH (n:`{l}`) RETURN count(n)").single()[0] for l in labels}
            edges = {t: s.run(f"MATCH ()-[r:`{t}`]->() RETURN count(r)").single()[0]
                     for t in types}
        return {"nodes": nodes, "edges": edges}
    finally:
        drv.close()


def git_state() -> dict:
    def g(*a):
        return subprocess.run(["git", *a], cwd=lib.JARVIS_ROOT, capture_output=True,
                              text=True).stdout.strip()
    return {"commit": g("rev-parse", "HEAD"), "branch": g("rev-parse", "--abbrev-ref", "HEAD"),
            "uncommitted_files": len([l for l in g("status", "--porcelain").splitlines() if l])}


def snapshot(base: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%MZ")
    snap = base / "snapshots" / stamp
    snap.mkdir(parents=True, exist_ok=False)
    print(f"  snapshot {snap}")

    for db in DATABASES:
        pg_dump(db, snap / f"{db}.pgdump")
        print(f"    {db}.pgdump  {(snap / f'{db}.pgdump').stat().st_size / 1e6:,.0f} MB")

    rsync(lib.RAW, base / "raw")
    files = sorted(p for p in lib.RAW.rglob("*") if p.is_file())
    sums = {str(p.relative_to(lib.RAW)): sha256(p) for p in files}
    (snap / "raw.sha256").write_text("".join(f"{h}  {p}\n" for p, h in sums.items()))
    print(f"    raw/  {len(files):,} files, {sum(p.stat().st_size for p in files) / 1e9:.2f} GB "
          f"mirrored to {base / 'raw'}")

    extra = {}
    parquet = MSME / "out" / "firms.parquet"
    if parquet.exists():
        (base / "msme" / "out").mkdir(parents=True, exist_ok=True)
        subprocess.run(["cp", "-p", str(parquet), str(base / "msme" / "out" / "firms.parquet")],
                       check=True)
        extra["msme/out/firms.parquet"] = sha256(parquet)
    if (MSME / "cache").exists():
        rsync(MSME / "cache", base / "msme" / "cache")

    manifest = {
        "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git": git_state(),
        "postgres": {"server": PG_CONTAINER, "user": PG_USER,
                     "dumps": {db: {"file": f"{db}.pgdump",
                                    "sha256": sha256(snap / f"{db}.pgdump")}
                               for db in DATABASES},
                     "cosmetics_tables": table_counts()},
        "graph": graph_counts(),
        "raw": {"files": len(sums), "bytes": sum(p.stat().st_size for p in files),
                "sha256_file": "raw.sha256"},
        "extra_files": extra,
    }
    (snap / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, default=str))
    print(f"    MANIFEST.json  git {manifest['git']['commit'][:7]}, "
          f"{sum(t['rows'] for t in manifest['postgres']['cosmetics_tables'].values()):,} "
          f"Postgres rows, {sum(manifest['graph']['nodes'].values()):,} nodes")
    return snap


def verify(base: Path, snap: Path | None) -> int:
    """Restore the dump into a scratch database, compare every table's rows with the
    manifest, and re-hash the raw mirror and the parquet. Returns the number of problems."""
    snaps = sorted((base / "snapshots").iterdir()) if snap is None else [snap]
    snap = snaps[-1]
    m = json.loads((snap / "MANIFEST.json").read_text())
    problems = []
    print(f"  verifying {snap}")

    for db, d in m["postgres"]["dumps"].items():
        if sha256(snap / d["file"]) != d["sha256"]:
            problems.append(f"{d['file']} checksum differs from the manifest")

    psql = ["docker", "exec", PG_CONTAINER, "psql", "-U", PG_USER, "-d", "postgres", "-qtAc"]
    subprocess.run(psql + [f"DROP DATABASE IF EXISTS {SCRATCH_DB}"], check=True)
    subprocess.run(psql + [f"CREATE DATABASE {SCRATCH_DB}"], check=True)
    try:
        with (snap / "cosmetics.pgdump").open("rb") as f:
            r = subprocess.run(["docker", "exec", "-i", PG_CONTAINER, "pg_restore", "-U", PG_USER,
                                "-d", SCRATCH_DB, "--no-owner"], stdin=f, capture_output=True)
        if r.returncode:
            problems.append("pg_restore reported errors: " + r.stderr.decode()[-400:])
        got = table_counts(SCRATCH_DB)
        for t, want in m["postgres"]["cosmetics_tables"].items():
            have = got.get(t, {}).get("rows")
            if have != want["rows"]:
                problems.append(f"table {t}: {have} rows restored, {want['rows']} in manifest")
        print(f"    restored {len(got)} tables, "
              f"{sum(t['rows'] for t in got.values()):,} rows into {SCRATCH_DB}")
    finally:
        subprocess.run(psql + [f"DROP DATABASE IF EXISTS {SCRATCH_DB}"], check=True)

    bad = 0
    for line in (snap / "raw.sha256").read_text().splitlines():
        h, rel = line.split("  ", 1)
        p = base / "raw" / rel
        if not p.exists() or sha256(p) != h:
            bad += 1
            if bad <= 5:
                problems.append(f"raw mirror: {rel} missing or altered")
    print(f"    raw mirror: {m['raw']['files'] - bad:,} of {m['raw']['files']:,} files match")

    for rel, h in m["extra_files"].items():
        p = base / rel
        if not p.exists() or sha256(p) != h:
            problems.append(f"{rel} missing or altered")

    for p in problems:
        print("  ! " + p)
    print("  verify: " + ("OK" if not problems else f"{len(problems)} problem(s)"))
    return len(problems)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", nargs="?", default="snapshot", choices=["snapshot", "verify"])
    ap.add_argument("snapshot_dir", nargs="?", type=Path)
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR, help="backup root")
    a = ap.parse_args()
    base = a.dir.expanduser()
    if a.action == "verify":
        return 1 if verify(base, a.snapshot_dir) else 0
    snapshot(base)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
