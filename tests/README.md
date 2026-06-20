# Tests

Three layers, run independently:

## 1. Pre-flight (run before deploy)

Fast (<5s), no services required. Catches syntax errors, broken plists, drifted ports, missing files.

```bash
cd /Users/sarvesh/Documents/Jarvis
python3 -m venv .venv-test
.venv-test/bin/pip install -r tests/requirements.txt
.venv-test/bin/pytest tests/test_preflight.py -v
```

## 2. Unit + integration (router, phase0_verify, bench scorer)

Covers most of the new code. The "live" router test auto-skips if the CC bridge isn't running.

```bash
.venv-test/bin/pytest tests/ -v --ignore=tests/test_router_live.py
```

To include live tests against the bridge:

```bash
.venv-test/bin/pytest tests/ -v
```

## 3. Post-deploy (run AFTER `./setup/00_run_all.sh`)

Bash script. Verifies the live deployed system:

```bash
bash tests/postdeploy_check.sh
```

Exit codes:
- `0` — all hard checks pass (warnings allowed)
- `1` — at least one hard check failed; deployment broken

## Files

| File | Tests |
|---|---|
| `test_preflight.py` | File presence, bash syntax, py compile, plist validity, YAML config schema, cross-file consistency |
| `test_router_tier.py` | Heuristic tier picker (haiku / sonnet / opus rules) |
| `test_router_budget.py` | Daily token spend, hard-cap canned response, healthz, model escape hatch |
| `test_router_logging.py` | brain-tokens.jsonl + brain-stub.jsonl writes |
| `test_router_live.py` | End-to-end through the live CC bridge (skipped if 3456 down) |
| `test_phase0_verify.py` | Verifier script on synthetic datasets, anomaly detection, cache behavior |
| `test_bench_scorer.py` | YAML scoring logic, code-fence stripping, instrument counting |
| `postdeploy_check.sh` | Live-system verification — user isolation, ACLs, all 4 ports, Hermes installed |
