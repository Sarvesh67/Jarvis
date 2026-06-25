"""Signal proposal orchestrator — drives the "agent proposes, you approve" loop.

Pipeline (per the plan's core diagram):

    seed ─► headless Hermes (jarvis-signals skill) writes a proposal JSON to proposals/inbox/
         ─► this orchestrator auto-backtests it (deterministic, 0 LLM)
         ─► moves it to proposals/pending/ with stats attached, for your review

Stdlib-only (subprocess + json + pathlib) so it runs under any venv; it SHELLS OUT to:
  - hermes            (headless agent run — generates the proposal)
  - the marketdata venv `backtest.engine`  (the deterministic evidence)

Modes:
  propose         seed → agent → inbox → backtest → pending   (the full loop)
  process-inbox   backtest whatever is already in inbox/ → pending  (deterministic; no agent —
                  used for testing the loop and for re-backtesting after a formula tweak)

The proposal queue lives under cognee/data/<project>/proposals/{inbox,pending,approved,rejected}/.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
COGNEE_DATA = REPO / "cognee" / "data"
HERMES_BIN = pathlib.Path.home() / ".local" / "bin" / "hermes"
MARKETDATA_PY = REPO / "marketdata" / ".venv" / "bin" / "python"

REQUIRED_FIELDS = ("id", "direction", "trigger")


def _say(msg: str):
    print(msg, flush=True)


def proposals_dir(project: str) -> pathlib.Path:
    return COGNEE_DATA / project / "proposals"


def _ensure_dirs(project: str) -> dict:
    base = proposals_dir(project)
    dirs = {k: base / k for k in ("inbox", "pending", "approved", "rejected")}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs


def new_proposal_id(project: str, seed: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (seed or "").lower()).strip("-")[:24] or "signal"
    return f"sig_{int(time.time())}_{slug}"


# ─── Agent step ───────────────────────────────────────────────────────────────

def _build_prompt(pid: str, seed: str, inbox_file: pathlib.Path, horizon: str,
                  direction: str) -> str:
    return f"""You are proposing ONE testable trading signal for the `hedgefund` project, to be \
backtested and reviewed by a human. Use the jarvis-signals skill.

SEED (the event/news to build a signal around):
{seed}

Steps:
1. Query the hedgefund knowledge graph (jarvis-knowledge / query.py) for the entities, events and \
relationships around this seed.
2. Reason over the graph context and form ONE concrete, testable signal hypothesis.
3. Write the proposal as a SINGLE valid JSON object to EXACTLY this path (overwrite if present):
   {inbox_file}

The JSON MUST have this shape:
{{
  "id": "{pid}",
  "seed": {json.dumps(seed)},
  "thesis": "<one-sentence hypothesis>",
  "direction": "{direction}",            // "long" or "short"
  "horizon": "{horizon}",                // e.g. "5d"
  "universe": ["<NSE ticker, e.g. TATAMOTORS.NS>"],
  "trigger": {{                          // MACHINE-EXECUTABLE — how to find historical instances
    "match": {{
      "tickers": ["<ticker(s) the signal trades>"],
      "entityAny": ["<entity names that must appear, e.g. Morgan Stanley>"],
      "keywordAny": ["<keywords, e.g. upgrade, overweight>"]
    }}
  }},
  "rationale": "<why, grounded in the graph context>",
  "sourceNodes": []
}}

Rules:
- Use ONLY tickers/entities that actually appear in the graph context — never invent a ticker.
- `trigger.match` is what a deterministic engine will re-run across history, so make it precise.
- Write ONLY the JSON file. Do not print the JSON in your reply; just confirm you wrote it.
"""


def run_agent(project: str, prompt: str, timeout: int = 300) -> str:
    if not HERMES_BIN.exists():
        raise RuntimeError(f"hermes not found at {HERMES_BIN}")
    home = str(pathlib.Path.home())
    env = {**_os_environ(), "HOME": home,
           "PATH": f"{home}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:"
                   + _os_environ().get("PATH", "")}
    proc = subprocess.run(
        [str(HERMES_BIN), "-p", project, "-c", f"signals-{project}", "-z", prompt],
        capture_output=True, text=True, env=env, cwd=home, timeout=timeout,
    )
    return (proc.stdout or "") + (("\n" + proc.stderr) if proc.returncode else "")


def _os_environ() -> dict:
    import os
    return dict(os.environ)


# ─── Proposal capture / validation ────────────────────────────────────────────

def _extract_json(text: str) -> dict | None:
    """Fallback: pull the first balanced JSON object out of agent stdout (if it didn't write
    the file). Best-effort — the file path is the primary, robust contract."""
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except Exception:  # noqa: BLE001
                        break
        start = text.find("{", start + 1)
    return None


def validate(proposal: dict) -> list[str]:
    errs = [f"missing '{f}'" for f in REQUIRED_FIELDS if not proposal.get(f)]
    if proposal.get("direction") not in (None, "long", "short"):
        errs.append("direction must be long|short")
    trig = proposal.get("trigger") or {}
    if "match" not in trig:
        errs.append("trigger.match required")
    return errs


# ─── Backtest step (shell to the marketdata venv) ─────────────────────────────

def backtest(proposal: dict, project: str) -> dict:
    if not MARKETDATA_PY.exists():
        return {"error": "marketdata venv not found"}
    tmp = proposals_dir(project) / "inbox" / f".bt_{proposal['id']}.json"
    tmp.write_text(json.dumps(proposal))
    try:
        r = subprocess.run(
            [str(MARKETDATA_PY), "-m", "backtest.engine", "--project", project,
             "--proposal", str(tmp)],
            cwd=str(REPO), capture_output=True, text=True, timeout=120,
        )
    finally:
        tmp.unlink(missing_ok=True)
    if r.returncode != 0:
        return {"error": (r.stderr or "").strip()[-300:]}
    try:
        return json.loads(r.stdout)
    except Exception as e:  # noqa: BLE001
        return {"error": f"bad backtest output: {e}"}


# ─── Finalize ─────────────────────────────────────────────────────────────────

def finalize(proposal: dict, bt: dict, dirs: dict) -> pathlib.Path:
    proposal["backtest"] = bt
    proposal["status"] = "pending"
    proposal.setdefault("createdAt", _now_iso())
    out = dirs["pending"] / f"{proposal['id']}.json"
    out.write_text(json.dumps(proposal, indent=2))
    return out


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def process_inbox(project: str) -> list[dict]:
    """Backtest every proposal in inbox/, move each to pending/ with stats. Returns summaries."""
    dirs = _ensure_dirs(project)
    out = []
    for f in sorted(dirs["inbox"].glob("*.json")):
        if f.name.startswith(".bt_"):
            continue
        try:
            proposal = json.loads(f.read_text())
        except Exception as e:  # noqa: BLE001
            _say(f"  skip {f.name}: unreadable ({e})")
            continue
        errs = validate(proposal)
        if errs:
            _say(f"  invalid {f.name}: {errs} — left in inbox")
            continue
        _say(f"  backtesting {proposal['id']}…")
        bt = backtest(proposal, project)
        dest = finalize(proposal, bt, dirs)
        f.unlink(missing_ok=True)
        stats = (bt.get("stats") or {})
        _say(f"    → pending: n={stats.get('n')} hitRate={stats.get('hitRate')} "
             f"avgFwdRet={stats.get('avgFwdRet')}")
        out.append({"id": proposal["id"], "stats": stats, "path": str(dest)})
    return out


def propose(project: str, seed: str, *, horizon: str = "5d", direction: str = "long") -> dict:
    dirs = _ensure_dirs(project)
    pid = new_proposal_id(project, seed)
    inbox_file = dirs["inbox"] / f"{pid}.json"
    _say(f"proposing {pid} (seed: {seed[:60]}…)")
    prompt = _build_prompt(pid, seed, inbox_file, horizon, direction)

    _say("running agent (headless hermes)…")
    stdout = run_agent(project, prompt)

    proposal = None
    if inbox_file.exists():
        try:
            proposal = json.loads(inbox_file.read_text())
            _say("agent wrote the proposal file")
        except Exception as e:  # noqa: BLE001
            _say(f"inbox file unreadable ({e}); trying stdout fallback")
    if proposal is None:
        proposal = _extract_json(stdout)
        if proposal is not None:
            _say("recovered proposal JSON from agent stdout")
    if proposal is None:
        _say("PROPOSE_FAILED: agent produced no proposal")
        _say(f"--- agent output (tail) ---\n{stdout[-800:]}")
        return {"ok": False, "error": "no proposal produced"}

    proposal["id"] = pid  # enforce our id regardless of what the agent wrote
    proposal.setdefault("seed", seed)
    proposal.setdefault("direction", direction)
    proposal.setdefault("horizon", horizon)
    errs = validate(proposal)
    if errs:
        _say(f"PROPOSE_FAILED: invalid proposal {errs}")
        (dirs["inbox"] / f"{pid}.json").write_text(json.dumps(proposal, indent=2))
        return {"ok": False, "error": f"invalid proposal: {errs}"}

    _say("backtesting…")
    bt = backtest(proposal, project)
    dest = finalize(proposal, bt, dirs)
    inbox_file.unlink(missing_ok=True)
    stats = bt.get("stats") or {}
    _say(f"PROPOSE_DONE: {pid} → pending  n={stats.get('n')} hitRate={stats.get('hitRate')}")
    return {"ok": True, "id": pid, "stats": stats, "path": str(dest)}


def _main(argv=None):
    ap = argparse.ArgumentParser(prog="signals.orchestrate")
    sub = ap.add_subparsers(required=True)

    p = sub.add_parser("propose")
    p.add_argument("--project", required=True)
    p.add_argument("--seed", required=True)
    p.add_argument("--horizon", default="5d")
    p.add_argument("--direction", default="long", choices=["long", "short"])
    p.set_defaults(fn=lambda a: propose(a.project, a.seed, horizon=a.horizon, direction=a.direction))

    q = sub.add_parser("process-inbox")
    q.add_argument("--project", required=True)
    q.set_defaults(fn=lambda a: {"processed": process_inbox(a.project)})

    args = ap.parse_args(argv)
    res = args.fn(args)
    if not res.get("ok", True) and "error" in res:
        sys.exit(1)


if __name__ == "__main__":
    _main()
