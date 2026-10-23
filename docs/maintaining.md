# Maintaining ADSfolio

## Keep Documentation in Sync

When code changes, update docs in the same commit.

1. Update the relevant docs pages.
2. If CLI behavior changes, update `docs/cli.rst`.
3. If you add or delete public API modules, update `docs/api/index.rst`.

## Release checks and API boundaries

For changed Python files, run these commands in this order:

```bash
uv run --no-sync isort PATHS
uv run --no-sync ruff format PATHS
uv run --no-sync ruff check PATHS
uv run --no-sync pytest tests/RELEVANT_TEST_FILE.py
```

The blocking lint gate explicitly selects `E4,E7,E9,F`. Import order belongs to
isort, and formatting belongs to Ruff. Full Ruff checks remain useful during
development. But their existing findings need individual review before you
expand the gate.

To set the release version:

1. Set the version in `src/adsfolio/__init__.py`.
2. Run `uv lock`.

Setuptools reads that same version for build metadata.

The package-check workflow runs tests on Python 3.10–3.13, builds documentation
and distributions, and installs the wheel without optional extras. The desktop
workflow checks native bundles separately. It publishes tagged releases after
all required checks pass.

The Python client, documented domain functions, and CLI commands are for direct
use. GUI widgets, job objects, and command implementation modules are
implementation interfaces. Their API pages aid maintenance. They do not promise
stable constructor or callback signatures.

Existing registry aliases remain available for compatibility.
`REGISTRY_FILENAME` names the legacy per-directory database.
`CATALOG_FILENAME` names the central catalog.

## Documentation Check

Run this before committing:

```bash
uv sync --extra gui --group docs
bash scripts/docs_check.sh
```

This installs the docs group and PySide6 in the project's `.venv`. The API
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

The build treats warnings as errors. Fix documentation warnings before you
commit. The build refreshes the API stubs with
`docs/_templates/autosummary/module.rst`. Edit that template or
`docs/api/index.rst`, not the generated stubs.

Sphinx also fetches Python, Click, and Qt cross-reference inventories. Network
failures can prevent a strict build even when local pages are valid. Report
such failures separately from documentation errors. Do not suppress all
reference warnings to pass a build.

Read the [Sphinx autodoc options](https://www.sphinx-doc.org/en/master/usage/extensions/autodoc.html)
before you change API member selection. Re-export packages link to
implementation pages. The registry page documents the central classes and
explains legacy aliases. Keep internal executable entry points out of recursive
discovery.

## Current Limitations and Follow-ups

**Citation and text formatting**

- Citation formatting converts title and publication HTML to plain text but
  leaves `bibstem` unchanged. Defensive normalization remains a follow-up.
- `html_to_latex` converts nested `<sub>`, `<sup>`, `<i>`, `<em>`, `<b>`, and
  `<strong>` elements. `format_title_latex` also uses it for titles. Existing
  `$...$` math stays unchanged outside subscript and superscript elements.
  Directly nested subscripts and superscripts share one math group. Unsupported
  tags and unmatched tags remain literal text. It decodes HTML entities once.
- Markdown CV output reuses the LaTeX title conversion, so titles can contain
  LaTeX math (`$_{2}$`) and escapes (`\&`). It is not plain text.
- In `format_author_list_cv`, `use_and` only decides whether the final
  conjunction appears (`None` omits it). The conjunction itself is always the
  word "and". The function does not insert the text of `use_and`, so the
  default `\&` does not appear in the output. `me_fmt` and LaTeX escaping of
  author names still apply.

**Catalog and file identity**

- File identifiers outside SQLite's signed 64-bit range use hexadecimal text
  internally and return as Python integers. Existing integer records remain valid.
  Windows discovery uses `os.stat` because `DirEntry.stat` omits device/inode IDs.
- Catalog matching by filesystem identity is limited because operating systems
  reuse inode numbers. An observation matches an active path first, then a
  device and inode, then a content digest.
- Some inode matches have a stored location with a known digest. Matching
  accepts such a match only if the incoming digest is known and equal.
  Otherwise, the file can still rejoin through the digest lookup.
- `discover_root` hashes a file only when both of these conditions are true.
  The file sits at a path with no active location. A stored location with the
  same inode has a known digest. It then hashes the file once, before any
  catalog write. Unchanged files are never
  hashed. A read error other than a vanished file aborts the scan before the
  catalog changes.
- Matching cannot check a stored location without a digest. Such a location
  matches by inode alone, and a reused inode can inherit that attachment.

**GUI file table**

- `FileTableModel` runs the registry query once per reset (`set_query`,
  `set_registry`, reload) and keeps the full sorted result in memory. It shows
  `PAGE_SIZE` (500) rows at a time. `fetchMore` slices that snapshot without
  querying again, so rows added after a reset appear only after the next reset.
  Memory grows with the number of matching records. If a query fails because
  the library is locked, the table becomes empty and the status bar reports it.
- When you omit `parent`, `FileTableModel` recognizes only lists and tuples as
  the `columns` argument. It interprets other sequences as the legacy positional
  parent. Pass columns as a list or tuple.

## Deferred design

Annotation review saves a separate PDF. For library PDFs, saving the copy
inside an open library folder registers it with the canonical PDF's metadata.
After a complete, verified merge, users can choose which
unchanged originals to move to Trash. Keeping originals is the default.
Catalog history remains available. See the [annotation workflow](gui.md) for
supported types and limits.

Catalog consolidation and unified TOML configuration remain future work.
They must preserve these requirements:

- Establish work identity from exact bibliographic identifiers. Title similarity
  and ADS result order must not authorize a merge. Preserve all identifiers.
- Let the user choose the canonical PDF. Transfer text markup automatically only
  when exact normalized text and its target geometry are unique. Require manual
  review for ambiguous text and geometry-based annotations. Preserve subtype,
  appearance, and comments. Each annotation needs an outcome or explicit waiver.
- Check a staged PDF before replacement. Journal filesystem and catalog updates
  together. Retain originals. Provide rollback and explicit recovery paths.
  Never delete obsolete inputs automatically.
- Use provenance to prevent duplicate transfers. A dual-pane review should show
  one annotation at a time.
- Use one typed configuration service for GUI and CLI, with global and
  per-library scope, explicit precedence, checked atomic writes, and
  recoverable migration. Keep session restoration global.

Additional limits:

- Uniform-font OCR or mixed-size titles can require a verified identifier.
- Title candidates require ADS verification.
- Downloads serialize session calls while they stream bodies concurrently.
  Callers must not mutate or close the client during active downloads.

## Publishing releases

Remote-changing commands print a preview unless you supply `--apply`. The
repository scripts prepare GitHub repositories, native application releases, a
Homebrew tap, and a static website. Installing the package publishes nothing.

## Repository setup

1. Install Git, [uv](https://docs.astral.sh/uv/), and the
   [GitHub CLI](https://cli.github.com/).
2. Authenticate with `gh auth login`.
3. Preview the repository setup, then apply it:

```sh
uv run --no-sync python scripts/release.py repository
uv run --no-sync python scripts/release.py repository --apply
```

The default repository is `ysBach/adsfolio`. For a fork, use
`--repository OWNER/NAME`.

The script initializes Git only when needed. It keeps existing history. An
existing `origin` becomes `previous-origin`, then the new repository becomes
`origin`.

If `origin` already points to the requested repository, rerunning setup
verifies that repository without changing remotes or creating it again.
Otherwise, creation stops if the remote repository already exists. For an
existing repository, inspect it and configure `origin` manually instead of
creating it again.

The script does not stage files, commit changes, or push history. A newly
created GitHub repository therefore remains empty until the first push.

Before the first commit:

1. Review the working tree, including untracked files.
2. Keep credentials, catalogs, personal PDFs, build outputs, and local reports
   out of Git.
3. After review, commit the intended files.
4. Push `main` to the new remote.

After you verify the new repository, you can delete the old Git remote.

## Prepare and publish a version

**Prepare the version**

1. Set the version. This example is the first stable release:

   ```sh
   uv run --no-sync python scripts/release.py version 0.1.0 --apply
   ```

2. Update `CHANGELOG.md`.
3. Check the package:

   ```sh
   uv run --no-sync python scripts/release.py check
   ```

**Test the bundles**

1. Run the **Desktop bundles** workflow manually.
2. Inspect all four platform results.
3. Test the downloads on clean target systems.

Complete code signing and dependency-license review before you claim production
readiness.

**Tag the release**

1. Commit the reviewed version, lockfile, and documentation. The release script
   requires a clean working tree on `main` with the intended `origin`.
2. Preview and create the release tag:

   ```sh
   uv run --no-sync python scripts/release.py tag
   uv run --no-sync python scripts/release.py tag --apply
   ```

The script repeats local checks, creates an annotated tag, and pushes `main` and
that tag atomically. It uses no force push. If the push fails, inspect the local
tag before you retry. The script never deletes a tag automatically.

Tag workflows check exact version equality, run package checks, and build macOS
ARM64, macOS Intel, Windows x86-64, and Linux x86-64 archives. They run frozen
application smoke tests, verify checksums, and create a GitHub Release with
Python distributions and desktop downloads. They mark development and
prerelease versions as prereleases. Stable releases also include `adsfolio.rb`
for Homebrew. The workflows do not overwrite existing release assets.

GitHub-hosted runners cannot establish every supported desktop configuration.
See [Building the desktop app](desktop.md) for acceptance checks. This
repository does not include signing and notarization credentials.

## Optional PyPI publication

1. Configure a PyPI trusted publisher for the project `adsfolio`, the
   repository, workflow `desktop.yml`, and environment `pypi`.
2. Create that GitHub environment with your chosen reviewer rules.
3. Set the repository variable `PUBLISH_PYPI=true`.

The workflow publishes the already-built wheel and source archive only after
the GitHub Release job succeeds. It uses short-lived OIDC credentials, not a
stored API token. Check project-name availability and publisher setup on PyPI.
Repository scripts do not reserve a name.

## Homebrew tap

Run this after a stable GitHub Release exists. Ruby must be available for the
cask syntax check. Git commits use your configured Git identity.

```sh
uv run --no-sync python scripts/publish_tap.py 0.1.0 --create
uv run --no-sync python scripts/publish_tap.py 0.1.0 --create --apply
```

This creates `ysBach/homebrew-adsfolio`, downloads the release checksums,
generates `Casks/adsfolio.rb`, checks its Ruby syntax, and commits and pushes
the cask. For later releases, omit `--create`. For other owners, use
`--repository` and `--tap`.

A cask requires checksums for both native macOS archives. The script uses no
placeholder checksum or `:no_check`. Do not publish a cask before native
acceptance.

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

Open `http://localhost:8000`. Before you deploy publicly, update the
first-release notice and download status to match the artifacts actually
available.

To publish the website:

1. In GitHub **Settings → Pages**, select **GitHub Actions** as the source.
2. Set `PUBLISH_PAGES=true` in repository variables.
3. Run **Documentation website**.

The expected project URL is `https://ysbach.github.io/adsfolio/`. Pull requests
build the site but cannot deploy it. Until you configure Pages and the variable,
website builds remain available as workflow artifacts without publishing.

## Data directories and the full rename

Update Python imports and scripts to the new name. The public package
namespace, CLI, application identifiers, and new storage paths use `adsfolio`.
The package does not install earlier command aliases. `ADS_API_TOKEN` still
names the credential for the NASA ADS service, not the application.

On first use, the application copies earlier configuration and cache directories
into the ADSfolio directories if the destination does not exist. SQLite uses the
backup API, including committed WAL data. Directory migration never merges or
overwrites an existing destination.

Version 0.6.1 no longer converts reserved metadata keys from the earlier
application. Copying an unconverted catalog retains its data, but manual field
locks, author text, and local references under those keys are not honoured.
Provider refreshes can overwrite fields whose earlier locks are not recognised.

Before upgrading from the earlier application:

1. Close the earlier application.
2. Open ADSfolio **0.6.0** once to convert the catalog and import its per-library
   files. Verify the metadata, manual locks, and local references there.
3. Install version 0.6.1. Keep the original directories until you verify the
   converted library.

If you already used ADSfolio 0.6.0, the catalog conversion is complete. Version
0.6.1 does not read per-library files under the earlier application name.

ADSfolio-named per-library sidecars and registries remain readable. The
rename keeps old Git history. It does not rewrite commits or change
bibliographic user text.
