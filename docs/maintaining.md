# Maintaining ADSfolio

## Keep Documentation in Sync

When code changes, update docs in the same commit.

1. Update the relevant docs pages.
2. If CLI behavior changes, update `docs/cli.rst`.
3. If public API modules are added or removed, update `docs/api/index.rst`.

## Release checks and API boundaries

The package-check workflow runs tests
on Python 3.10–3.13, builds documentation and distributions, and installs the
wheel without optional extras. The desktop workflow checks native bundles
separately and publishes tagged releases after all required checks pass.

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
- File identifiers outside SQLite's signed 64-bit range use hexadecimal text
  internally and return as Python integers. Existing integer records remain valid.
  Windows discovery uses `os.stat` because `DirEntry.stat` omits device/inode IDs.
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

## Deferred design

The development version includes annotation review that saves a separate PDF.
It does not consolidate catalog records or delete duplicates. See the
[annotation workflow](gui.md) for supported types and limits.

Catalog consolidation and unified TOML configuration remain future work.
They must preserve these requirements:

- Establish work identity from exact bibliographic identifiers. Title similarity
  and ADS result order must not authorize a merge. Preserve all identifiers.
- Let the user choose the canonical PDF. Transfer text markup automatically only
  when exact normalized text and its target geometry are unique. Require manual
  review for ambiguous text and geometry-based annotations. Preserve subtype,
  appearance, and comments. Each annotation needs an outcome or explicit waiver.
- Verify a staged PDF before replacement. Journal filesystem and catalog updates
  together, retain originals, and provide rollback and explicit recovery paths.
  Never delete obsolete inputs automatically. Use provenance to prevent duplicate
  transfers. A dual-pane review should show one annotation at a time.
- Use one typed configuration service for GUI and CLI, with global and per-library
  scope, explicit precedence, validated atomic writes, and recoverable migration.
  Keep session restoration global.

Additional limits: uniform-font OCR or mixed-size titles can require a verified
identifier. Title candidates require ADS verification. Downloads serialize session
calls while streaming bodies concurrently; callers must not mutate or close the
client during active downloads.

## Publishing releases

The repository scripts prepare GitHub repositories, native application releases,
a Homebrew tap, and a static website. Remote-changing commands print a preview
unless you supply `--apply`. Nothing is published by installing the package.

## Repository setup

Install Git, [uv](https://docs.astral.sh/uv/), and the
[GitHub CLI](https://cli.github.com/). Authenticate with `gh auth login`.
The default repository is `ysBach/adsfolio`; use `--repository OWNER/NAME` for a fork.

```sh
uv run --no-sync python scripts/release.py repository
uv run --no-sync python scripts/release.py repository --apply
```

The script initializes Git only when needed. Existing history is retained.
An existing `origin` becomes `previous-origin`, then the new repository becomes
`origin`. If `origin` already points to the requested repository, rerunning setup verifies
that repository without changing remotes or creating it again. Otherwise,
creation stops if the remote repository already exists. For an existing
repository, inspect it and configure `origin` manually instead of creating it again.
The script does not stage files, commit changes, or push history. A newly created
GitHub repository therefore remains empty until the first push.

Review the working tree, including untracked files, before the first commit.
Keep credentials, catalogs, personal PDFs, build outputs, and local reports out
of Git. After review, commit the intended files and push `main` to the new remote.
The old Git remote can be removed after the new repository is verified.

## Prepare and publish a version

1. Set the version. This example is the first stable release:

   ```sh
   uv run --no-sync python scripts/release.py version 0.1.0 --apply
   ```

2. Update `CHANGELOG.md` and check the package:

   ```sh
   uv run --no-sync python scripts/release.py check
   ```

3. Run the **Desktop bundles** workflow manually. Inspect all four platform
   results and test the downloads on clean target systems. Complete code signing
   and dependency-license review before claiming production readiness.
4. Commit the reviewed version, lockfile, and documentation. The release script
   requires a clean working tree on `main` with the intended `origin`.
5. Preview and create the release tag:

   ```sh
   uv run --no-sync python scripts/release.py tag
   uv run --no-sync python scripts/release.py tag --apply
   ```

The script repeats local checks, creates an annotated tag, and pushes `main` and
that tag atomically. No force push is used. If the push fails, inspect the local
tag before retrying; the script never deletes a tag automatically.

Tag workflows check exact version equality, run package checks, and build macOS
ARM64, macOS Intel, Windows x86-64, and Linux x86-64 archives. They run frozen
application smoke tests, verify checksums, and create a GitHub Release with
Python distributions and desktop downloads. Development and prerelease versions
are marked as prereleases. Stable releases also include `adsfolio.rb` for Homebrew.
Existing release assets are not overwritten.

GitHub-hosted runners cannot establish every supported desktop configuration.
See [platform requirements and acceptance checks](desktop.md). Signing and
notarization credentials are not included in this repository.

## Optional PyPI publication

Configure a PyPI trusted publisher for the project `adsfolio`, the repository,
workflow `desktop.yml`, and environment `pypi`. Create that GitHub environment
with your chosen reviewer rules. Set the repository variable `PUBLISH_PYPI=true`.
The workflow publishes the already-built wheel and source archive only after
the GitHub Release job succeeds. It uses short-lived OIDC credentials, not a
stored API token. Project-name availability and publisher setup must be checked
on PyPI; repository scripts do not reserve a name.

## Homebrew tap

Run this after a stable GitHub Release exists. Ruby must be available for the
cask syntax check. Git commits use your configured Git identity.

```sh
uv run --no-sync python scripts/publish_tap.py 0.1.0 --create
uv run --no-sync python scripts/publish_tap.py 0.1.0 --create --apply
```

This creates `ysBach/homebrew-adsfolio`, downloads the release checksums, generates
`Casks/adsfolio.rb`, checks its Ruby syntax, and commits and pushes the cask.
For later releases, omit `--create`. Use `--repository` and `--tap` for other
owners. A cask requires checksums for both native macOS archives. No placeholder
checksum or `:no_check` is used. Do not publish a cask before native acceptance.

Users can then run:

```sh
brew install --cask ysBach/adsfolio/adsfolio
brew upgrade --cask adsfolio
```

## Website and documentation

The static landing page lives in `website/`. It has no build-time JavaScript
framework, tracking scripts, or external fonts. The build includes strict
Sphinx documentation under `docs/` and works beneath a repository URL prefix.

```sh
uv sync --locked --extra gui --group docs
uv run --no-sync python scripts/build_site.py
uv run --no-sync python -m http.server 8000 --directory _site
```

Open `http://localhost:8000`. Before public deployment, update the first-release
notice and download status to match the artifacts actually available.

In GitHub **Settings → Pages**, select **GitHub Actions** as the source.
Set `PUBLISH_PAGES=true` in repository variables and run **Documentation website**.
The expected project URL is `https://ysbach.github.io/adsfolio/`. Pull requests
build the site but cannot deploy it. Until Pages and the variable are configured,
website builds remain available as workflow artifacts without publishing.

## Data directories and the full rename

The public package namespace, CLI, application identifiers, and new storage
paths use `adsfolio`. Update Python imports and scripts to the new name. Earlier
command aliases are not installed. `ADS_API_TOKEN` still names the NASA ADS
service credential, not the application.

On first use, the application copies earlier configuration and cache directories
into the ADSfolio directories if the destination does not exist. SQLite uses the
backup API, including committed WAL data. Catalog IDs, file history, user text,
and manual field locks are retained. Reserved metadata keys are translated in a
transaction. Conflicting old and new reserved keys stop migration instead of
choosing a value. An existing destination is never merged or overwritten.

Close the earlier application before migration. Keep the original directories
until you verify the new catalog. Earlier per-library sidecars remain readable.
Their old names exist only in migration readers, migration tests, and ignore
rules that prevent private catalog files from entering Git. Old Git history is
retained; the rename does not rewrite commits or modify bibliographic user text.

