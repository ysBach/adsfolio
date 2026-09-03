# adsapi

Comprehensive Python client for NASA ADS / SciXplorer API with CLI tools.

## Useful Info
- https://ui.adsabs.harvard.edu/help/search/comprehensive-solr-term-list

## Features

- **Full API Coverage**: Search, Export, Libraries, Metrics, and Journals APIs
- **CLI Tools**: Easy command-line access to all functionality
- **Rate Limiting**: Built-in rate limit handling and caching
- **Research Utilities**: Journal-name formatting, citation key extraction, and PDF workflows
- **Cross-Domain Discovery**: Supports astronomy, Earth science, physical science, and space-based life-science literature workflows via ADS / SciXplorer coverage

## Installation

Requires Python 3.10 or newer.

```bash
pip install adsapi

# Install all optional features (watcher, annotation porting, ISO4 abbreviation)
pip install adsapi[full]
```

## Quick Start

### Get your API token

1. Create an account at [ADS](https://ui.adsabs.harvard.edu)
2. Get your token from [settings](https://ui.adsabs.harvard.edu/user/settings/token)
3. Set it as environment variable: `export ADS_API_TOKEN=<your-token>`

### CLI Usage

```bash
# Search for papers
adsapi search "author:Einstein" --fl bibcode,title --rows 5

# Export to BibTeX
adsapi export 2020ApJ...900..100S -f bibtex

# List your libraries
adsapi library list

# Get library contents as BibTeX
adsapi library get <library_id> --bibtex
```

### Python API

```python
from adsapi import ADSClient

client = ADSClient()

# Search
results = client.search.query("author:Einstein", fl=["bibcode", "title"], rows=5)

# Export
bibtex = client.export.export(["2020ApJ...900..100S"], format="bibtex")

# Libraries
libs = client.libraries.list_libraries()
```

## PDF Workflow (Identify, Rename, Watch, Upgrade)

Recommended order:

1. Run `adsapi set` once (token + identify profile and publication-cache build).
2. Inspect or refresh the cache with `adsapi cache status` / `adsapi cache refresh`.
3. Optional: run `pdf ocr` to route likely non-OCR PDFs for OCR first.
4. Run `pdf identify` (cache-first matching + batched ADS fallback).
5. `rename` previews filename changes (dry-run by default), then applies. Use
   `rename --allow-ads` to identify pending PDFs before rendering names.
6. `watch` continuously processes new PDFs in a folder.
7. `upgrade` updates arXiv PDFs to published versions when available.
8. Optional: `obsidian` generates one Obsidian-friendly Markdown note per tracked PDF.

PDF state lives in one central `pdf-library.sqlite` catalog under the user
configuration directory, so adding roots does not create databases inside
synced folders. The user-level publication cache is the platform-specific
`platformdirs` cache path returned by `adsapi cache status`. Use `adsapi cache
clear` to remove the publication cache and `adsapi pdf export DIR -o
snapshot.json` for a readable registry backup. Legacy `.adsapi-pdfs.sqlite`
and `.adsapi-pdfs.json` files are imported read-only and remain available as
source backups.

### Desktop GUI

Install the optional PySide6 desktop browser with `pip install 'adsapi[gui]'`,
then run `adsapi gui` (or `adsapi-gui`). It provides a multi-root, searchable
and sortable PDF table with metadata, OS-default opening/reveal actions, and
Identify/Rename dialogs that execute the existing CLI commands as subprocesses.

Do not run identify or watch against the same library from two machines at once.
Keep `pdf export` snapshots as backups.

### 0) One-time setup

```bash
# Do this once unless your token/profile changes
adsapi set
```

### 1) Optional OCR precheck

Use this only if you have scanned/non-OCR PDFs.

```bash
# Dry-run by default
adsapi pdf ocr ~/Papers --recursive -o ~/Papers/ocr_required

# Move likely non-OCR files
adsapi pdf ocr ~/Papers --recursive -o ~/Papers/ocr_required --apply

# Also generate a runnable OCR batch script in current directory
adsapi pdf ocr ~/Papers --recursive -o ~/Papers/ocr_required --make-ocr-sh
# then run:
bash ocrmypdf.sh
```

The command prints an `ocrmypdf` loop example. If those PDFs contain annotations,
review OCR output carefully before replacing files. You can manually move selected
files out of `ocr_required` to skip OCR for those files. The generated script
uses the same `-o/--output` directory you provided as OCR input.

### 2) Identify PDFs with batched ADS queries

```bash
# Scan and resolve metadata (queries ADS/SciXplorer in DOI batches)
adsapi pdf identify ~/Papers --recursive

# Optional unresolved routing (dry-run; use --apply to move)
adsapi pdf identify ~/Papers --recursive \
  --output-unresolved ~/Papers/unresolved
```

Identify summary:

1. Central-catalog match using stored filesystem facts and SHA-256 only when needed.
2. Publication-cache match first (`adsapi set` ORCID/query names/library IDs).
3. For remaining PDFs, extract DOI/bibcode candidates from filename, PDF metadata, and first-page contents.
4. Run ADS DOI/bibcode batches, then title fallback queries for any remaining candidates.
5. Interactive ADS permission gate with estimated query volume.
   - In non-interactive runs, ADS fallback is skipped unless `--allow-ads` is set.
6. If files remain unresolved, exports `unresolved.csv` with `filename`,
   `relative_path`, and `first_page_text` (first-page preview), then prints
   a ready-to-copy AI prompt template for bibcode extraction.
7. Interactive runs then ask for your AI-enriched CSV and immediately retry
   batched ADS bibcode/DOI lookup from that CSV.

For non-interactive usage, pass AI output directly:

```bash
adsapi pdf identify ~/Papers --recursive --ai-csv ~/Papers/unresolved_ai.csv
```

### 3) Rename: dry-run first, then apply

```bash
# Preview rename plan from registry metadata
adsapi pdf rename ~/Papers \
  --scheme "{authors}_{bibcode}-{title}" \
  --author-format surname_initials \
  --max-authors 3

# Execute rename plan
adsapi pdf rename ~/Papers \
  --scheme "{authors}_{bibcode}-{title}" \
  --author-format surname_initials \
  --max-authors 3 \
  --apply
```

To resolve pending PDFs before rendering names, add `--allow-ads`. This scans
the directory tree and uses the same ADS workflow as `pdf identify`.

To route any unresolved PDFs at the same time, add `--output-unresolved`; a
relative value is interpreted below the PDF directory:

```bash
adsapi pdf rename ~/Papers --allow-ads --output-unresolved unresolved --apply
```

When `~/Papers/unresolved` exists, `rename --apply` also moves resolved records
from that directory into `~/Papers` after rendering the new name. Files without
successful ADS metadata remain in `unresolved`.

### 5) Watch a folder for incoming PDFs

```bash
# Polling backend (default), applies renames/moves
adsapi pdf watch ~/Papers --recursive --interval 10 --apply

# Watchdog backend (requires adsapi[full])
adsapi pdf watch ~/Papers --recursive --use-watchdog --interval 60 \
  --output-unresolved unresolved --apply
```

Watch mode processes only new or observably changed PDFs. The watchdog backend
queues create/move/modify events to one worker and uses `--interval` for a
facts-only reconciliation scan, so a missed event does not trigger a full
library re-identification. The default filename policy preserves Unicode;
pass `--char-policy ascii` for transliterated ASCII names. Watch-mode
`--output-unresolved` must remain inside the watched root so file and catalog
moves stay consistent. Omit `--apply` to preview resolved renames and unresolved
moves without changing paths.

Author joining and truncation markers are configurable. This example includes
at most two authors, joins them with `+`, and appends another `+` only when
additional authors were omitted:

```bash
adsapi pdf rename ~/Papers --max-authors 2 \
  --authors-when-truncated 1 \
  --author-separator "+" --author-truncation-suffix "+" --apply
```

With the `{authors}` placeholder, one or two authors are shown in full; three
or more render as `SmithJ+` with the example's retained count of one.
`{authors_N}` uses the same retained authors but omits the truncation suffix.
Set `--authors-when-truncated 2` to render `SmithJ+DoeA+` for three or more
authors. The Rename dialog can save both values for all libraries or only the
selected library. Use **Save Defaults** to persist them without renaming existing
PDFs.

### 6) Upgrade arXiv PDFs to published versions

```bash
# Dry-run upgrade check
adsapi pdf upgrade ~/Papers

# Execute upgrades
adsapi pdf upgrade ~/Papers --apply

# Optional overwrite mode with backup
adsapi pdf upgrade ~/Papers --apply \
  --overwrite-arxiv-with-official \
  --arxiv-backup-if-overwritten ~/Papers/arxiv-backup
```

You can also do automatic upgrade checks while watching:

```bash
adsapi pdf watch ~/Papers --recursive --apply \
  --auto-upgrade --upgrade-interval 24
```

### 7) Generate Obsidian-friendly notes

For PDFs that already live inside an Obsidian vault, write one Markdown note per tracked PDF
(YAML frontmatter with title/authors/year/journal/bibcode, plus an `## Abstract` section). Notes
link back to the PDF with an absolute `file://` link — PDFs are never copied or moved.

```bash
# Dry-run: no ADS queries, no files written
adsapi pdf obsidian ~/Papers

# Fetch/cache missing abstracts from ADS and write notes
adsapi pdf obsidian ~/Papers --apply

# Overwrite existing notes (default leaves them untouched)
adsapi pdf obsidian ~/Papers --apply --force
```

### 8) Quick status and library sync

```bash
# Show identified/unresolved/duplicate summary
adsapi pdf status ~/Papers --recursive

# Preview push to ADS library
adsapi pdf push-library ~/Papers <library_id>

# Execute push
adsapi pdf push-library ~/Papers <library_id> --apply
```

## Example Workflow Docs

- Query ADS and generate CV-ready Markdown documents:
  - `docs/query-to-document-example.md`

## Developer Note: Keep Docs Updated

When changing code, update documentation in the same commit.

1. Update relevant docs pages (for example `docs/getting-started.md`, `docs/cli.rst`, `docs/api/index.rst`).
2. If you add a new public module, add it to the autosummary list in `docs/api/index.rst`.
3. Run docs checks before commit:

```bash
uv sync
uv run pre-commit install
uv run pre-commit run docs-check --all-files
uv run pre-commit run --all-files
```

RTD will also build docs for pushed commits/PRs via `.readthedocs.yaml`.

## License

BSD-3-Clause
