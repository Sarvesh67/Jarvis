"""Parse the official NIC-2008 master (Excel/CSV preferred; PDF fallback) into a structured table
and merge it into the NIC map so every code has an authoritative label + hierarchy.

    msme/.venv/bin/python -m msme.nic_master --file nic2008.xlsx   # or .csv / .pdf

Output: msme/data/nic2008.csv  (columns: code, label, level, parent).
This is the DETERMINISTIC parse — it assigns NO materials. The material inference (outputs/inputs)
is a separate, cached LLM step (`nic.enrich_all`). Merging here only fills labels + SUBCLASS_OF
hierarchy into cache/nic_map.json (outputs/inputs left empty for enrichment; curated seeds win).

PDF note: NIC-2008 PDFs are indented hierarchical text, not tables. We extract text, then classify
each line by the digit-length of its leading code (2=Division … 5=Subclass) and rebuild parents by
prefix. Lines that don't match are collected to data/nic_unparsed.txt for a manual/LLM pass. Prefer
the govt Excel/CSV if you can get it — skip PDF parsing entirely.
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import re

BASE = pathlib.Path(__file__).resolve().parent
DATA = BASE / "data"
DATA.mkdir(exist_ok=True)
OUT_CSV = DATA / "nic2008.csv"

# Confirmed against NIC_Sector.pdf "Detailed Structure": section/division headers use words (case
# mangled by a small-caps font), groups/classes/sub-classes lead with 3/4/5-digit codes.
_SEC_LINE = re.compile(r"^section\s+([A-U])\s*:\s*(.+)$", re.I)
_DIV_LINE = re.compile(r"^divi[s]?ion\s+(\d{2})\s*:\s*(.+)$", re.I)
_CODE_LINE = re.compile(r"^(\d{3,5})\s+(.+)$")
# Page header repeats on every detailed-structure page; drop it (and description prose markers).
_SKIP_EXACT = {"detailed structure", "group class sub- description", "class",
               "group class sub-description", "sub-"}
_PROSE_PREFIX = ("this class", "this group", "this division", "this sub", "this section",
                 "includes", "excludes", "- ", "note", "see ")


def _level_of(code: str) -> str:
    if code.isalpha():
        return "section"
    return {2: "division", 3: "group", 4: "class", 5: "subclass"}.get(len(code), "?")


def _parent_of(code: str) -> str:
    if code.isalpha() or len(code) <= 2:
        return ""          # section/division parents need the section map; left blank in prototype
    return code[:-1]


def _rows_from_records(records) -> list[dict]:
    """records: iterable of (code, label) — from a CSV/Excel with those two columns."""
    out = []
    for code, label in records:
        code = str(code).strip()
        label = str(label).strip()
        if not code or not label:
            continue
        out.append({"code": code, "label": label, "level": _level_of(code),
                    "parent": _parent_of(code)})
    return out


def parse_csv(path: pathlib.Path) -> list[dict]:
    with open(path, newline="") as f:
        rdr = csv.reader(f)
        header = next(rdr, None)
        # find code + description columns heuristically
        ci, li = 0, 1
        if header:
            low = [h.lower() for h in header]
            ci = next((i for i, h in enumerate(low) if "code" in h or "nic" in h), 0)
            li = next((i for i, h in enumerate(low) if "desc" in h or "label" in h or "activ" in h), 1)
        return _rows_from_records((r[ci], r[li]) for r in rdr if len(r) > max(ci, li))


def parse_excel(path: pathlib.Path) -> list[dict]:
    import pandas as pd
    df = pd.read_excel(path, dtype=str).fillna("")
    cols = {c.lower(): c for c in df.columns}
    cc = next((cols[c] for c in cols if "code" in c or "nic" in c), df.columns[0])
    lc = next((cols[c] for c in cols if "desc" in c or "label" in c or "activ" in c), df.columns[1])
    return _rows_from_records(zip(df[cc], df[lc]))


def parse_pdf(path: pathlib.Path) -> list[dict]:
    """Parse the 'Detailed Structure' section. Each Section/Division header and each 3/4/5-digit
    code line becomes a row; a non-code line immediately following a code line is treated as a
    wrapped label continuation (unless it's a description-prose marker, which is skipped). This is
    deliberately conservative — labels may occasionally truncate, but never absorb 'This class
    includes…' prose. The authoritative activity text also comes from the Udyam Description field."""
    import pdfplumber
    rows: list[dict] = []
    cur: dict | None = None  # last code/header row, for continuation attachment
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            for raw in (page.extract_text() or "").splitlines():
                line = raw.strip()
                if not line or line.lower() in _SKIP_EXACT:
                    continue
                m = _SEC_LINE.match(line)
                if m:
                    cur = {"code": m.group(1), "label": m.group(2).strip(),
                           "level": "section", "parent": ""}
                    rows.append(cur); continue
                m = _DIV_LINE.match(line)
                if m:
                    cur = {"code": m.group(1), "label": m.group(2).strip(),
                           "level": "division", "parent": ""}
                    rows.append(cur); continue
                m = _CODE_LINE.match(line)
                if m:
                    code = m.group(1)
                    cur = {"code": code, "label": m.group(2).strip(),
                           "level": _level_of(code), "parent": _parent_of(code)}
                    rows.append(cur); continue
                # continuation: attach a wrapped label to the current code, skip description prose
                if cur is not None and not line.lower().startswith(_PROSE_PREFIX):
                    cur["label"] = (cur["label"] + " " + line).strip()
                else:
                    cur = None  # a prose paragraph ends the current label's continuation window
    return rows


def _validate(rows: list[dict]) -> None:
    codes = {r["code"] for r in rows}
    subclasses = [r for r in rows if r["level"] == "subclass"]
    orphans = [r["code"] for r in subclasses if r["parent"] and r["parent"] not in codes]
    print(f"parsed {len(rows)} rows: {len(subclasses)} subclasses; "
          f"{len(orphans)} orphan subclasses (missing parent)" +
          (f" e.g. {orphans[:5]}" if orphans else ""))


def merge_into_nic_map(rows: list[dict]) -> None:
    """Fill labels + hierarchy into cache/nic_map.json (curated seed entries are preserved)."""
    from . import nic as N
    nm = N._materialize("nic_map.json")
    nic = nm.setdefault("nic", {})
    added = 0
    for r in rows:
        if r["level"] != "subclass":
            continue
        e = nic.setdefault(r["code"], {"outputs": [], "inputs": [], "keywords": []})
        if not e.get("label"):
            e["label"] = r["label"]
        e["parent"] = r["parent"]
        added += 1
    (N.CACHE / "nic_map.json").write_text(json.dumps(nm, indent=2))
    print(f"merged {added} subclass codes into cache/nic_map.json (materials left for enrichment)")


def run(path: str):
    p = pathlib.Path(path)
    if not p.exists():
        raise SystemExit(f"file not found: {p}")
    ext = p.suffix.lower()
    rows = (parse_excel(p) if ext in {".xlsx", ".xls"} else
            parse_pdf(p) if ext == ".pdf" else parse_csv(p))
    _validate(rows)
    with open(OUT_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["code", "label", "level", "parent"])
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {OUT_CSV}")
    merge_into_nic_map(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, help="NIC-2008 master: .xlsx / .csv / .pdf")
    args = ap.parse_args()
    run(args.file)


if __name__ == "__main__":
    main()
