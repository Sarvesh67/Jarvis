"""Reference data — pincode -> district -> state, and the Cluster spine.

Source: GeoNames postal-code export for IN (CC BY 4.0), the same pincode/district/state
mapping India Post publishes, in a stable, versioned, machine-readable form.
Spec §7.2 depends on this: every address is geo-resolved by its LAST 6-digit token.
"""
from __future__ import annotations

import csv
import io
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib  # noqa: E402

SOURCE = "s15_ref"
URL = "https://download.geonames.org/export/zip/IN.zip"


def main() -> int:
    f = lib.Fetcher(SOURCE, delay=0.0)
    try:
        r = f.get(URL)
    finally:
        f.close()
    lib.land(SOURCE, URL, r.content, suffix=".zip", key="geonames_IN")

    rows: dict[str, dict] = {}
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        with z.open("IN.txt") as fh:
            for rec in csv.reader(io.TextIOWrapper(fh, "utf-8"), delimiter="\t"):
                if len(rec) < 6:
                    continue
                pin, place, state, district = rec[1].strip(), rec[2].strip(), rec[3].strip(), rec[5].strip()
                if not (pin.isdigit() and len(pin) == 6):
                    continue
                # first record for a pincode wins; GeoNames lists several places per pin
                rows.setdefault(pin, {"pincode": pin, "district": district or None,
                                      "state": state or None, "place": place or None})
    n = lib.upsert("ref_pincode", list(rows.values()), "pincode")
    print(f"reference: {n} pincodes loaded")

    # Cluster nodes come straight from the ontology; write them to Postgres-free YAML use
    cl = lib.clusters()
    wedge = [k for k, v in cl.items() if v.get("wedge")]
    print(f"clusters: {len(cl)} defined, {len(wedge)} wedge -> {', '.join(wedge)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
