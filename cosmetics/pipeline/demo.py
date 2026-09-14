"""Print the demo script — the questions in order, each with what to say while it runs.

The set lives in `validate/demo_questions.yaml` and is tested by `tests/test_demo.py`,
so what this prints is the same thing CI asserts still works. Reading it from one file
is the point: a demo script kept separately drifts from the demo.

    python -m pipeline.demo            # the script
    python -m pipeline.demo --run      # run each question and show the first rows
"""
from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEMO = yaml.safe_load((ROOT / "validate" / "demo_questions.yaml").read_text())


def wrap(text: str, indent: str = "      ") -> str:
    return textwrap.fill(" ".join((text or "").split()), width=88,
                         initial_indent=indent, subsequent_indent=indent)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true", help="also run each question")
    ap.add_argument("--rows", type=int, default=3)
    a = ap.parse_args()

    print(f"\n  {len(DEMO)} demo questions, "
          f"{sum(len(d['asks']) for d in DEMO)} tested phrasings\n")
    for i, d in enumerate(DEMO, 1):
        print(f"  {i}. {d['asks'][0]}")
        print(f"      -> {d['template']}  (also tested: "
              f"{len(d['asks']) - 1} other phrasing"
              f"{'s' if len(d['asks']) != 2 else ''})")
        print(wrap(d["caveat"]))
        if a.run:
            from pipeline import router
            res = router.answer(d["asks"][0], limit=50)
            rows = res.get("rows") or []
            print(f"      [{len(rows)} row(s)]")
            for r in rows[:a.rows]:
                print("      ", {k: (str(v)[:44] if v is not None else None)
                                 for k, v in r.items()})
            if not rows and res.get("empty_reason"):
                print(wrap(res["empty_reason"], "      > "))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
