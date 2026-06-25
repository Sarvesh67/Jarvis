"""Jarvis pre-processing — a generic, config-driven step engine.

Each step is a single, **stateless** chat-completions call to the LiteLLM gateway that
cleans / filters / annotates one document *before* cognify. No tools, no multi-turn, no
agent — prose in, prose out. The step's `prompt` is the whole instruction; the model just
runs it. Because the only dependency is httpx, this module imports cleanly in **both** the
Cognee venv (driven by `ingest.py`) and the dashboard venv (the test-step / compile endpoints).

A step has a free-text purpose (`name` + `prompt`); the engine only needs to know how to
consume its output, declared by `output`:

    {
      "id": "clean-filter", "name": "Clean & filter",
      "output": "rewrite" | "signal",    # how the pipeline uses the model's output
      "enabled": true,
      "model": "preprocess",              # role handle, resolved per project (Flash by default)
      "appliesTo": {"docTypes": ["*"]},   # ["*"] = all; else match the item's doc_type
      "prompt": "...",                    # executed verbatim
      # signal only:
      "metaKey": "relevance",             # where the value lands in item meta
      "labels": ["high", "low"],          # optional: constrain the output. empty => free-form value
      "signalMaxTokens": 128              # optional cap for a free-form signal value
    }

`output`:
  - rewrite → the model rewrites the text; output `DROP` gates the doc out of ingestion.
  - signal  → the model emits a value stored in item meta under `metaKey`; the text is
              unchanged. With `labels`, the value is matched to one of them (e.g.
              `relevance: high`); with no labels, the trimmed output is stored verbatim
              (e.g. a detected `docType` or an extracted ticker list). The cognify router
              reads these signals.

Legacy `kind` is still accepted: `transform`->`rewrite`, `classify`->`signal`.

The hard rule: per-document execution uses the cheap `model` (Flash). Sonnet only ever runs
in `compile_prompt` (authoring time), never per document.
"""
from __future__ import annotations

import json
import pathlib
import subprocess

import httpx

GATEWAY = "http://127.0.0.1:4000/v1"
DROP = "DROP"

# The market-data feature registry lives in its own venv (duckdb/pandas) to keep those heavy
# deps out of the Cognee/dashboard venvs. A `feature` step computes a deterministic value by
# shelling out to it — the same isolation pattern the dashboard uses to drive the Cognee venv.
_REPO = pathlib.Path(__file__).resolve().parent.parent
_MARKETDATA_PY = _REPO / "marketdata" / ".venv" / "bin" / "python"

# Output caps. DeepSeek V4 is a *reasoning* model — with reasoning enabled it spends
# completion tokens thinking and returns empty content until done, so per-document calls
# disable reasoning (see run_one_step). Rewriting a doc can be long; a label is tiny; a
# free-form signal value sits in between.
_MAX_TOKENS_REWRITE = 8192
_MAX_TOKENS_LABEL = 32
_MAX_TOKENS_SIGNAL = 128


def chat(model: str, system: str, user: str, key: str, *,
         temperature: float = 0.0, max_tokens: int = _MAX_TOKENS_REWRITE,
         reasoning: bool | None = None, timeout: float = 180.0) -> str:
    """One stateless chat-completions call to the gateway. Returns the message text.

    `model` is a bare litellm alias (e.g. "preprocess", "reasoner") — no "openai/" prefix,
    that prefix is only for Cognee's custom adapter, not a direct OpenAI-compatible call.
    `reasoning=False` turns off the model's hidden reasoning (mechanical calls don't need it;
    it just burns tokens) — forwarded to OpenRouter via the `reasoning` field.
    """
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    body = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if reasoning is False:
        body["reasoning"] = {"enabled": False}
    r = httpx.post(f"{GATEWAY}/chat/completions", json=body, headers=headers, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    return (data["choices"][0]["message"]["content"] or "").strip()


def step_applies(step: dict, doc_type: str | None) -> bool:
    """Is this step enabled and in scope for a doc of `doc_type`?"""
    if not step.get("enabled", True):
        return False
    dts = ((step.get("appliesTo") or {}).get("docTypes")) or ["*"]
    if "*" in dts:
        return True
    return doc_type in dts


def _is_drop(out: str) -> bool:
    """A transform step gates a doc by emitting DROP (alone or as a short leading token)."""
    s = (out or "").strip()
    if not s:
        return False
    head = s.splitlines()[0].strip().strip(".:!\"' ").upper()
    return head == DROP or (s.upper().startswith(DROP) and len(s) <= 16)


def _match_label(out: str, labels: list) -> str | None:
    """Pick the first declared label that appears in the model's output (case-insensitive)."""
    low = (out or "").lower()
    for lab in labels:
        if lab and str(lab).lower() in low:
            return lab
    return None


def _engine(step: dict) -> str:
    """Which executor runs a step: 'llm' (default, a gateway chat call) or 'feature'
    (a deterministic, $0-LLM market-data feature computed in the marketdata venv)."""
    return (step.get("engine") or "llm").strip().lower()


def _label_from_value(value, thresholds: dict) -> str | None:
    """Map a numeric feature value to a label via config thresholds (mirror of
    marketdata.features.label_from_value — kept inline to avoid a cross-venv import).

    thresholds e.g. {"high": 1.5, "by": "abs", "highLabel": "high", "lowLabel": "low"}.
    Returns None when value is None (no data => no label, fail closed)."""
    if value is None:
        return None
    by = (thresholds or {}).get("by", "abs")
    v = abs(value) if by == "abs" else value
    hi = (thresholds or {}).get("high")
    if hi is None:
        return None
    return (thresholds.get("highLabel", "high") if v >= hi
            else thresholds.get("lowLabel", "low"))


def _marketdata_feature(name: str, ticker: str, bench: str | None, as_of: str,
                        params: dict, *, freq: str = "1d") -> dict:
    """Compute one feature for (ticker, as_of) via the marketdata venv. Returns {value, version,
    error?}; value is None when there's no data (fail closed — never fabricate a number)."""
    if not _MARKETDATA_PY.exists():
        return {"value": None, "error": "marketdata venv not found"}
    cmd = [str(_MARKETDATA_PY), "-m", "marketdata.cli", "feature",
           "--name", name, "--ticker", ticker, "--as-of", as_of,
           "--params", json.dumps(params or {}), "--freq", freq]
    if bench:
        cmd += ["--bench", bench]
    try:
        r = subprocess.run(cmd, cwd=str(_REPO), capture_output=True, text=True, timeout=60)
    except Exception as e:  # noqa: BLE001
        return {"value": None, "error": str(e)[:200]}
    if r.returncode != 0:
        return {"value": None, "error": (r.stderr or "").strip()[-200:]}
    try:
        return json.loads(r.stdout)
    except Exception as e:  # noqa: BLE001
        return {"value": None, "error": f"bad feature output: {e}"}


def _run_feature_step(step: dict, link: dict | None) -> dict:
    """A `feature` step: compute a market-data feature for the doc's ticker(s)+as_of and emit a
    signal. Multi-ticker docs take the most extreme value (max |value|). Fails open (doc kept,
    no meta) when ticker/as_of/data are missing — logged via the trace reason."""
    res = {"output": "signal", "dropped": False, "text": None, "meta": {}, "error": None}
    feat = step.get("feature")
    tickers = (link or {}).get("tickers") or []
    as_of = (link or {}).get("as_of")
    if not feat or not tickers or not as_of:
        res["meta"]["_reason"] = "no ticker/as_of/feature"
        return res
    benches = (link or {}).get("benchmarks") or {}
    params = step.get("params") or {}
    best = None
    for tk in tickers:
        out = _marketdata_feature(feat, tk, benches.get(tk), as_of, params)
        v = out.get("value")
        if v is not None and (best is None or abs(v) > abs(best[0])):
            best = (v, tk, out.get("version"))
    if best is None:
        res["meta"]["_reason"] = "no price data for window"
        return res
    value, tk, version = best
    mk = step.get("metaKey") or step.get("id") or "feature"
    label = _label_from_value(value, step.get("thresholds") or {})
    res["meta"][mk] = label if label is not None else round(float(value), 4)
    res["meta"]["_raw"] = f"{feat}={value:.4f} ticker={tk} v{version}"
    return res


def _output_mode(step: dict) -> str:
    """How the pipeline consumes a step's output: 'rewrite' or 'signal'.

    Prefers the explicit `output` field; falls back to the legacy `kind`
    (`classify`->`signal`, anything else->`rewrite`)."""
    out = (step.get("output") or "").strip().lower()
    if out in ("rewrite", "signal"):
        return out
    return "signal" if (step.get("kind") or "").strip().lower() == "classify" else "rewrite"


def run_one_step(step: dict, text: str, key: str, *, doc_type: str | None = None,
                 link: dict | None = None) -> dict:
    """Execute one step against `text`.

    Returns {output, dropped, text, meta, error}. On a gateway error the step **fails open**
    (keeps the original text, no meta) so a transient hiccup never silently drops data.

    A `feature`-engine step ignores `text` and computes a deterministic market-data value from
    the `link` context (doc tickers + as_of); see `_run_feature_step`.
    """
    if _engine(step) == "feature":
        r = _run_feature_step(step, link)
        r["text"] = text  # feature steps never rewrite
        return r

    mode = _output_mode(step)
    model = step.get("model") or "preprocess"
    prompt = step.get("prompt") or ""
    labels = step.get("labels") or []
    res = {"output": mode, "dropped": False, "text": text, "meta": {}, "error": None}
    if not prompt.strip() or not (text or "").strip():
        return res

    if mode == "signal":
        max_tokens = _MAX_TOKENS_LABEL if labels else int(step.get("signalMaxTokens") or _MAX_TOKENS_SIGNAL)
    else:
        max_tokens = _MAX_TOKENS_REWRITE
    try:
        # Mechanical per-document call — disable reasoning (cheaper, and a reasoning model
        # otherwise returns empty content until it burns through the token cap).
        out = chat(model, prompt, text, key, max_tokens=max_tokens, reasoning=False)
    except Exception as e:  # noqa: BLE001  — fail open, keep the doc
        res["error"] = str(e)[:300]
        return res

    if mode == "signal":
        mk = step.get("metaKey") or step.get("id") or "label"
        # constrained to the declared set, or a free-form value stored verbatim
        value = _match_label(out, labels) if labels else (out.strip() or None)
        if value is not None:
            res["meta"][mk] = value
        res["meta"]["_raw"] = out[:200]
        return res  # text unchanged

    # rewrite / filter
    if _is_drop(out):
        res["dropped"] = True
        res["text"] = None
    else:
        res["text"] = out
    return res


def run_steps(steps: list, text: str, key: str, *,
              doc_type: str | None = None, tags: list | None = None,
              link: dict | None = None) -> dict:
    """Run enabled, in-scope steps in order over `text`.

    Returns {text|None, meta, trace}: text=None means a rewrite step DROPped the doc. `meta`
    accumulates signal values (e.g. {"relevance": "high", "docType": "concall"}); a signal
    step that sets `docType` updates scope for later steps. `trace` is per-step for logging/UI.

    `link` is the timeseries context (doc tickers + as_of + benchmarks) used by feature-engine
    steps; it's resolved once per doc by the caller (ingest.py) and ignored by LLM steps.
    """
    meta: dict = {"docType": doc_type, "tags": list(tags or [])}
    trace: list = []
    cur = text
    cur_type = doc_type
    for step in steps or []:
        name = step.get("name") or step.get("id") or "step"
        if not step_applies(step, cur_type):
            continue
        r = run_one_step(step, cur, key, doc_type=cur_type, link=link)
        trace.append({
            "step": name, "output": r["output"], "dropped": r["dropped"],
            "error": r["error"],
            "meta": {k: v for k, v in r["meta"].items() if not k.startswith("_")},
        })
        if r["error"]:
            continue  # fail open
        if r["dropped"]:
            return {"text": None, "meta": meta, "trace": trace}
        for k, v in r["meta"].items():
            if not k.startswith("_"):
                meta[k] = v
        if "docType" in r["meta"]:
            cur_type = r["meta"]["docType"]
            meta["docType"] = cur_type
        if r["output"] == "rewrite":
            cur = r["text"]
    return {"text": cur, "meta": meta, "trace": trace}


# --------------------------------------------------------------------------------------
# Authoring-time only: Sonnet compiles a robust execution prompt from plain-English intent.
# This is NEVER called per document — only when the user clicks "Compile" in the dashboard.
# --------------------------------------------------------------------------------------

_META_REWRITE = """You are a prompt engineer. Write a single, self-contained instruction \
that a cheaper model will follow to process ONE document for a knowledge graph.

The user's intent is given below. Turn it into a precise execution prompt that:
- Tells the model exactly what to keep and what to strip/rewrite.
- Requires the OUTPUT to be only the resulting text — no preamble, no explanation, no markdown fences.
- Preserves numbers, quotes, named entities, attributions and opinions unless the intent says otherwise.
- Ends with this exact rule: "If the document contains no material signal worth keeping, output exactly: DROP".

Return ONLY the execution prompt text — nothing else."""

_META_SIGNAL_LABELS = """You are a prompt engineer. Write a single, self-contained instruction \
that a cheaper model will follow to LABEL ONE document for a knowledge-graph pipeline.

The user's intent and the allowed labels are given below. Turn it into a precise execution prompt that:
- Explains how to choose among the labels based on the intent.
- Requires the OUTPUT to be EXACTLY ONE of the allowed labels, lowercase, with no other text.

Return ONLY the execution prompt text — nothing else."""

_META_SIGNAL_FREE = """You are a prompt engineer. Write a single, self-contained instruction \
that a cheaper model will follow to EXTRACT ONE short signal value from a document for a \
knowledge-graph pipeline.

The user's intent is given below. Turn it into a precise execution prompt that:
- Explains exactly what single value to extract or infer (e.g. a category, a list, a short label).
- Requires the OUTPUT to be ONLY that value — concise, no preamble, no explanation, no markdown fences.
- Tells the model to output an empty line if the value cannot be determined.

Return ONLY the execution prompt text — nothing else."""


def compile_prompt(intent: str, output: str, key: str, *,
                   labels: list | None = None, model: str = "reasoner",
                   timeout: float = 120.0) -> str:
    """Compile a plain-English `intent` into an execution prompt (Sonnet authors it).

    `output` is the step's output mode ('rewrite' | 'signal'); legacy 'transform'/'classify'
    are accepted. Authoring-time only; the returned prompt is stored verbatim and run
    per-document by Flash.
    """
    mode = (output or "").strip().lower()
    if mode in ("classify", "signal"):
        if labels:
            system = _META_SIGNAL_LABELS
            user = f"INTENT:\n{intent}\n\nALLOWED LABELS: {labels}"
        else:
            system = _META_SIGNAL_FREE
            user = f"INTENT:\n{intent}"
    else:
        system = _META_REWRITE
        user = f"INTENT:\n{intent}"
    return chat(model, system, user, key, max_tokens=1200, timeout=timeout)
