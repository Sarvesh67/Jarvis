# Contributing

## Setup

`cognee/` and `dashboard/` each have their own virtualenv (`.venv/`) with conflicting
dependencies — keep them separate. Deps are pinned in each component's `requirements.lock`.

```bash
cognee/.venv/bin/python    cognee/query.py msme "test question"
dashboard/.venv/bin/python -m py_compile dashboard/app.py
```

## Ground rules

- **Never commit secrets.** Keys live in `platform/.env` (gitignored). Reference them via
  `os.environ/...` in configs, never inline. Run `git status` before every commit and confirm
  `platform/.env` and `cognee/data/` are not staged.
- **Match the surrounding style.** This codebase favors small, well-commented functions and
  documents *why* (the non-obvious config gotchas) over *what*. See `cognee/README.md`.
- **Keep the two homes straight.** Code lives in this repo; runtime state lives outside it
  (`~/.hermes/`, `~/.local/bin/`, `~/Library/LaunchAgents/`, Docker). Don't hardcode the
  former into the latter beyond what the setup scripts already do.

## Before opening a PR

```bash
.venv-test/bin/pytest                 # unit tests
bash tests/postdeploy_check.sh        # end-to-end smoke (requires the stack running)
```

Compile-check any edited Python with `python -m py_compile`, and verify dashboard changes in
the browser (frontend edits are live on refresh; `app.py` needs a service reload — see RUNBOOK).
