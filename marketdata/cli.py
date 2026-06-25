"""marketdata CLI — load history, inspect coverage, and spot-check a feature value.

Run from the marketdata venv:
    .venv/bin/python -m marketdata.cli demo --tickers INFY.NS RELIANCE.NS ^NSEI
    .venv/bin/python -m marketdata.cli coverage
    .venv/bin/python -m marketdata.cli load --spec '{"kind":"csvdir","path":"~/nse"}' --tickers INFY.NS
    .venv/bin/python -m marketdata.cli feature --name car_zscore --ticker INFY.NS \
        --bench ^NSEI --as-of 2024-06-01 --params '{"horizon":5}'

The `feature` command is the manual-check used in Phase 1 verification.
"""
from __future__ import annotations

import argparse
import json

from . import config, ingest_from_adapter, store
from .features import FeatureContext, compute


def _cmd_demo(args):
    spec = {"kind": "synthetic", "start": "2024-01-01", "periods": args.periods}
    res = ingest_from_adapter(spec, args.tickers, freq=args.freq)
    print(json.dumps(res, indent=2, default=str))


def _cmd_load(args):
    spec = json.loads(args.spec)
    res = ingest_from_adapter(spec, args.tickers, start=args.start, end=args.end, freq=args.freq)
    print(json.dumps(res, indent=2, default=str))


def _cmd_coverage(args):
    print(store.coverage(args.freq).to_string(index=False))


def _cmd_feature(args):
    ctx = FeatureContext(
        close=store.close_series(args.ticker, freq=args.freq),
        bench=store.close_series(args.bench, freq=args.freq) if args.bench else None,
    )
    out = compute(args.name, ctx, args.as_of, json.loads(args.params) if args.params else {})
    print(json.dumps(out, indent=2, default=str))


def main(argv=None):
    p = argparse.ArgumentParser(prog="marketdata")
    sub = p.add_subparsers(required=True)

    d = sub.add_parser("demo", help="load deterministic synthetic bars")
    d.add_argument("--tickers", nargs="+", required=True)
    d.add_argument("--freq", default="1d")
    d.add_argument("--periods", type=int, default=400)
    d.set_defaults(func=_cmd_demo)

    l = sub.add_parser("load", help="load via a source adapter spec (JSON)")
    l.add_argument("--spec", required=True)
    l.add_argument("--tickers", nargs="+", required=True)
    l.add_argument("--start"); l.add_argument("--end")
    l.add_argument("--freq", default="1d")
    l.set_defaults(func=_cmd_load)

    c = sub.add_parser("coverage", help="per-ticker rows + date range")
    c.add_argument("--freq", default="1d")
    c.set_defaults(func=_cmd_coverage)

    f = sub.add_parser("feature", help="compute one feature for a ticker/as_of (manual check)")
    f.add_argument("--name", required=True)
    f.add_argument("--ticker", required=True)
    f.add_argument("--bench", default=config.DEFAULT_BENCHMARK)
    f.add_argument("--as-of", required=True, dest="as_of")
    f.add_argument("--params", default="")
    f.add_argument("--freq", default="1d")
    f.set_defaults(func=_cmd_feature)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
