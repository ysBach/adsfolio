# Developer Notes

## Keep Documentation in Sync

When code changes, update docs in the same commit.

1. Update the relevant docs pages.
2. If CLI behavior changes, update `docs/cli.rst`.
3. If public API modules are added or removed, update `docs/api/index.rst`.

## Release checks and API boundaries

See [Release readiness](release-readiness.md) for the current verification,
confirmed defects, and release decisions. The package-check workflow runs tests
on Python 3.10–3.13, builds documentation and distributions, and installs the
wheel without optional extras. The desktop workflow checks native bundles
separately. Neither publishes a release.

The blocking lint gate explicitly selects `E4,E7,E9,F`; isort owns import order,
and Ruff owns formatting. Full Ruff checks remain useful during development,
but their existing findings need individual review before expanding the gate.
Run both sorters' required operations in this order for changed Python files:

```bash
uv run --no-sync isort PATHS
uv run --no-sync ruff format PATHS
uv run --no-sync ruff check PATHS
uv run --no-sync pytest tests/RELEVANT_TEST_FILE.py
```

Set the release version in `src/adsfolio/__init__.py`, then run `uv lock`.
Setuptools reads that same version for build metadata.

The Python client, documented domain functions, and CLI commands are intended
for direct use. GUI widgets, job objects, and command implementation modules
are implementation interfaces: their API pages aid maintenance and do not
promise stable constructor or callback signatures. Existing registry aliases
remain available for compatibility; `REGISTRY_FILENAME` names the legacy
per-directory database, while `CATALOG_FILENAME` names the central catalog.

## Documentation Check

Run this before committing:

```bash
uv sync --extra gui --group docs
bash scripts/docs_check.sh
```

This installs the docs group and PySide6 in the project's `.venv`; the API
reference imports GUI modules without launching the application. To build
without a separate setup step:

```bash
uv run --extra gui --group docs sphinx-build -n -W --keep-going -b html docs docs/_build/html
```

For an already activated shared environment, avoid project synchronization.
Layer the documentation requirements temporarily instead:

```bash
uv run --active --no-sync --with-requirements docs/requirements.txt sphinx-build -n -W --keep-going -b html docs docs/_build/html
```

Warnings are treated as errors, so fix documentation warnings before commit.
The build refreshes the API stubs using `docs/_templates/autosummary/module.rst`;
edit that template or `docs/api/index.rst`, not generated stubs. Sphinx also
fetches Python, Click, and Qt cross-reference inventories. Network failures can
prevent a strict build even when local pages are valid; report that separately
from documentation errors. Do not suppress all reference warnings to pass a build.

Read the [Sphinx autodoc options](https://www.sphinx-doc.org/en/master/usage/extensions/autodoc.html)
before changing API member selection. Re-export packages link to implementation
pages, while the registry page documents the central classes and explains legacy
aliases. Keep internal executable entry points out of recursive discovery.

## Current Limitations and Follow-ups

- Citation formatting converts title and publication HTML to plain text but
  leaves `bibstem` unchanged. Defensive normalization remains a follow-up.
- `html_to_latex` (also used for titles by `format_title_latex`) converts nested
  `<sub>`, `<sup>`, `<i>`, `<em>`, `<b>`, and `<strong>` elements. Existing
  `$...$` math stays unchanged outside subscript and superscript elements.
  Directly nested subscripts and superscripts share one math group. Unsupported
  tags and unmatched tags remain literal text. HTML entities are decoded once.
- Markdown CV output reuses the LaTeX title conversion, so titles can contain
  LaTeX math (`$_{2}$`) and escapes (`\&`). It is not plain text.
- Catalog matching by filesystem identity is limited because operating systems
  reuse inode numbers. An observation matches an active path first, then a
  device and inode, then a content digest. An inode match whose stored location
  has a known digest is accepted only if the incoming digest is known and equal;
  otherwise the file can still rejoin through the digest lookup. `discover_root`
  hashes a file once, before any catalog write, only when it sits at a path with
  no active location and a stored location with the same inode has a known
  digest. Unchanged files are never hashed, and a read error other than a
  vanished file aborts the scan before the catalog changes. A stored location
  without a digest cannot be checked, so it matches by inode alone and a reused
  inode can inherit that attachment.
- In `format_author_list_cv`, `use_and` only decides whether the final
  conjunction appears (`None` omits it). The conjunction itself is always the word
  "and"; the text of `use_and` is not inserted, so the default `\&` does not
  appear in the output. `me_fmt` and LaTeX escaping of author names still apply.
- `FileTableModel` runs the registry query once per reset (`set_query`,
  `set_registry`, reload) and keeps the full sorted result in memory. It shows
  `PAGE_SIZE` (500) rows at a time and `fetchMore` slices that snapshot without
  querying again, so rows added after a reset appear only after the next reset.
  Memory grows with the number of matching records. If a query fails because the
  library is locked, the table is emptied and the status bar reports it.
- `FileTableModel` recognizes only lists and tuples as the `columns` argument
  when `parent` is omitted; other sequences are interpreted as the legacy
  positional parent. Pass columns as a list or tuple.

## Retained Design Proposal

The PDF merge and configuration proposal is unimplemented. Its future priority
is unknown; it is retained because cleanup should not discard unfinished design
requirements.

```{toctree}
:maxdepth: 1

design/pdf-identity-merge-and-config
design/2026-10-17-gemini-package-review
```
