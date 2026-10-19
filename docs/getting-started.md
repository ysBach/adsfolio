# Getting Started

ADSfolio (distributed as package `adsfolio`) provides command-line tools
(`adsfolio`) and an interactive desktop GUI (`adsfolio-gui`).
Python code imports the `adsfolio` package namespace.

## Installation

Requires Python 3.10 or newer. Before the first package-index publication, use
the development-checkout instructions below or install a downloaded release
wheel. After publication, install the `adsfolio` distribution:

```bash
uv pip install adsfolio
```

Install PDF extras and the desktop GUI:

```bash
uv pip install 'adsfolio[full,gui]'
```

`adsfolio[full]` includes:

- `watchdog` for filesystem watch mode
- `pymupdf` for annotation porting during PDF upgrade
- `iso4` for journal abbreviation conversion

The separate `gui` extra includes PySide6. It is not included by `full` alone.
`uv pip install` needs an existing virtual environment; `python -m pip install`
accepts the same package names in a pip-managed environment.

For the current development checkout (`0.1.0.dev`), run from the repository:

```bash
uv sync --all-extras
uv run adsfolio --help
uv run adsfolio gui
```

`uv sync` manages the project's `.venv`. Do not use it, or `uv pip sync`, against
a shared environment. With an existing shared environment already containing the
package and its dependencies, activate it and use `uv run --active --no-sync`.

## API Token and Provider Resolution

Local PDF library browsing, catalog queries, custom citation styles, and
offline reference workflows operate without an NASA ADS token. An API token is
required only when querying NASA ADS / SciXplorer network services.
Identification tries ADS, Crossref, then DataCite by default. Provider order is
configurable in the Identify dialog or through repeated `--provider` options.
Crossref and DataCite use exact DOIs and need no ADS token. Title searches use
ADS. Neither OpenAlex nor PMID/PubMed lookup is supported.

## Configure API token

1. Create an account at [SciX / ADS](https://scixplorer.org).
2. Get your token from [settings](https://scixplorer.org/user/settings/token).
3. Run interactive setup (recommended):

```bash
adsfolio set
```

`adsfolio set` is usually needed only once. It saves the profile and immediately
builds the publication cache. Re-run it when your token or identify profile changes.

To configure only the token without the profile/cache setup:

```bash
adsfolio config set-token <your-token>
```

Or set it for the current shell:

```bash
export ADS_API_TOKEN=<your-token>
```

## PDF setup, cache, and state

Run the PDF workflow in this order:

```bash
adsfolio set
adsfolio cache status
adsfolio pdf identify ~/Papers --recursive
adsfolio pdf rename ~/Papers
adsfolio pdf rename ~/Papers --apply
```

`adsfolio cache refresh` re-downloads the user publication universe; `adsfolio cache clear`
removes it. PDF state lives in the central user-level `pdf-library.sqlite`
catalog, under `Config.get_config_dir()` (`~/Library/Application Support/adsfolio` on
macOS). Roots do not contain a new live database.
Use `adsfolio pdf export ~/Papers -o pdfs-backup.json` for a human-readable backup.
Legacy `.adsfolio-pdfs.sqlite` and `.adsfolio-pdfs.json` files are imported read-only
and retained as source backups. The publication cache is separate from the
catalog; `adsfolio cache status` reports its location. Do not identify, rename, or
watch the same synced library from two machines concurrently: each machine has
its own catalog and filesystem changes can race.

Identify's `--apply` controls unresolved-file moves. Identify can still update
catalog records, make authorized ADS requests, and export an unresolved CSV
without that flag. Preview Rename before adding `--apply`.

## Show PDFs in subdirectories in the GUI

Run `adsfolio-gui` (or `adsfolio gui`), then choose **File → Add Library Root**.
top-level PDFs by default; the table shows current catalog entries.

1. Right-click the library name under **LIBRARIES**.
2. Turn off **Watch Current Library** if checked, then reopen the context menu.
3. Check **Include Subdirectories**.
4. Quit and reopen the GUI, select **All**, and clear the search field.

The setting also changes future GUI watch scope. It is separate from the
recursive checkbox in **Tools → Identify**. **Refresh/F5** only reloads the
catalog; it does not discover files, and changing the recursive setting does not
rescan until restart.

Top-level discovery leaves nested catalog entries unchanged. If an earlier
version marked existing nested files missing, a recursive startup scan restores
their visibility and discovers uncataloged files without renaming or moving
them. **Unresolved** is an
identification-status filter, not a listing of the `unresolved/` directory.
See the [GUI guide](gui.md) for watch behavior and troubleshooting.

## Quick CLI examples

```bash
adsfolio search "author:Einstein" --fields bibcode,title --rows 5
adsfolio export '2024A&A...684A..80B' '2021A&A...654A.113B' -f bibtex
adsfolio library list
adsfolio pdf ocr ~/Papers --recursive -o ~/Papers/ocr_required
adsfolio pdf identify ~/Papers --recursive
adsfolio cv init
adsfolio cv generate --config cv.yaml --dry-run
```

## Python example

```python
from adsfolio import ADSClient

client = ADSClient()
results = client.search.query("author:Einstein", fl=["bibcode", "title"], rows=5)
print(results.num_found)
```

## Continue

For step-by-step command-line walkthroughs, see [CLI Tutorials](./cli-tutorials/index.md).
