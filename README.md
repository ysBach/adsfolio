# ADSfolio

A desktop application and Python toolkit for organizing research PDFs with NASA
ADS / SciXplorer, Crossref, and DataCite metadata. ADSfolio is an independent
project, not an official ADS application.

![ADSfolio logo](docs/_static/adsfolio.png)

## Install the desktop app

Download [ADSfolio 0.1.0](https://github.com/ysBach/adsfolio/releases/latest).
Desktop downloads include Python and the application dependencies. You do not
need a Python environment or an ADS token to browse your local library.

| System | Download and launch |
| --- | --- |
| macOS 15+, Apple Silicon | Extract `adsfolio-macos-arm64.zip`, move `ADSfolio.app` to Applications, then open it. |
| macOS 15+, Intel | Extract `adsfolio-macos-x86_64.zip`, move `ADSfolio.app` to Applications, then open it. |
| Windows x86-64 | Extract `adsfolio-windows-x86_64.zip`, then open `adsfolio.exe` inside the extracted folder. |
| Linux x86-64 | Extract `adsfolio-linux-x86_64.tar.gz`, then run `adsfolio` inside the extracted folder. Requires glibc 2.39+ and system desktop libraries. |

Keep the complete extracted folder together on Windows and Linux.
See the [platform requirements](docs/desktop.md) for details.

### macOS with Homebrew

```sh
brew install --cask ysBach/adsfolio/adsfolio
open -a ADSfolio
```

**Version 0.1.0 is unsigned and not notarized.** macOS can block launch even
when Homebrew installation succeeds. After attempting to open ADSfolio, review
**System Settings → Privacy & Security → Open Anyway** if macOS offers that
option. Approve it only if you trust the release. Installation alone does not
establish that the app can launch. Windows can also show publisher warnings.

### Start using your library

1. Open ADSfolio. Choose **File → Add Library Root…** and select your PDF folder.
   To include nested folders, enable **Include Subdirectories** on the root.
2. Select a PDF to inspect or edit its metadata. Double-click a row to open it
   in your system PDF viewer.
3. Use **Identify** to look up publication metadata. Configure your ADS token
   through **Tools → NASA ADS Token…** when you need ADS services. Crossref and
   DataCite can look up DOIs without an ADS token; choose provider order in Identify.
4. Preview **Rename** before applying filename changes. Papers with sufficient
   metadata can be renamed without an ADS bibcode. Use **File → Local References…**
   for manual entries and BibTeX import/export, including references without PDFs.
5. Press **Refresh** or **F5** after moving or deleting files outside ADSfolio.
   This reconciles local files. Use the separate ADS refresh actions to update
   remote metadata. Permanent cleanup remains a separate operation.

See the [interactive application guide](docs/gui.md) for watching folders,
selection, metadata editing, notifications, and refresh options.

### Updates and existing libraries

Quit ADSfolio before replacing the app, or run `brew upgrade --cask adsfolio`.
The catalog, metadata, and history live in your user data directory, outside the
application bundle. Retain that directory when upgrading or uninstalling.

Earlier settings and catalogs are copied into ADSfolio storage on first use;
the source remains intact. Close the earlier application before migration and
keep its data until you verify the migrated library. Saved automatic watch and
rename settings remain active, so review them before resuming library work.

## Command-line and Python use

The command-line tools and Python API complement the desktop application.
Homebrew installs the `adsfolio` command alongside the app.

PyPI publication is not enabled for 0.1.0. For development or Python API use,
clone this repository and run `uv sync --all-extras` in its directory. Launch
with `uv run adsfolio-gui`, or prefix CLI examples below with `uv run`.
Python 3.10 or newer is required for source installations.

For ADS queries, obtain a token from
[ADS settings](https://ui.adsabs.harvard.edu/user/settings/token).
The desktop token dialog saves it for CLI use too. Alternatively, set
`ADS_API_TOKEN` in your environment.

### CLI Usage

```bash
# Search for papers
adsfolio search "author:Einstein" --fl bibcode,title --rows 5

# Export to BibTeX
adsfolio export 2020ApJ...900..100S -f bibtex

# List your libraries
adsfolio library list

# Get library contents as BibTeX
adsfolio library get <library_id> --bibtex
```

### Python API

```python
from adsfolio import ADSClient

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

1. Run `adsfolio set` once (token + identify profile and publication-cache build).
2. Inspect or refresh the cache with `adsfolio cache status` / `adsfolio cache refresh`.
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
`platformdirs` cache path returned by `adsfolio cache status`. Use `adsfolio cache
clear` to remove the publication cache and `adsfolio pdf export DIR -o
snapshot.json` for a readable registry backup. Legacy `.adsfolio-pdfs.sqlite`
and `.adsfolio-pdfs.json` files are imported read-only and remain available as
source backups.

### Desktop GUI

Install the optional PySide6 desktop browser with `pip install 'adsfolio[gui]'`,
then run `adsfolio gui` (or `adsfolio-gui`). It provides a multi-root, searchable
and sortable PDF table with metadata, OS-default opening/reveal actions, and
Identify/Rename dialogs that execute the existing CLI commands as subprocesses.

#### Custom citation styles

Use `Tools -> Custom Citation Styles...` to manage the citation text shown in
the GUI. The editor supports **Add**, **Duplicate**, **Rename** by editing the
selected style's **Name** field, **Reorder** with **Move Up**/**Move Down**, and
**Delete**. Styles remain in the displayed order. **Update Preview** validates the current
draft and uses the one selected table record when exactly one row is selected;
otherwise it uses the built-in example. Preview does not save or apply edits.
**Save** validates every style, applies the list to the running GUI, and saves
it for the next launch. **Cancel** discards the draft. Invalid templates,
fields, names, IDs, duplicate names/IDs, and author settings leave the editor
open and identify the affected style. A control is focused only when one exists;
IDs have no editor field.

Each style adds a computed, non-sortable table column and a read-only,
non-lockable detail row after **Bibcode** by default, in style order. Use
**View -> Appearance...** to reorder or hide these generated items and set
their optional font, size, and color; **OK** keeps those settings for saving
and **Cancel** restores the previous appearance. New items are visible by
default, and style plus appearance settings persist across restart. Click a
non-empty table cell or detail value to copy its complete text. Empty rendered
values do not change the clipboard. Rendering uses only local catalog/row
metadata and does not issue an ADS request. Custom citation text is not a
filename and does not rename PDFs.

Citation templates use these exact fields:

| Field | Meaning |
| --- | --- |
| `{bibcode}` | ADS Bibcode, unchanged. |
| `{year}` | Publication year. |
| `{title}` | First title value, with ADS HTML converted to plain text. |
| `{first_author_surname}` | First author's surname only; `Bach, Yoonsoo P.` becomes `Bach`. |
| `{first_author_initials}` | First-author surname plus given-name initials; `Bach, Yoonsoo P.` becomes `BachYP`. |
| `{authors}` | Selected-format author list; above **Max authors**, retain **Authors when truncated** leading authors and append the suffix. |
| `{authors_without_suffix}` | The same author list without the truncation suffix. |
| `{journal}` | First bibstem, falling back to `pub` only when bibstem is absent. |
| `{bibstem}` | First ADS bibstem only; no fallback. |
| `{pub}` | ADS publication or journal text. |
| `{volume}` | Publication volume. |
| `{page}` | First page or article identifier. |
| `{{` and `}}` | Literal opening and closing braces. |

Missing values render as empty strings; spaces, punctuation, and Unicode are
preserved. ADS HTML display markup is converted to plain text, and no `.pdf`
extension is appended. Empty names, templates, or IDs, invalid author settings,
duplicate names/IDs, and invalid template syntax leave the editor open and
identify the affected style. A control is focused only when one exists; IDs
have no editor field. Format specifiers, conversions,
attribute/item access, unknown fields, and unmatched braces are not permitted.
The author controls are **Surname + initials** (such
as `BachYP`), **Surname only** (`Bach`), or **Last, First** (`Bach, Yoonsoo
P.`), plus **Max authors**, **Authors when truncated**, **Author separator**,
and **Truncation suffix**. A no-comma author uses its last token as surname and
preceding tokens as given names.

```text
{first_author_initials} ({year}) {bibstem}, {volume}, {page}
→ BachYP (2024) A&A, 123, 456
```

#### Add identified PDFs to a remote ADS library

The GUI distinguishes a **local PDF library** from a **remote ADS library**.
A local PDF library is a filesystem root registered with `File -> Add Library
Root` and backed by adsfolio's local catalog. A remote ADS library is a
destination managed by ADS/SciXplorer and shown separately in the destination
table. This GUI workflow never renames or moves PDFs and never edits the local
catalog, local metadata, Rename settings, or watch state.

Open it from `Tools -> Add to ADS Library...` or the table context menu's
**Add to ADS Library...** entry. **Selected rows** snapshots the selected rows
currently loaded in the table. If the context-menu row was not selected, that
row becomes the sole selection first. **All identified files in this local
library** queries every resolved record in the current local catalog,
independent of the active search, virtual view, and table pagination.

Only a resolved record with a non-empty ADS Bibcode is eligible. Ineligible
records are reported but not submitted. Bibcodes are deduplicated in stable
first-seen order, so duplicate local files produce one remote document at
most. The dialog reports source files, eligible unique Bibcodes, ineligible
files, and local duplicate Bibcodes.

The remote destination table shows name, document count, visibility, and
permission. Only `owner`, `admin`, and `write` permissions are selectable;
`read` and unknown permissions remain visible but disabled. **Create Library**
accepts a non-empty name, optional description, and public/private choice. It
creates and selects an empty remote ADS library; it never implicitly adds the
pending Bibcodes.

Opening the dialog lists ADS libraries, and selecting a destination fetches
its documents for duplicate preflight. Those are read-only ADS requests. A
complete preflight gives an exact **Already present** count and excludes those
Bibcodes. If preflight fails, the count is **Unknown**, the dialog warns that
all eligible Bibcodes may be submitted, and the final confirmation repeats
that fallback. Creating an empty library is a separate user-initiated remote
creation request. A document-add POST is made only after the final affirmative
confirmation; opening the dialog, changing scope, selecting a destination, or
loading remote documents never adds documents.

The confirmation shows the destination and permission, eligible unique count,
already-present count or `Unknown`, ineligible count, local-duplicate count,
and number to submit. Confirmed additions use ADS's contract limit of at most
2,000 Bibcodes per request and run asynchronously. Cancellation takes effect
before the next batch; an in-flight request may finish. After the first failed
batch, acknowledged batches remain acknowledged and later batches are
unattempted. Results distinguish acknowledged, already present, failed,
status-unknown, unattempted, local duplicate, ineligible, and cancelled
counts.

A timeout or connection failure after an add request is sent is **status
unknown**, because ADS may have accepted that batch; it is not treated as a
definite failure. **Retry Remaining** first reloads the remote destination
and submits only remaining Bibcodes still absent. If reconciliation fails,
adsfolio warns that a raw retry may resubmit an uncertain batch and requires a
second explicit confirmation.

Do not run identify or watch against the same library from two machines at once.
Keep `pdf export` snapshots as backups.

### 0) One-time setup

```bash
# Do this once unless your token/profile changes
adsfolio set
```

### 1) Optional OCR precheck

Use this only if you have scanned/non-OCR PDFs.

```bash
# Dry-run by default
adsfolio pdf ocr ~/Papers --recursive -o ~/Papers/ocr_required

# Move likely non-OCR files
adsfolio pdf ocr ~/Papers --recursive -o ~/Papers/ocr_required --apply

# Also generate a runnable OCR batch script in current directory
adsfolio pdf ocr ~/Papers --recursive -o ~/Papers/ocr_required --make-ocr-sh
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
adsfolio pdf identify ~/Papers --recursive

# Optional unresolved routing (dry-run; use --apply to move)
adsfolio pdf identify ~/Papers --recursive \
  --output-unresolved ~/Papers/unresolved
```

Identify summary:

1. Central-catalog match using stored filesystem facts and SHA-256 only when needed.
2. Publication-cache match first (`adsfolio set` ORCID/query names/library IDs).
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
adsfolio pdf identify ~/Papers --recursive --ai-csv ~/Papers/unresolved_ai.csv
```

### 3) Rename: dry-run first, then apply

```bash
# Preview rename plan from registry metadata
adsfolio pdf rename ~/Papers \
  --scheme "{authors}_{bibcode}-{title}" \
  --author-format surname_initials \
  --max-authors 3

# Execute rename plan
adsfolio pdf rename ~/Papers \
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
adsfolio pdf rename ~/Papers --allow-ads --output-unresolved unresolved --apply
```

When `~/Papers/unresolved` exists, `rename --apply` also moves resolved records
from that directory into `~/Papers` after rendering the new name. Files without
successful ADS metadata remain in `unresolved`.

### 5) Watch a folder for incoming PDFs

```bash
# Polling backend (default), applies renames/moves
adsfolio pdf watch ~/Papers --recursive --interval 10 --apply

# Watchdog backend (requires adsfolio[full])
adsfolio pdf watch ~/Papers --recursive --use-watchdog --interval 60 \
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
adsfolio pdf rename ~/Papers --max-authors 2 \
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
adsfolio pdf upgrade ~/Papers

# Execute upgrades
adsfolio pdf upgrade ~/Papers --apply

# Optional overwrite mode with backup
adsfolio pdf upgrade ~/Papers --apply \
  --overwrite-arxiv-with-official \
  --arxiv-backup-if-overwritten ~/Papers/arxiv-backup
```

You can also do automatic upgrade checks while watching:

```bash
adsfolio pdf watch ~/Papers --recursive --apply \
  --auto-upgrade --upgrade-interval 24
```

### 7) Generate Obsidian-friendly notes

For PDFs that already live inside an Obsidian vault, write one Markdown note per tracked PDF
(YAML frontmatter with title/authors/year/journal/bibcode, plus an `## Abstract` section). Notes
link back to the PDF with an absolute `file://` link — PDFs are never copied or moved.

```bash
# Dry-run: no ADS queries, no files written
adsfolio pdf obsidian ~/Papers

# Fetch/cache missing abstracts from ADS and write notes
adsfolio pdf obsidian ~/Papers --apply

# Overwrite existing notes (default leaves them untouched)
adsfolio pdf obsidian ~/Papers --apply --force
```

### 8) Quick status and library sync

```bash
# Show identified/unresolved/duplicate summary
adsfolio pdf status ~/Papers --recursive

# Preview push to ADS library
adsfolio pdf push-library ~/Papers <library_id>

# Execute push
adsfolio pdf push-library ~/Papers <library_id> --apply
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
