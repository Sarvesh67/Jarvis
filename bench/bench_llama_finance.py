#!/usr/bin/env python3
"""Bench Llama-Open-Finance-8B on real Indian-market articles.

Runs the model against 10 sampled articles from the existing IFN corpus
(/Users/hedgefund/financial-data/datasets/huggingface/indian_financial_news.parquet),
asks for the 12-field YAML, and scores:

  - Valid YAML?           (parses without error)
  - All 12 fields present? (schema completeness)
  - Tickers found?        (instruments list non-empty when ground truth says >0)
  - Latency per article   (seconds)
  - Output token estimate (rough)

Writes a report to ./bench-report-YYYYMMDD-HHMM.md so you can compare
runs across model swaps (Llama-Open-Finance vs FinMA vs hybrid).

Run:
    python3 bench/bench_llama_finance.py
    python3 bench/bench_llama_finance.py --model llama-open-finance --n 10
    python3 bench/bench_llama_finance.py --model finma-7b-nlp --n 20
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REQUIRED_FIELDS = {
    "source", "url", "published_at", "headline", "instruments",
    "entities", "event_type", "event_subtype",
    "sentiment_label", "sentiment_confidence", "is_market_moving", "notes",
}


def load_articles(parquet_path: Path, n: int, seed: int = 42) -> list[dict]:
    """Load n random articles from the IFN parquet."""
    try:
        import pandas as pd
    except ImportError:
        print("FATAL: pandas required. pip install pandas pyarrow", file=sys.stderr)
        sys.exit(2)

    df = pd.read_parquet(parquet_path)
    random.seed(seed)
    idx = random.sample(range(len(df)), min(n, len(df)))
    rows = df.iloc[idx]
    out = []
    for _, r in rows.iterrows():
        out.append({
            "url": str(r.get("URL", "")),
            "content": str(r.get("Content", ""))[:3000],  # cap context
            "ground_truth_sentiment": r.get("Sentiment", None),
        })
    return out


def call_ollama(model: str, prompt: str, timeout_s: int = 60) -> tuple[str, float]:
    """Run `ollama run` and return (output, elapsed_seconds)."""
    start = time.monotonic()
    proc = subprocess.run(
        ["ollama", "run", model],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )
    elapsed = time.monotonic() - start
    if proc.returncode != 0:
        return f"ERROR: {proc.stderr[:500]}", elapsed
    return proc.stdout.strip(), elapsed


def score_output(output: str) -> dict:
    """Heuristic scoring of one output. No model-as-judge — just structural."""
    score = {
        "parses_yaml": False,
        "fields_present": 0,
        "fields_missing": list(REQUIRED_FIELDS),
        "instruments_found": 0,
        "raw_len": len(output),
    }

    # Try YAML parse
    try:
        import yaml
    except ImportError:
        # Without yaml, still fail-safe — just check field-name presence.
        for field in REQUIRED_FIELDS:
            if re.search(rf"\b{field}:", output):
                score["fields_present"] += 1
                score["fields_missing"].remove(field)
        return score

    # Strip code-fence wrapping if present
    cleaned = output
    if "```" in cleaned:
        match = re.search(r"```(?:yaml|yml)?\n?(.*?)```", cleaned, re.DOTALL)
        if match:
            cleaned = match.group(1)

    try:
        parsed = yaml.safe_load(cleaned)
        if isinstance(parsed, dict):
            score["parses_yaml"] = True
            present = REQUIRED_FIELDS & set(parsed.keys())
            score["fields_present"] = len(present)
            score["fields_missing"] = sorted(REQUIRED_FIELDS - present)
            instruments = parsed.get("instruments")
            if isinstance(instruments, list):
                score["instruments_found"] = len(instruments)
    except yaml.YAMLError as e:
        score["yaml_error"] = str(e)[:200]

    return score


def build_prompt(article: dict) -> str:
    return (
        "Extract the 12-field YAML from this Indian financial news article.\n\n"
        "URL: " + article["url"] + "\n\n"
        "Article body:\n" + article["content"] + "\n\n"
        "Output ONLY the YAML. No preamble. No explanations."
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="llama-open-finance",
                    help="Ollama model name to bench")
    ap.add_argument("--n", type=int, default=10, help="Number of articles to bench")
    ap.add_argument("--parquet", default=os.path.expanduser(
        "~/financial-data/datasets/huggingface/indian_financial_news.parquet"))
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--timeout", type=int, default=120)
    args = ap.parse_args()

    parquet = Path(args.parquet)
    if not parquet.exists():
        print(f"FATAL: corpus not found at {parquet}", file=sys.stderr)
        return 2

    print(f"Loading {args.n} articles from {parquet} ...")
    articles = load_articles(parquet, args.n, args.seed)
    print(f"Benching model: {args.model}")

    results = []
    for i, art in enumerate(articles, 1):
        print(f"  [{i}/{len(articles)}] {art['url'][:80]} ... ", end="", flush=True)
        prompt = build_prompt(art)
        output, elapsed = call_ollama(args.model, prompt, args.timeout)
        scored = score_output(output)
        scored["elapsed_s"] = round(elapsed, 2)
        scored["url"] = art["url"]
        results.append({"input": art, "output": output, "score": scored})
        print(f"{elapsed:.1f}s  fields={scored['fields_present']}/{len(REQUIRED_FIELDS)}")

    # --- aggregate ---
    n = len(results)
    valid_yaml_pct = 100 * sum(r["score"]["parses_yaml"] for r in results) / n
    avg_fields = sum(r["score"]["fields_present"] for r in results) / n
    avg_lat = sum(r["score"]["elapsed_s"] for r in results) / n
    has_instruments = sum(r["score"]["instruments_found"] > 0 for r in results)

    # --- report ---
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%d-%H%M")
    out_dir = Path("bench-reports")
    out_dir.mkdir(exist_ok=True)
    report = out_dir / f"bench-{args.model}-{stamp}.md"

    lines = [
        f"# Bench: {args.model} ({now.isoformat()})",
        "",
        f"- **Articles**: {n}",
        f"- **Valid YAML**: {valid_yaml_pct:.0f}%",
        f"- **Avg fields present**: {avg_fields:.1f} / {len(REQUIRED_FIELDS)}",
        f"- **Avg latency**: {avg_lat:.1f}s/article",
        f"- **Articles with instruments extracted**: {has_instruments}/{n}",
        "",
        "## Per-article",
        "",
    ]
    for i, r in enumerate(results, 1):
        s = r["score"]
        lines.extend([
            f"### Article {i}",
            f"- URL: `{r['input']['url']}`",
            f"- Valid YAML: {s['parses_yaml']}",
            f"- Fields: {s['fields_present']}/{len(REQUIRED_FIELDS)}"
            + (f" (missing: {', '.join(s['fields_missing'])})" if s['fields_missing'] else ""),
            f"- Instruments: {s['instruments_found']}",
            f"- Latency: {s['elapsed_s']}s",
            "",
            "Raw output (first 800 chars):",
            "```",
            r["output"][:800],
            "```",
            "",
        ])

    report.write_text("\n".join(lines))
    print(f"\nReport: {report}")
    print(f"Summary: {valid_yaml_pct:.0f}% valid YAML, {avg_fields:.1f}/12 fields, {avg_lat:.1f}s avg")

    # JSON dump for programmatic comparison
    json_out = out_dir / f"bench-{args.model}-{stamp}.json"
    json_out.write_text(json.dumps(results, indent=2, default=str))
    return 0 if valid_yaml_pct >= 70 and avg_fields >= 8 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
