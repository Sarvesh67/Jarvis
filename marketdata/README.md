# marketdata — the timeseries spine

Numbers live here, **not in the graph**. FalkorDB holds entities + `(ticker, as_of)` pointers;
this package holds the OHLCV they join back to. Cognify/embeddings never touch it → zero LLM cost.

## Layout

```
marketdata/
  features.py   pure, golden-tested feature registry (the ONE source of truth for price math,
                shared by the price-relevance preprocess step AND the backtester)
  store.py      canonical store: Parquet at rest (store/bars/<freq>/<ticker>.parquet),
                DuckDB as the query layer. write_bars() upserts; close_series() feeds features.
  source.py     swappable SourceAdapter interface + csvdir / parquetdir / synthetic / yfinance
  config.py     paths, canonical schema, NSE constants
  cli.py        load / coverage / feature  (see below)
  tests/        golden tests pinning the deterministic math
  store/        gitignored at-rest data
```

## Setup

```bash
uv venv --python 3.12
uv pip install -r requirements.lock
```

## Usage

```bash
PY=.venv/bin/python

# Deterministic synthetic bars (no external data) — for demos / end-to-end checks:
$PY -m marketdata.cli demo --tickers INFY.NS RELIANCE.NS '^NSEI'

# Real history via a source adapter (format the user supplies — swap the adapter, not the engine):
$PY -m marketdata.cli load --spec '{"kind":"csvdir","path":"~/nse","adjusted":true}' \
    --tickers INFY.NS RELIANCE.NS

$PY -m marketdata.cli coverage
$PY -m marketdata.cli feature --name car_zscore --ticker INFY.NS --bench '^NSEI' \
    --as-of 2024-06-03 --params '{"horizon":5,"vol_window":60}'
```

## Adapters

`source.make_adapter({"kind": ...})` builds one from a config dict (what the Dispatch tab stores):

| kind | use |
|------|-----|
| `csvdir` / `parquetdir` | the user's supplied NSE history (column map configurable) |
| `synthetic` | deterministic generated bars for tests/verification |
| `yfinance` | optional, lazy import — the **1-minute rolling capture** (run as a scheduled Dispatch; yfinance only serves ~7 days of 1m, so it accumulates an archive that can't be backfilled) |

## Features (`features.py`)

Pure functions: `forward_return`, `abnormal_returns`, `cum_abnormal_return`, `trailing_ar_vol`,
`realized_vol`, `car_zscore`. Registry entries carry a `version`; results stamp it. Tweak a
formula = change params (config) or add a **new** registry entry — never edit in place and break
existing outputs. `car_zscore` is the price-relevance statistic (forward CAR ÷ trailing AR vol).

**Point-in-time discipline:** predictors read only data ≤ `as_of`; anything using a *forward*
window (`forward_*`, `cum_abnormal_return`, `car_zscore`) is HINDSIGHT — fine for relevance
tagging, must never become a backtested signal feature.

```bash
.venv/bin/python -m pytest tests/ -q
```
