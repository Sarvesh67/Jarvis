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

Off-machine, under OFFSITE_DIR (default iCloud Drive/Backups/lattix-cosmetics), the same
root as encrypted tar.gz chunks, readable on any machine with openssl and the passphrase:

  files-<UTC time>.tar.gz.enc       raw/ and msme/ files new or changed since the previous
                                    files chunk. Append-only like the mirror: restore all,
                                    oldest first.
  snapshot-<UTC time>.tar.gz.enc    one snapshots/<UTC time>/ directory. Restore the newest.
  SHA256SUMS, KEYCHECK.enc, README.txt

The passphrase is read from the login keychain (item KEYCHAIN_ITEM, set once with `make
backup-passphrase`) or from the file named by COSMETICS_BACKUP_PASSFILE. It reaches
openssl through a pipe, never argv or the environment. Its other copy belongs in the
owner's password manager: the keychain is lost with the Mac.

  python -m pipeline.backup                 take a snapshot, then copy it off-machine
  python -m pipeline.backup verify [DIR]    restore the newest (or DIR) snapshot into a
                                            scratch database and check it against the
                                            manifest; re-hash the raw mirror
  python -m pipeline.backup offsite         copy the newest snapshot off-machine
  python -m pipeline.backup verify-offsite  decrypt the off-machine copy into a scratch
                                            root and run verify on it
  python -m pipeline.backup restore-offsite DIR   decrypt the off-machine copy into DIR,
                                            a new backup root (DATA.md, "Restore")
  python -m pipeline.backup offsite-status  which chunks iCloud has finished uploading

Secrets (.env files) are deliberately not copied. DATA.md lists the names they need.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
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

ICLOUD = Path("~/Library/Mobile Documents/com~apple~CloudDocs").expanduser()
OFFSITE_DIR = Path(lib.ENV.get("COSMETICS_OFFSITE_DIR",
                               ICLOUD / "Backups" / "lattix-cosmetics")).expanduser()
KEYCHAIN_ITEM = "lattix-cosmetics-backup"
# OpenSSL's own salted format, so a restore needs no code from this repo
CIPHER = ["-aes-256-cbc", "-pbkdf2", "-iter", "600000", "-md", "sha256"]
KEYCHECK = b"lattix-cosmetics backup key check\n"


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


README = """\
Lattix cosmetics: encrypted off-machine backup
==============================================
Written by cosmetics/pipeline/backup.py (`make backup`). Nothing here can be read without
the passphrase. It is kept in the owner's password manager, and on the Mac in the login
keychain as "lattix-cosmetics-backup".

  files-<UTC>.tar.gz.enc     raw/ payloads and msme/ inputs, new or changed since the
                             previous files chunk. Restore every one, oldest first.
  snapshot-<UTC>.tar.gz.enc  one database snapshot: pg_dump of cosmetics and jarvis,
                             MANIFEST.json, raw.sha256. Restore the newest.
  SHA256SUMS                 checksums of the encrypted files (shasum -a 256 -c SHA256SUMS)
  KEYCHECK.enc               lets the script refuse a passphrase that differs from this copy's

Format: tar.gz, encrypted with
  openssl enc -aes-256-cbc -pbkdf2 -iter 600000 -md sha256      (OpenSSL 1.1.1+ or LibreSSL)

Restore with the repo:  cd cosmetics && .venv/bin/python -m pipeline.backup restore-offsite DIR
Restore without it (openssl asks for the passphrase once per file):
  mkdir restored && cd restored
  for f in "<this folder>"/files-*.tar.gz.enc; do
    openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -md sha256 -in "$f" | tar -xzf -
  done
  openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -md sha256 \\
    -in "<this folder>/<the newest snapshot-*.tar.gz.enc>" | tar -xzf -
The result is a backup root. Then follow cosmetics/DATA.md, "Restore and rebuild".
"""


def passphrase() -> str | None:
    """From the file COSMETICS_BACKUP_PASSFILE names, else the login keychain. Never printed."""
    pf = lib.ENV.get("COSMETICS_BACKUP_PASSFILE")
    if pf:
        return Path(pf).expanduser().read_text().strip("\n") or None
    r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_ITEM, "-w"],
                       capture_output=True, text=True)
    return (r.stdout.strip("\n") or None) if r.returncode == 0 else None


def openssl(args: list[str], secret: str, **kw) -> subprocess.Popen:
    """openssl enc with the passphrase on a pipe: never in argv, the environment or a file."""
    r, w = os.pipe()
    os.write(w, secret.encode())
    os.close(w)
    try:
        return subprocess.Popen(["openssl", "enc", *args, *CIPHER, "-pass", f"fd:{r}"],
                                pass_fds=(r,), **kw)
    finally:
        os.close(r)


def seal(base: Path, rels: list[str], out: Path, secret: str) -> None:
    """tar.gz the files (paths relative to base) through openssl into out."""
    with out.open("wb") as f:
        p = openssl(["-salt"], secret, stdin=subprocess.PIPE, stdout=f)
        with tarfile.open(fileobj=p.stdin, mode="w|gz", compresslevel=6) as tf:
            for rel in rels:
                tf.add(base / rel, arcname=rel, recursive=False)
        p.stdin.close()
        if p.wait():
            raise RuntimeError(f"openssl failed writing {out.name}")


def unseal(chunk: Path, target: Path, secret: str) -> None:
    p = openssl(["-d", "-in", str(chunk)], secret, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL)
    try:
        with tarfile.open(fileobj=p.stdout, mode="r|gz") as tf:
            tf.extractall(target, filter="data")     # refuses paths that leave target
        while p.stdout.read(1 << 16):                # tar padding: let openssl finish
            pass
    except (tarfile.TarError, OSError, EOFError) as e:
        p.kill()
        p.wait()
        raise RuntimeError(f"{chunk.name}: wrong passphrase or damaged file ({e})") from None
    finally:
        p.stdout.close()
    if p.wait():
        raise RuntimeError(f"{chunk.name}: wrong passphrase or damaged file")


def check_key(dest: Path, secret: str, create: bool = False) -> bool:
    """The first copy writes KEYCHECK.enc and later ones must decrypt it, so one
    off-machine copy never needs two passphrases to restore."""
    kc = dest / "KEYCHECK.enc"
    if not kc.exists() and create:
        p = openssl(["-salt", "-out", str(kc)], secret, stdin=subprocess.PIPE)
        p.communicate(KEYCHECK)
        return p.returncode == 0
    p = openssl(["-d", "-in", str(kc)], secret, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL)
    out, _ = p.communicate()
    return p.returncode == 0 and out == KEYCHECK


def read_sums(dest: Path) -> dict[str, str]:
    f = dest / "SHA256SUMS"
    lines = f.read_text().splitlines() if f.exists() else []
    return {name: h for h, name in (l.split("  ", 1) for l in lines if l)}


def offsite(base: Path, dest: Path) -> int:
    """Copy the newest snapshot, and every raw/ or msme/ file not copied before, to dest
    as encrypted chunks. Returns the number of problems."""
    secret = passphrase()
    if not secret:
        print("  ! NOT copied off-machine: no passphrase. Run `make backup-passphrase` once.")
        return 1
    if dest.is_relative_to(ICLOUD) and not ICLOUD.exists():
        print(f"  ! NOT copied off-machine: {ICLOUD} is missing. Is iCloud Drive on?")
        return 1
    dest.mkdir(parents=True, exist_ok=True)
    if not check_key(dest, secret, create=True):
        print(f"  ! NOT copied off-machine: the passphrase is not the one {dest} was made with")
        return 1
    (dest / "README.txt").write_text(README)

    # what has been copied where, so each run sends only what is new
    state_file = base / "offsite.json"
    states = json.loads(state_file.read_text()) if state_file.exists() else {}
    st = states.setdefault(str(dest), {"chunks": {}, "files": {}})
    gone = {c for c in st["chunks"] if not (dest / c).exists()}   # deleted there: send again
    st["chunks"] = {c: v for c, v in st["chunks"].items() if c not in gone}
    st["files"] = {r: v for r, v in st["files"].items() if v[1] not in gone}

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    while (dest / f"files-{stamp}.tar.gz.enc").exists():   # restore order is name order
        time.sleep(1)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    sig = {}
    for top in ("raw", "msme"):
        for p in (base / top).rglob("*"):
            if p.is_file():
                s = p.stat()
                sig[str(p.relative_to(base))] = f"{s.st_size}:{int(s.st_mtime)}"
    todo = []
    new = sorted(r for r, v in sig.items() if st["files"].get(r, [None])[0] != v)
    if new:
        todo.append((f"files-{stamp}.tar.gz.enc", new))
    snap = sorted((base / "snapshots").iterdir())[-1]
    if f"snapshot-{snap.name}.tar.gz.enc" not in st["chunks"]:
        todo.append((f"snapshot-{snap.name}.tar.gz.enc",
                     sorted(str(p.relative_to(base)) for p in snap.iterdir())))

    staging = base / "offsite-staging"
    staging.mkdir(exist_ok=True)
    for name, rels in todo:
        tmp = staging / name
        seal(base, rels, tmp, secret)
        h, size = sha256(tmp), tmp.stat().st_size
        shutil.move(tmp, dest / name)     # a rename on one volume: never seen half-written
        sums = {**{k: v for k, v in read_sums(dest).items() if (dest / k).exists()}, name: h}
        (dest / "SHA256SUMS").write_text("".join(f"{v}  {k}\n" for k, v in sorted(sums.items())))
        st["chunks"][name] = {"sha256": h, "bytes": size, "files": len(rels), "made_at": stamp}
        if name.startswith("files-"):
            st["files"].update({r: [sig[r], name] for r in rels})
        state_file.write_text(json.dumps(states, indent=1))
        print(f"    {name}  {len(rels):,} files, {size / 1e6:,.0f} MB")
    if not todo:
        print("    nothing new to copy")
    total = sum(c["bytes"] for c in st["chunks"].values())
    print(f"  off-machine: {len(st['chunks'])} chunks, {total / 1e9:.2f} GB in {dest}")
    return 0


def restore_offsite(dest: Path, target: Path, secret: str) -> None:
    """Decrypt every files chunk, oldest first, then the newest snapshot chunk, into
    target, which becomes a backup root."""
    sums = read_sums(dest)
    files = sorted(dest.glob("files-*.tar.gz.enc"))
    snaps = sorted(dest.glob("snapshot-*.tar.gz.enc"))
    if not snaps:
        raise RuntimeError(f"no snapshot chunk in {dest}")
    if not check_key(dest, secret):
        raise RuntimeError(f"the passphrase is not the one {dest} was made with")
    for c in files + snaps[-1:]:
        if sha256(c) != sums.get(c.name):
            raise RuntimeError(f"{c.name}: checksum differs from SHA256SUMS")
        unseal(c, target, secret)
        print(f"    decrypted {c.name}")


def verify_offsite(base: Path, dest: Path) -> int:
    """Decrypt the off-machine copy into a scratch root and run verify on it: proof that it
    alone rebuilds the graph."""
    secret = passphrase()
    if not secret:
        print("  ! no passphrase. Run `make backup-passphrase` once.")
        return 1
    scratch = Path(tempfile.mkdtemp(prefix="offsite-check-", dir=base))
    print(f"  decrypting {dest} into {scratch}")
    try:
        restore_offsite(dest, scratch, secret)
        return verify(scratch, None)
    except RuntimeError as e:
        print(f"  ! {e}\n  verify: 1 problem(s)")
        return 1
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def offsite_status(dest: Path) -> int:
    """Which chunks iCloud Drive has finished uploading. Returns how many have not."""
    pending = 0
    for c in sorted(dest.glob("*.enc")):
        r = subprocess.run(["fileproviderctl", "evaluate", str(c)], capture_output=True,
                           text=True)
        up = "isUploaded = 1" in r.stdout
        pending += not up
        print(f"    {'uploaded' if up else 'NOT YET '}  {c.name}")
    print(f"  {pending} file(s) still waiting to upload" if pending else "  all uploaded")
    return pending


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", nargs="?", default="snapshot",
                    choices=["snapshot", "verify", "offsite", "verify-offsite",
                             "restore-offsite", "offsite-status"])
    ap.add_argument("path", nargs="?", type=Path,
                    help="verify: the snapshot to check; restore-offsite: the new backup root")
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR, help="backup root")
    ap.add_argument("--offsite-dir", type=Path, default=OFFSITE_DIR)
    a = ap.parse_args()
    base, dest = a.dir.expanduser(), a.offsite_dir.expanduser()
    if a.action == "verify":
        return 1 if verify(base, a.path) else 0
    if a.action == "offsite":
        return 1 if offsite(base, dest) else 0
    if a.action == "verify-offsite":
        return 1 if verify_offsite(base, dest) else 0
    if a.action == "offsite-status":
        return 1 if offsite_status(dest) else 0
    if a.action == "restore-offsite":
        target, secret = a.path, passphrase()
        if target is None or (target.exists() and any(target.iterdir())):
            ap.error("restore-offsite needs a new or empty directory")
        if not secret:
            ap.error("no passphrase: run `make backup-passphrase`, or set COSMETICS_BACKUP_PASSFILE")
        target.mkdir(parents=True, exist_ok=True)
        try:
            restore_offsite(dest, target, secret)
        except RuntimeError as e:
            print(f"  ! {e}")
            return 1
        print(f"  restored. Check it: python -m pipeline.backup verify --dir {target}")
        return 0
    snapshot(base)
    return 1 if offsite(base, dest) else 0


if __name__ == "__main__":
    raise SystemExit(main())
