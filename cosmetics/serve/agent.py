"""The demo's answering agent — the same contract the Jarvis console chat runs on.

The console's cosmetics chat is a Hermes agent carrying the `jarvis-cosmetics-search`
skill: it CHOOSES a reviewed template, runs it through `pipeline.router`, and then
reasons over the returned JSON into an answer. The tool never writes prose and the model
never writes a query.

This module runs that same loop, and takes its instructions from the skill file itself
rather than from a copy. If the skill changes, the demo changes with it — a paraphrase
kept in here would drift from the product within a week, and the demo would stop being
the product.

What is deliberately NOT carried over from Hermes: the terminal, browser, filesystem and
MCP toolsets. The console agent has them because a person it trusts is driving it. Here
the only tool is the whitelisted query router.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import lib  # noqa: E402
from pipeline import router as _router  # noqa: E402

# Matches `model.default` in ~/.hermes/profiles/cosmetics/config.yaml.
MODEL = os.environ.get("DEMO_MODEL", "reasoner")
GATEWAY = "http://127.0.0.1:4000/v1/chat/completions"
MAX_TOOL_CALLS = 4          # the skill's worked example needs two; four leaves headroom
MAX_HISTORY_TURNS = 8

SKILL_DIR = (Path.home() / ".hermes" / "profiles" / "cosmetics"
             / "skills" / "jarvis-cosmetics-search")


def _skill_text() -> str:
    """The skill as the console agent receives it, plus its operational reference."""
    parts = []
    for p in (SKILL_DIR / "SKILL.md",
              SKILL_DIR / "jarvis-cosmetics-search" / "references" / "system.md"):
        try:
            parts.append(p.read_text())
        except OSError:
            pass
    if not parts:
        raise RuntimeError(
            f"the cosmetics skill is not readable at {SKILL_DIR} — the demo answers with "
            "the console's own instructions and will not substitute a paraphrase")
    return "\n\n---\n\n".join(parts)


# NOTE: that skill lives ONLY under ~/.hermes and is not version-controlled, so the
# contract the console agent and this demo both answer under is one `rm -rf` from gone
# and has no history. `serve/SKILL.md.reference` is a dated copy for recovery — it is
# NOT read at runtime, deliberately: a fallback that silently works would let the demo
# keep answering under stale rules after the real skill changed.


SYSTEM = _skill_text() + """

---

# This conversation

You are answering in a live product demo for a non-technical audience. Everything above
still governs what you may claim; this only changes how you say it.

* The tool is called for you — you do not run shell commands here. Emit a `query_graph`
  call with the template id and the parameters as the user said them.
* Answer in prose, 2-5 sentences, no markdown headings and no bullet lists. The rows are
  displayed beside your answer as a table, so do NOT re-list them; say what they mean.
* Lead with the answer. Then the caveat that changes what it means — the evidence tier,
  a coverage gap, an EU/India distinction — in plain words, not jargon.
* Never say "the graph", "the tool", "the template" or "tier 4". Say where the fact came
  from: "CDSCO's register", "the company's own website", "this is inferred, not verified".
* When there are no rows, the reason matters more than the absence. Say what is missing
  and what would answer it instead.
* Never invent a company, licence number, certificate or figure that is not in the rows.
"""

TOOLS = [{
    "type": "function",
    "function": {
        "name": "query_graph",
        "description": ("Run ONE reviewed query against the cosmetics supply-chain graph "
                        "and return bounded JSON. Choose the template yourself from the "
                        "table in your instructions; pass parameter values exactly as the "
                        "user said them."),
        "parameters": {
            "type": "object",
            "properties": {
                "template": {"type": "string",
                             "description": "template id, e.g. testing_labs_for_cosmetics"},
                "params": {"type": "object",
                           "description": "declared params, e.g. {\"cluster\": \"Baddi\"}"},
            },
            "required": ["template"],
        },
    },
}]


def _call_gateway(messages: list[dict]) -> dict:
    key = lib.ENV.get("LITELLM_MASTER_KEY", "")
    with httpx.Client(timeout=120.0) as c:
        r = c.post(GATEWAY,
                   headers={"Authorization": f"Bearer {key}"},
                   json={"model": MODEL, "temperature": 0, "max_tokens": 900,
                         "messages": messages, "tools": TOOLS})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]


ROWS_TO_MODEL = 12


def _for_model(res: dict) -> dict:
    """The tool result as the model sees it: every caveat, a sample of the rows."""
    rows = res.get("rows") or []
    if len(rows) <= ROWS_TO_MODEL:
        return res
    out = dict(res)
    out["rows"] = rows[:ROWS_TO_MODEL]
    out["row_note"] = (f"{len(rows)} rows matched; the first {ROWS_TO_MODEL} are shown "
                       f"here and ALL {len(rows)} are displayed to the user. Report the "
                       f"total as {len(rows)}.")
    return out


def _run_template(template: str, params: dict | None) -> dict:
    """The tool. Whitelisted templates only — an unknown id is answered, not executed."""
    if template not in _router.TEMPLATES:
        return {"error": f"no such template {template!r}",
                "available": sorted(_router.TEMPLATES)}
    return _router.answer("", template=template, params=params or {}, limit=25)


def answer(question: str, history: list[dict] | None = None) -> dict:
    """One turn, all at once. `stream` is the same loop with progress; this wraps it."""
    out = {"reply": "", "evidence": []}
    for ev in stream(question, history):
        if ev["type"] == "done":
            out = {"reply": ev["reply"], "evidence": ev["evidence"]}
    return out


def stream(question: str, history: list[dict] | None = None):
    """The same turn, yielding progress as it goes.

    A turn takes ten seconds or more: the model chooses a template, the query runs, and
    on an empty result it chooses a second one before writing anything. Ten seconds of a
    blank screen reads as broken. Worse, it hides the part worth seeing — that a reviewed
    query was selected and returned rows BEFORE any prose existed. So the stages are the
    demo, not a loading bar over it.
    """
    messages = [{"role": "system", "content": SYSTEM}]
    for h in (history or [])[-MAX_HISTORY_TURNS:]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": h["content"][:4000]})
    messages.append({"role": "user", "content": question})

    evidence: list[dict] = []
    yield {"type": "stage", "stage": "Choosing a reviewed query"}
    for i in range(MAX_TOOL_CALLS):
        msg = _call_gateway(messages)
        calls = msg.get("tool_calls") or []
        if not calls:
            yield {"type": "done", "reply": (msg.get("content") or "").strip(),
                   "evidence": evidence}
            return
        messages.append({"role": "assistant",
                         "content": msg.get("content") or "",
                         "tool_calls": calls})
        for tc in calls:
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            tid = args.get("template", "")
            yield {"type": "stage",
                   "stage": f"Running {tid.replace('_', ' ')}" if tid else "Querying"}
            res = _run_template(tid, args.get("params"))
            if not res.get("error"):
                evidence.append(res)
                n = len(res.get("rows") or [])
                yield {"type": "stage",
                       "stage": (f"{n} result{'' if n == 1 else 's'} — writing the answer"
                                 if n else "No rows — checking why, then another query")}
            # The model sees the honesty metadata and the rows; it does not see a
            # pre-digested summary, because summarising here would be this module
            # making the claim instead of the model reasoning over the evidence.
            #
            # It does NOT need every row. Writing three sentences over 25 rows costs the
            # same as writing them over 12, and the rows are the bulk of the prompt —
            # a lab row carries three arrays of accreditation scopes. The page still
            # receives and shows all of them; only the model's copy is trimmed, and it
            # is told the count so it never reports 12 when the answer is 25.
            messages.append({"role": "tool", "tool_call_id": tc.get("id"),
                             "content": json.dumps(_for_model(res), default=str)[:20000]})

    # Out of tool calls. Ask for the answer with what it already has rather than
    # returning nothing — but no further queries.
    messages.append({"role": "user",
                     "content": "Answer now from what you already retrieved."})
    final = _call_gateway(messages)
    yield {"type": "done", "reply": (final.get("content") or "").strip(),
           "evidence": evidence}
