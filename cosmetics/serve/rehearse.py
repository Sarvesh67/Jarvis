"""Rehearse the investor demo against the LIVE demo, the way the page asks it.

    .venv/bin/python -m serve.rehearse                 # http://127.0.0.1:8099
    .venv/bin/python -m serve.rehearse --base https://demo.lattix.co.in

`make demo-check` proves each template still routes. This proves the demo itself: the
agent, its skill, the server and the graph, one question after another, in the order
DEMO_SCRIPT.md walks them (validate/investor_demo.yaml). The skill once lost the distance
templates while every routing test stayed green; only asking the live demo shows that.

Each question counts against the demo's daily budget, and the burst cap is 20 a minute,
so steps are asked one at a time with a short gap.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
STEPS = yaml.safe_load((ROOT / "validate" / "investor_demo.yaml").read_text())


def ask(base: str, question: str) -> dict:
    out = {"reply": "", "evidence": [], "error": None}
    with httpx.stream("POST", f"{base}/api/ask", json={"question": question, "history": []},
                      timeout=180) as r:
        if r.status_code != 200:
            out["error"] = f"HTTP {r.status_code}: {r.read().decode()[:160]}"
            return out
        for line in r.iter_lines():
            if line.startswith("data: "):
                ev = json.loads(line[6:])
                if ev["type"] == "done":
                    out.update(reply=ev["reply"], evidence=ev["evidence"])
                elif ev["type"] == "error":
                    out["error"] = ev["message"]
    return out


def check(step: dict, res: dict) -> list[str]:
    if res["error"]:
        return [res["error"]]
    problems = []
    ev = res["evidence"]
    want = step.get("template")
    used = [e for e in ev if want is None or e.get("template") == want]
    if not used:
        problems.append(f"did not use {want} (used {[e.get('template') for e in ev]})")
    rows = max((len(e.get("rows") or []) for e in used), default=0)
    if rows < step.get("min_rows", 1):
        problems.append(f"{rows} rows, wanted >= {step['min_rows']}")
    first = next((e["rows"][0] for e in used if e.get("rows")), {})
    if step.get("expect_grade") and first.get("grade") != step["expect_grade"]:
        problems.append(f"grade {first.get('grade')!r}, wanted {step['expect_grade']!r}")
    if step.get("expect_km") and first.get("km") is None:
        problems.append("rows carry no distance")
    for t in step.get("expect_text") or []:
        if t.lower() not in res["reply"].lower():
            problems.append(f"reply lacks {t!r}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8099")
    ap.add_argument("--gap", type=float, default=2.0, help="seconds between questions")
    a = ap.parse_args()
    failed = 0
    for s in STEPS:
        t0 = time.time()
        try:
            res = ask(a.base, s["ask"])
        except Exception as e:                                # pragma: no cover
            res = {"reply": "", "evidence": [], "error": f"{type(e).__name__}: {e}"}
        secs = time.time() - t0
        problems = check(s, res)
        failed += bool(problems)
        mark = "FAIL" if problems else "ok  "
        print(f"  {mark} step {s['step']:>2}  {secs:5.1f}s  {s['ask']}")
        for p in problems:
            print(f"             - {p}")
        time.sleep(a.gap)
    print(f"\n  {len(STEPS) - failed}/{len(STEPS)} steps as the script expects"
          + ("" if not failed else " — fix or re-script the failing steps before presenting"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
