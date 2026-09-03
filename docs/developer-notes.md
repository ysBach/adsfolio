# Developer Notes

## Keep Documentation in Sync

When code changes, update docs in the same commit.

1. Update the relevant docs pages.
2. If CLI behavior changes, update `docs/cli.rst`.
3. If public API modules are added or removed, update `docs/api/index.rst`.

## Pre-Commit Docs Routine

Run this before committing:

```bash
uv sync
uv run pre-commit install
uv run pre-commit run docs-check --all-files
uv run pre-commit run --all-files
```

The `docs-check` hook runs:

```bash
uv run sphinx-build -n -W --keep-going -b html docs docs/_build/html
```

Warnings are treated as errors, so fix documentation warnings before commit.
