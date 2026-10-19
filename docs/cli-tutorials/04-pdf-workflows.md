# Tutorial 4: PDF Workflows

This tutorial covers `adsfolio pdf` end-to-end: identify, rename, watch, status, upgrade, and library push.

## 0) Start with `--help`

Review the full PDF command surface before applying file operations:

~~~bash
adsfolio pdf --help
adsfolio set --help
adsfolio cache --help
adsfolio pdf ocr --help
adsfolio pdf identify --help
adsfolio pdf rename --help
adsfolio pdf watch --help
adsfolio pdf status --help
adsfolio pdf upgrade --help
adsfolio pdf push-library --help
adsfolio pdf export --help
adsfolio pdf obsidian --help
~~~

Key PDF options to understand:

| Option | Meaning | When to use |
| --- | --- | --- |
| `identify --settings-scope global/library` | Load reusable global or per-library settings | Share defaults or keep one library distinct |
| `identify --recursive/--no-recursive` | Override saved recursive scanning for one run | Multi-folder paper archives |
| `identify --debut-year/--end-year` | Override publication-prefetch years for one run | Narrow a cache refresh without changing setup |
| `identify --refresh-cache` | Rebuild the publication cache first | Deliberate cache refresh |
| `identify --output-unresolved` | Move unresolved PDFs to a separate directory | Keep review queue clean |
| `ocr --output` | Route likely scanned/non-OCR PDFs | Separate files needing OCR before identify |
| `ocr --make-ocr-sh` | Generate `ocrmypdf.sh` in current directory | Run OCR in batch with one command |
| `identify --ai-csv` | Apply AI-enriched unresolved CSV directly | Non-interactive AI roundtrip |
| `identify -v` | Print per-file unresolved move logs | Debug detailed unresolved routing |
| `identify --verbose-parser` | Show raw parser warnings from malformed PDFs | Debugging problematic files or corrupt inputs |
| `--apply` | Execute destructive actions (rename/move/push/upgrade) | Always run after dry-run verification |
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
pip install adsfolio[full]
```

Run `adsfolio set` once before first `pdf identify` so the tool can cache your publication list
(ORCID + optional names + year window), optional library papers, and optionally
references/citations of that library set. This dramatically reduces per-file ADS queries.
Run `adsfolio set` again only when token/profile values change.

## How identify works

`adsfolio pdf identify` uses the central user catalog `pdf-library.sqlite` as its
incremental state. Legacy per-root registry and JSON files are imported without
being renamed or deleted.
The user-level publication cache is separate and is managed by `adsfolio cache`. On each run it:

1. Compares stored size, modification time, and filesystem identity first.
2. Computes SHA-256 only for new or changed PDFs, then reuses matching catalog attachments.
3. Tries the publication cache first, then the folder ADS-document cache.
4. For remaining PDFs, extracts DOI/bibcode candidates from filename, PDF metadata, and first-page text.
5. Resolves candidates with batched ADS DOI/bibcode queries, then title fallback queries for remaining PDFs.
6. If files remain unresolved, writes `unresolved.csv` and prints an AI prompt.
7. In interactive mode, waits for an AI-enriched CSV path and applies batched
   ADS bibcode/DOI lookups from that CSV.

With `--recursive`, all tracked files still go into the same central catalog.
Already-identified PDFs are never re-queried regardless of how many times you
run the command.

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
verify OCR outputs carefully. You can manually move selected files out of
`ocr_required` to skip OCR for those files.

## 2) Identify PDFs and create registry metadata

```bash
adsfolio pdf identify ~/Papers --recursive
```

This matches the publication cache, reuses cached fallback ADS documents, and
writes the central catalog outside the library root.

Reusable Identify settings include recursive scanning, filename inference,
unresolved destination, unresolved-directory skipping, and verbosity. Personal
identity and publication-year bounds are global profile data configured by
`adsfolio set` or **Tools → My Publication Profile…**; they are not library-scoped
Identify behavior. The default Identify scope is
`global`; use `--settings-scope library` for values stored with the current
library. Explicit options override the selected settings for one run without
mutating them:

```bash
adsfolio pdf identify ~/Papers --settings-scope library --no-recursive
adsfolio pdf identify ~/Papers --clear-output-unresolved
```

Use `--debut-year`, `--end-year`, `--clear-debut-year`, or `--clear-end-year` to
override the global profile's prefetch window for one run. `--refresh-cache`,
`--allow-ads`, and `--apply` are never inherited;
they must be supplied each time. Library exports now include the canonical
`identify_settings` payload. Older adsfolio releases ignore that new payload but
can still read the retained `identify_profile` provenance.

Inspect or back up the state with:

```bash
adsfolio cache status
adsfolio pdf export ~/Papers -o pdfs-backup.json
```

If many files remain unmatched after cache/profile matching,
`identify` asks for confirmation before launching fallback query batches and prints an
estimated DOI batch query count.
At high traffic levels (about >1000 requests in 24h), ADS may temporarily block
requests for roughly one day.
In non-interactive runs, fallback ADS queries are skipped by default; use
`--allow-ads` to run them without a prompt.
If unresolved files remain at the end, `identify` writes `unresolved.csv` in the scan
root with `filename`, `relative_path`, and a `first_page_text` preview. It also prints
an LLM prompt template so you can parse metadata with your preferred AI.

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

The skip is path-based and happens before hashing or ADS work. It does not skip
unresolved-status records elsewhere. If `--output-unresolved` is omitted, the
effective skipped path is `~/Papers/unresolved`. The option defaults off.

If you already generated AI hints, pass them directly:

```bash
adsfolio pdf identify ~/Papers --recursive --ai-csv ~/Papers/unresolved_ai.csv
```

## 3) Rename safely: dry-run then apply

Available template variables: `{bibcode}`, `{year}`, `{title}`,
`{first_author}`, `{first_author_full}`, `{authors}`, `{authors_N}`, `{journal}`,
`{volume}`, `{page}`

The template becomes the filename. `{authors}` adds a trailing `+` when
`--max-authors` truncates the list; `{authors_N}` uses the same limited list
without that marker. Missing metadata produces an empty component. Unknown
fields stop the rename, unsafe filename characters are sanitized, and `.pdf` is
appended automatically when absent. Results are limited to 240 UTF-8 bytes, and
a rename is skipped when its target already exists. Use `{{` and `}}` for
literal braces.

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

`rename --allow-ads` identifies pending PDFs recursively with the same ADS
workflow as `identify` before rendering names. If the scan root contains
`unresolved/`, applying the rename also moves resolved records from that
directory into the scan root; unresolved files are left in `unresolved/`.
Pass `--output-unresolved unresolved` to route unresolved PDFs during this
identify step; relative paths are resolved below the scan root.

Author format options:

| Value | Example output |
| --- | --- |
| `surname` | `Smith` |
| `surname_initials` (default) | `SmithJ` |
| `last_comma_first` | `Smith,_J.` |
| `ads_custom:FORMAT` | delegates to ADS export API |

## 5) Watch a folder for new PDFs

Polling (default):

```bash
adsfolio pdf watch ~/Papers --recursive --interval 10 --apply
```

Watchdog backend (requires `adsfolio[full]`):

```bash
adsfolio pdf watch ~/Papers --recursive --use-watchdog --interval 60 \
  --output-unresolved unresolved --apply
```

The event callback only queues paths; one worker performs PDF parsing, ADS
queries, renames, and catalog writes. `--interval` controls a periodic
filesystem-facts reconciliation that catches missed events without hashing or
re-identifying unchanged PDFs. Incomplete, encrypted, or concurrently changing
PDFs are deferred until a later event or reconciliation. Watch-mode unresolved
destinations must be inside the watched root.

Filename rendering preserves Unicode by default, for example `Wesołowski`.
Use `--char-policy ascii` to request `Wesolowski`. Without `--apply`, watch mode
reports proposed path changes but does not rename or move files.

The author separator and truncation suffix are independent settings.
`--max-authors` is inclusive: one or two authors are fully shown, while three
or more are truncated. `--authors-when-truncated` controls how many leading
authors remain in a truncated result and defaults to one. For example,
`--max-authors 2 --authors-when-truncated 1 --author-separator "+"
--author-truncation-suffix "+"` renders three authors as `SmithJ+`;
setting `--authors-when-truncated 2` renders `SmithJ+DoeA+` instead.
`{authors_N}` uses the configured retained authors and separator but never adds
the truncation suffix. The Rename dialog persists these values globally or per
library.
Use **Save Defaults** there to update the standard without renaming existing
PDFs.

### Run continuously on macOS

Create `~/Library/LaunchAgents/org.example.adsfolio-pdf-watch.plist`, replacing
every `/Users/YOU/...` path with an absolute path valid for your account:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>org.example.adsfolio-pdf-watch</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/YOU/.venvs/ysbpy/bin/adsfolio</string>
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

## 6) Check current state

```bash
adsfolio pdf status ~/Papers --recursive
```

Shows identified catalog locations, unresolved files on disk, and duplicate hash
groups. With `--recursive`, all locations share the same central catalog. Do not
run identify/watch from two machines against the same library concurrently; use
the export command for portable backups.

## 7) Upgrade arXiv PDFs to published versions

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

## 8) Auto-upgrade while watching

```bash
adsfolio pdf watch ~/Papers --recursive --apply \
  --auto-upgrade --upgrade-interval 24
```

## 9) Push resolved bibcodes to an ADS library

Dry-run:

```bash
adsfolio pdf push-library ~/Papers <library_id>
```

Apply:

```bash
adsfolio pdf push-library ~/Papers <library_id> --apply
```

## 10) Generate Obsidian-friendly notes

If the PDFs already live inside an Obsidian vault, `pdf obsidian` writes one Markdown note per
tracked PDF (YAML frontmatter with title/authors/year/journal/bibcode, plus an `## Abstract`
section) next to each file. Notes link back to the PDF with an absolute `file://` link — PDFs are
never copied, moved, or embedded.

Dry-run (no ADS queries, no files written):

```bash
adsfolio pdf obsidian ~/Papers
```

Apply — fetches any missing abstracts from ADS in one batched query, caches them into the
registry/sidecar, and writes the notes:

```bash
adsfolio pdf obsidian ~/Papers --apply
```

Existing notes are left untouched on re-runs (so manual edits in Obsidian are preserved) unless you
pass `--force`:

```bash
adsfolio pdf obsidian ~/Papers --apply --force
```

Write notes to a separate folder instead of colocating them with the PDFs:

```bash
adsfolio pdf obsidian ~/Papers --apply --notes-dir ~/Papers/notes
```

## Next

Continue with [Tutorial 5: Operations and Troubleshooting](./05-operations-and-troubleshooting.md).
