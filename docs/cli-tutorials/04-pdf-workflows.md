# Tutorial 4: PDF workflows

This tutorial covers `adsfolio pdf` end-to-end: identify, rename, watch, status, upgrade, and library push.

## 0) Start with `--help`

Before you apply file operations, review the full PDF command surface:

~~~bash
adsfolio pdf --help
adsfolio set --help
adsfolio cache --help
adsfolio pdf ocr --help
adsfolio pdf identify --help
adsfolio pdf refresh-ads --help
adsfolio pdf rename --help
adsfolio pdf watch --help
adsfolio pdf status --help
adsfolio pdf upgrade --help
adsfolio pdf push-library --help
adsfolio pdf export --help
adsfolio pdf obsidian --help
~~~

Key PDF options:

| Option | Meaning | When to use |
| --- | --- | --- |
| `identify --settings-scope global/library` | Load reusable global or per-library settings | Share defaults or keep one library distinct |
| `identify --recursive/--no-recursive` | Override saved recursive scanning for one run | Multi-folder paper archives |
| `identify --strategy` | Select one or more ordered evidence strategies for one run | Limit or reorder local metadata extraction and lookups |
| `identify --debut-year/--end-year` | Override publication-prefetch years for one run | Narrow a cache refresh without changing setup |
| `identify --refresh-cache` | Rebuild the publication cache first | Deliberate cache refresh |
| `identify --output-unresolved` | Move unresolved PDFs to a separate directory | Keep review queue clean |
| `ocr --output` | Route likely scanned/non-OCR PDFs | Separate files needing OCR before identify |
| `ocr --make-ocr-sh` | Generate `ocrmypdf.sh` in current directory | Run OCR in batch with one command |
| `identify --ai-csv` | Apply AI-enriched unresolved CSV directly | Non-interactive AI roundtrip |
| `identify -v` | Print per-file unresolved move logs | Debug detailed unresolved routing |
| `identify --verbose-parser` | Show raw parser warnings from malformed PDFs | Debugging problematic files or corrupt inputs |
| `--apply` | Apply the command's file moves, renames, remote additions, or upgrades | Preview the command first. This flag does not disable all catalog writes or ADS requests |
| `rename --scheme` | Template defining output filename | Enforce your citation/file naming convention |
| `rename --author-format` | Author token style (`surname`, `surname_initials`, etc.) | Match your preferred filename style |
| `watch --use-watchdog` | Event-driven watcher backend | Lower-latency updates on supported systems |
| `watch --auto-upgrade` | Periodic arXiv→published upgrade checks | Keep long-lived PDF archives canonical |
| `watch --upgrade-interval` | Hours between auto-upgrade passes | Balance freshness and API activity |
| `upgrade --overwrite-arxiv-with-official` | Replace tracked arXiv file contents | Preserve stable path/filename while updating content |
| `upgrade --arxiv-backup-if-overwritten` | Save backup before overwrite | Safety rollback path |
| `upgrade --skip-annotation-port` | Download official PDF without transferring annotations | Fast upgrades or no `pymupdf` environment |
| `upgrade --force` | Re-check entries already marked as published | Force re-scan after manual registry edits |
| `push-library --apply` | Push resolved bibcodes to ADS library | Synchronize local PDF collection with ADS library |
| `obsidian --notes-dir` | Write `.md` notes to a directory other than the PDFs | Keep notes separate from the PDF folder |
| `obsidian --force` | Overwrite existing notes | Refresh notes after re-running `identify` |
| `obsidian --apply` | Fetch/cache missing abstracts and write notes | Always run after dry-run verification |

## Prerequisite

Install optional features:

```bash
pip install 'adsfolio[full]'
```

Run `adsfolio set` once before your first `pdf identify` run. This step lets the tool cache
these items:

- your publication list (ORCID + optional names + year window)
- optional library papers
- optionally, references/citations of that library set

This cache reduces per-file ADS queries.
Run `adsfolio set` again only when token/profile values change.

## How identify works

`adsfolio pdf identify` uses the central user catalog `pdf-library.sqlite` as its
incremental state. The tool imports legacy per-root registry and JSON files. It does
not rename or remove them.
The user-level publication cache is separate. `adsfolio cache` manages it.

For each new, changed, or unresolved catalog location, `identify` does these steps.

Find new and changed files:

1. Scans the selected root top-level or recursively. When `--skip-unresolved-directory`
   is enabled, it skips the configured unresolved destination before hashing.
2. Compares stored size, modification time, and filesystem identity first.
3. Computes SHA-256 only for new or changed PDFs, then reuses matching catalog attachments.

Run the evidence strategies:

4. Runs the enabled evidence strategies in order. Each stage handles only PDFs that
   are still unresolved. Before it makes any ADS network request, it checks the
   root's cached ADS documents and publication cache.
5. With the default strategy order, extracts and looks up:
   `filename_bibcode`, `filename_doi`, `pdf_metadata_bibcode`,
   `pdf_metadata_doi`, `first_page_bibcode`, `first_page_doi`,
   `structured_filename`, `pdf_metadata_title`, and `first_page_title`.
   It batches candidate lookups by identifier where possible. Repeat
   `--strategy` to select an ordered subset for one run, for example:

   ```bash
   adsfolio pdf identify ~/Papers \
     --strategy filename_doi \
     --strategy first_page_title
   ```

Handle the files that remain unresolved:

6. If files remain unresolved, writes `unresolved.csv` and prints an AI prompt.
7. In interactive mode, waits for an AI-enriched CSV path and applies batched
   ADS bibcode/DOI lookups from that CSV.

With `--recursive`, the same central catalog still holds all tracked files.

First-page title identification collects typography candidates and checks them
against ADS titles. A larger journal masthead does not automatically become the
title. Conflicting records remain unresolved. Uniform-font OCR and titles with
mixed font sizes can still require a verified DOI or Bibcode.

Resolved PDFs with unchanged content reuse their catalog metadata without
repeat identification queries. Unresolved or changed PDFs remain eligible for
identification.

## 1) Optional: route non-OCR PDFs before identify

Dry-run:

```bash
adsfolio pdf ocr ~/Papers --recursive -o ~/Papers/ocr_required
```

Apply move:

```bash
adsfolio pdf ocr ~/Papers --recursive -o ~/Papers/ocr_required --apply

# Generate ocrmypdf.sh (uses the same --output directory as OCR input)
adsfolio pdf ocr ~/Papers --recursive -o ~/Papers/ocr_required --make-ocr-sh
bash ocrmypdf.sh
```

The command prints an `ocrmypdf` script template. If these files have annotations,
check OCR outputs carefully. You can manually move selected files out of
`ocr_required` to skip OCR for those files.

## 2) Identify PDFs and update catalog metadata

```bash
adsfolio pdf identify ~/Papers --recursive
```

This command matches the publication cache, reuses cached fallback ADS documents, and
writes the central catalog outside the library root.

Reusable Identify settings include these values:

- ordered evidence strategies
- recursive scanning
- unresolved destination
- unresolved-directory skipping
- verbosity

Personal identity and publication-year bounds are global profile data. You configure
them with `adsfolio set` or **Tools → My Publication Profile…**. They are not
library-scoped Identify behavior.

For a paper that ADS does not index, choose DOI providers and their order. This
example does not use ADS or need an ADS token:

```bash
adsfolio pdf identify /path/to/papers --provider crossref --provider datacite
adsfolio pdf rename /path/to/papers
```

DOI lookup fills missing metadata and records its provider and file digest.
Existing metadata, manual edits, and catalog history remain intact. See
[My paper is not on ADS](../local-references.md) for the desktop steps.

The default Identify scope is `global`. For values stored with the current library,
use `--settings-scope library`. Explicit options override the selected settings for
one run without mutating them:

```bash
adsfolio pdf identify ~/Papers --settings-scope library --no-recursive
adsfolio pdf identify ~/Papers --clear-output-unresolved
```

Use `--debut-year`, `--end-year`, `--clear-debut-year`, or `--clear-end-year` to
override the global profile's prefetch window for one run. `--refresh-cache`,
`--allow-ads`, and `--apply` are never inherited.
You must supply them each time.

Library exports now include the canonical
`identify_settings` payload. Older adsfolio releases ignore that new payload but
can still read the retained `identify_profile` provenance.

To inspect the state or make a backup, run:

```bash
adsfolio cache status
adsfolio pdf export ~/Papers -o pdfs-backup.json
```

If many files remain unmatched after cache/profile matching,
`identify` asks you to approve the fallback query batches before it starts them.
It also prints an estimated count of DOI batch queries.
In non-interactive runs, `identify` skips fallback ADS queries by default. To run them
without a prompt, use `--allow-ads`.

Use `adsfolio rate-limit` to inspect the limits that ADS returns for your account.
Request allowances and reset times depend on the endpoint and server response.

If unresolved files remain at the end, `identify` writes `unresolved.csv` in the scan
root. The file contains `filename`, `relative_path`, and a `first_page_text` preview.
`identify` also prints an LLM prompt template so you can parse metadata with your
preferred AI.

See unresolved routing (dry-run, no moves):

```bash
adsfolio pdf identify ~/Papers --recursive \
  --output-unresolved ~/Papers/unresolved
```

Move unresolved files:

```bash
adsfolio pdf identify ~/Papers --recursive \
  --output-unresolved ~/Papers/unresolved --apply

# Optional: print per-file "Moved unresolved" lines
adsfolio pdf identify ~/Papers --recursive \
  --output-unresolved ~/Papers/unresolved --apply \
  -v
```

Skip files already in the unresolved destination on later recursive runs:

```bash
adsfolio pdf identify ~/Papers --recursive \
  --output-unresolved ~/Papers/unresolved \
  --skip-unresolved-directory
```

Identify updates catalog records even without `--apply`. It may also make ADS requests
or export an unresolved CSV without `--apply`. That flag controls the unresolved-file moves.

The skip is path-based and happens before hashing or ADS work. It does not skip
unresolved-status records elsewhere. If you omit `--output-unresolved`, the
effective skipped path is `~/Papers/unresolved`. The option defaults off.

If you already generated AI hints, pass them directly:

```bash
adsfolio pdf identify ~/Papers --recursive --ai-csv ~/Papers/unresolved_ai.csv
```

## 3) Rename safely: dry-run then apply

`adsfolio pdf refresh-ads` also renames successfully updated PDFs immediately.
It uses the library's saved Rename settings, or global settings when no library
override exists. To use global settings explicitly, pass `--rename-scope global`.

Files under the configured unresolved directory return to the library root
with their new name. Other PDFs stay in their current directory. The
`--output-unresolved` option overrides the saved unresolved directory.

`refresh-ads` does not overwrite existing destinations. The log reports skipped
renames. Those files keep the refreshed ADS metadata. Optional citation-metrics
requests run after these renames. A metrics failure does not undo completed file
updates.

Available template variables: `{bibcode}`, `{year}`, `{title}`,
`{first_author}`, `{first_author_full}`, `{authors}`, `{authors_N}`, `{journal}`,
`{volume}`, `{page}`

The template becomes the filename. `{authors}` adds a trailing `+` when
`--max-authors` truncates the list. `{authors_N}` uses the same limited list
without that marker. Missing metadata produces an empty component. Use `{{` and `}}` for
literal braces.

Filename rendering follows these rules:

- Unknown fields stop the rename.
- The tool sanitizes unsafe filename characters.
- The tool appends `.pdf` automatically when it is absent.
- The tool limits results to 240 UTF-8 bytes.
- The tool skips a rename when its target already exists.

Dry-run:

```bash
adsfolio pdf rename ~/Papers \
  --scheme "{authors}_{bibcode}-{title}" \
  --author-format surname_initials \
  --max-authors 3
```

Apply:

```bash
adsfolio pdf rename ~/Papers \
  --scheme "{authors}_{bibcode}-{title}" \
  --author-format surname_initials \
  --max-authors 3 \
  --apply
```

Before it renders names, `rename --allow-ads` identifies pending PDFs recursively.
It uses the same ADS workflow as `identify`.

If the scan root contains
`unresolved/`, applying the rename also moves resolved records from that
directory into the scan root. Unresolved files stay in `unresolved/`.

To route unresolved PDFs during this identify step, pass
`--output-unresolved unresolved`. Relative paths resolve below the scan root.

Author format options:

| Value | Example output |
| --- | --- |
| `surname` | `Smith` |
| `surname_initials` (default) | `SmithJ` |
| `last_comma_first` | `Smith,_J.` |
| `ads_custom:FORMAT` | delegates to ADS export API |

## 4) Watch a folder for new PDFs

Polling (default):

```bash
adsfolio pdf watch ~/Papers --recursive --interval 10 --apply
```

Watchdog backend (requires `adsfolio[full]`):

```bash
adsfolio pdf watch ~/Papers --recursive --use-watchdog --interval 60 \
  --output-unresolved unresolved --apply
```

At startup, CLI watch reconciles the filesystem. It can process untracked or
changed PDFs that are already present. It skips catalog entries whose filesystem
facts still match. Later `--interval` passes reconcile filesystem facts to catch
missed events. These passes do not hash or re-identify unchanged PDFs.

This initial reconciliation differs from the GUI watcher. The GUI watcher's startup
scan establishes a baseline and leaves existing PDFs alone. The GUI only offers
later arrivals for watch processing.

With `--use-watchdog`, event callbacks only queue paths. One worker performs
PDF parsing, ADS queries, renames, and catalog writes.

Watch mode defers incomplete, encrypted, or concurrently changing PDFs until a
later event or reconciliation. Watch mode matches the `.pdf` suffix in any letter
case. It ignores symbolic links to PDFs outside the watched root.
Watch-mode unresolved destinations must be inside the watched root.

If a pass cannot read a folder or file (for example, permission denied), it logs
the error and skips that pass. It does not mark tracked files missing. The next
pass retries.

Filename rendering preserves Unicode by default, for example `Wesołowski`.
Use `--char-policy ascii` to request `Wesolowski`.

Without `--apply`, watch mode
reports proposed path changes but does not rename or move files.

The author separator and truncation suffix are independent settings.
`--max-authors` is inclusive. The tool shows a list at or below that size in full.
It truncates a longer list. With `--max-authors 2`, the tool truncates three or more
authors.

`--authors-when-truncated` controls how many leading
authors remain in a truncated result. It defaults to one. For example,
`--max-authors 2 --authors-when-truncated 1 --author-separator "+"
--author-truncation-suffix "+"` renders three authors as `SmithJ+`.
Setting `--authors-when-truncated 2` renders `SmithJ+DoeA+` instead.
`{authors_N}` uses the configured retained authors and separator but never adds
the truncation suffix.

The Rename dialog persists these values globally or per
library.
Use **Save Defaults** there to update the standard without renaming existing
PDFs.

### Run continuously on macOS

1. Create `~/Library/LaunchAgents/org.example.adsfolio-pdf-watch.plist` with the content below.
2. Replace every `/Users/YOU/...` path with an absolute path valid for your account.
   The executable is the output of `which adsfolio`.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>org.example.adsfolio-pdf-watch</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/YOU/path/to/venv/bin/adsfolio</string>
    <string>pdf</string><string>watch</string>
    <string>/Users/YOU/Papers</string>
    <string>--recursive</string><string>--use-watchdog</string>
    <string>--interval</string><string>60</string>
    <string>--output-unresolved</string><string>unresolved</string>
    <string>--apply</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/Users/YOU/Library/Logs/adsfolio-pdf-watch.log</string>
  <key>StandardErrorPath</key><string>/Users/YOU/Library/Logs/adsfolio-pdf-watch.err</string>
</dict>
</plist>
```

Load or remove it explicitly:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/org.example.adsfolio-pdf-watch.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/org.example.adsfolio-pdf-watch.plist
```

The package does not install or start this agent automatically because the
executable path, library path, naming policy, and filesystem permissions are
user-specific.

## 5) Check current state

```bash
adsfolio pdf status ~/Papers --recursive
```

This command shows identified catalog locations, unresolved files on disk, and duplicate hash
groups. With `--recursive`, all locations share the same central catalog.

Do not run identify/watch from two machines against the same library concurrently.
For portable backups, use the export command.

## 6) Upgrade arXiv PDFs to published versions

Dry-run:

```bash
adsfolio pdf upgrade ~/Papers
```

Apply:

```bash
adsfolio pdf upgrade ~/Papers --apply
```

Overwrite tracked arXiv file with official PDF and keep backups:

```bash
adsfolio pdf upgrade ~/Papers --apply \
  --overwrite-arxiv-with-official \
  --arxiv-backup-if-overwritten ~/Papers/arxiv-backup
```

Force re-check entries already marked as published:

```bash
adsfolio pdf upgrade ~/Papers --apply --force
```

## 7) Auto-upgrade while watching

```bash
adsfolio pdf watch ~/Papers --recursive --apply \
  --auto-upgrade --upgrade-interval 24
```

## 8) Push resolved bibcodes to an ADS library

Dry-run:

```bash
adsfolio pdf push-library ~/Papers <library_id>
```

Apply:

```bash
adsfolio pdf push-library ~/Papers <library_id> --apply
```

## 9) Generate Obsidian-friendly notes

If the PDFs already live inside an Obsidian vault, `pdf obsidian` writes one Markdown note per
tracked PDF next to each file. Each note has YAML frontmatter with
title/authors/year/journal/bibcode, plus an `## Abstract` section. Notes link to the
PDF with an absolute `file://` link. `pdf obsidian` never copies, moves, or embeds PDFs.

Dry-run (no ADS queries, no files written):

```bash
adsfolio pdf obsidian ~/Papers
```

Apply — fetches any missing abstracts from ADS in one batched query, caches them into the
central catalog, and writes the notes:

```bash
adsfolio pdf obsidian ~/Papers --apply
```

On re-runs, `pdf obsidian` leaves existing notes untouched, so your manual edits in Obsidian
stay. To overwrite existing notes, pass `--force`:

```bash
adsfolio pdf obsidian ~/Papers --apply --force
```

Write notes to a separate folder instead of colocating them with the PDFs:

```bash
adsfolio pdf obsidian ~/Papers --apply --notes-dir ~/Papers/notes
```

## Next

Continue with [Tutorial 5: Operations and troubleshooting](./05-operations-and-troubleshooting.md).
