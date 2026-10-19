# Tutorial 5: Operations and Troubleshooting

This tutorial summarizes daily usage patterns and common CLI issues.

## 0) Start with `--help`

Run these first whenever behavior is unclear or you need exact flag names:

~~~bash
adsfolio --help
adsfolio set --help
adsfolio search --help
adsfolio export --help
adsfolio library --help
adsfolio pdf --help
adsfolio pdf upgrade --help
adsfolio cv --help
~~~

High-value options to check first:

| Command | Option | What it clarifies |
| --- | --- | --- |
| `adsfolio search --help` | `--fields`, `--rows`, `--sort`, `--fq`, `--json` | Query scope and output format |
| `adsfolio export --help` | `--format`, `--custom`, `--journal`, `--output` | Export style and journal naming behavior |
| `adsfolio library watch --help` | `--num-iter`, `--interval`, `--info-interval`, `--additional` | Polling behavior and output composition |
| `adsfolio pdf ocr --help` | `--recursive`, `--output`, `--make-ocr-sh`, `--apply`, `-v` | Optional non-OCR routing before identify |
| `adsfolio pdf rename --help` | `--scheme`, `--author-format`, `--max-authors`, `--allow-ads`, `--output-unresolved`, `--apply` | Filename rendering, optional ADS identify, and unresolved routing |
| `adsfolio pdf identify --help` | `--recursive`, `--output-unresolved`, `--ai-csv`, `-v`, `--verbose-parser` | Identification scope, unresolved routing, AI roundtrip input, parser diagnostics |
| `adsfolio cache --help` | `status`, `refresh`, `clear` | Publication-cache management |
| `adsfolio pdf watch --help` | `--use-watchdog`, `--auto-upgrade`, `--upgrade-interval` | Real-time ingestion and background upgrade cadence |
| `adsfolio pdf upgrade --help` | `--overwrite-arxiv-with-official`, `--arxiv-backup-if-overwritten`, `--skip-annotation-port`, `--force` | Upgrade safety and annotation transfer behavior |
| `adsfolio cv generate --help` | `--config`, `--format`, `--style`, `--author-format`, `--dry-run` | CV output rendering options |

## 1) Configuration commands

```bash
adsfolio config show
adsfolio rate-limit
adsfolio set
```

Set or rotate token:

```bash
adsfolio config set-token <new-token>
```

Refresh identify profile (ORCID/names/year window) and publication cache:

```bash
adsfolio set
```

`adsfolio set` is usually one-time setup. Re-run only when token/profile values change.

## 2) Daily command map

| Task | Command |
| --- | --- |
| Find papers | `adsfolio search "query"` |
| Export selected bibcodes | `adsfolio export <bibcode...> --output refs.bib` |
| Inspect libraries | `adsfolio library list` |
| Keep a library bib up to date | `adsfolio library watch <library_id> -n 0` |
| Route likely non-OCR PDFs (optional) | `adsfolio pdf ocr <dir> --recursive -o ocr_required` |
| Identify PDFs on disk | `adsfolio pdf identify <dir> --recursive` |
| Inspect publication cache | `adsfolio cache status` |
| Export a library's catalog records | `adsfolio pdf export <dir> -o pdfs-backup.json` |
| Preview PDF rename plan | `adsfolio pdf rename <dir>` |
| Apply PDF rename plan | `adsfolio pdf rename <dir> --apply` |
| Watch folder for new PDFs | `adsfolio pdf watch <dir> --recursive --apply` |
| Upgrade arXiv PDFs | `adsfolio pdf upgrade <dir> --apply` |
| Generate CV output | `adsfolio cv generate --config cv.yaml` |

## 3) Common issues

### Authentication errors

- Symptom: command fails with auth error.
- Check: `adsfolio config show`.
- Fix: set token again with `adsfolio config set-token` or export `ADS_API_TOKEN`.

### Watchdog unavailable

- Symptom: `--use-watchdog` fails.
- Fix: install optional extras: `pip install 'adsfolio[full]'`.

### ISO4 journal conversion unavailable

- Symptom: `--journal iso4` warns and returns full names.
- Fix: install optional extras: `pip install 'adsfolio[full]'`.

### PDF rename changed nothing

- Symptom: `adsfolio pdf rename` outputs "No files to rename."
- Check: run `adsfolio pdf identify <dir> --recursive` first to populate the catalog.

### `pdf identify` does not use the cache

- Symptom: identify is slow even though you ran it recently.
- Cause: resolved PDFs with unchanged content reuse catalog metadata. Unresolved
  or changed PDFs still need identification. Matching also uses the publication
  cache and the library's cached ADS documents; these are separate from PDF
  resolution status.
- Fix:
  - run `adsfolio pdf status <dir> --recursive` to inspect the library's records
  - check the central `pdf-library.sqlite` under `Config.get_config_dir()`
    (`~/Library/Application Support/adsfolio` on macOS); new libraries do not create
    `.adsfolio-pdfs.sqlite` at the scan root
  - run `adsfolio cache status` and `adsfolio cache refresh` if the publication cache is missing
  - rerun `adsfolio pdf identify <dir> --recursive` to reconcile externally renamed
    files by content hash and identify pending files

### Identify cannot verify a first-page title

Identify collects heading candidates from first-page typography instead of
sending the entire page as a title. It uses transformed font sizes and excludes
fontless form aggregates that duplicate the page text. It retains sentence
candidates when a heading shares a line with its authors.

ADS must return a unique matching title after case and punctuation normalization.
Conflicting matches leave the file unresolved. Queries share a cache and are
batched. Oversized batches split into smaller queries rather than dropping the
file. A single oversized candidate is still reported with its file path.

Typography is a heuristic: uniform-font OCR, titles split across font sizes,
and poor text layers can still fail. The text-boundary fallback recognizes
parenthesized submission dates but cannot reliably separate every title from
its authors. Metadata DOI and metadata title strategies run before first-page
titles in the default order.

To correct a remaining unresolved file, select it and use **Update from ADS…**
with a verified DOI or Bibcode. From the CLI:

```bash
adsfolio pdf refresh-ads <dir> --identifier 10.1051/0004-6361/201834764 \
  --relative-path <path-of-pdf-inside-dir>
```

Check the identifier against the paper. The eyepiece article by F. E. Wright
uses DOI `10.1364/JOSA.24.000206` and Bibcode `1934JOSA...24..206W`.

### PDFs exist in subdirectories but are absent from the GUI

The GUI displays catalog records. A new root discovers only top-level PDFs by
default. **Refresh/F5** scans the selected root using its current recursion setting.

1. Right-click the root under **LIBRARIES** and turn off **Watch Current Library**
   if checked.
2. Reopen that context menu and enable **Include Subdirectories**.
3. Press **Refresh/F5**, select **All**, and clear the search field.

The recursive scan restores nested locations marked missing and discovers new
PDFs. Top-level scans leave nested records unchanged. **Unresolved** is
a status filter, not a listing of the `unresolved/` directory. See the
[GUI guide](../gui.md) for the distinction between root scope and Identify settings.

### `pdf identify` warns about large query batch

- Symptom: command asks to confirm ADS fallback queries for many unmatched files.
- Cause: cache/profile matching left many files unresolved.
- Fix:
  - use `adsfolio set` and confirm ORCID/year window
  - run `adsfolio pdf ocr` first if many files are scanned/non-OCR
  - run identify and only approve fallback when needed
  - use unresolved AI roundtrip (`unresolved.csv` -> AI CSV -> `--ai-csv`)
  - rerun `adsfolio pdf identify <dir> --recursive --allow-ads` after improving OCR or supplying AI hints
  - run `adsfolio pdf status <dir>` to confirm identified records

### CV init fails with PyYAML error

- Symptom: `adsfolio cv init` exits with a PyYAML import error.
- Fix: reinstall the package in the environment running the command. PyYAML is
  a required dependency of the base package; the `full` extra is unnecessary.

### CV generate produces wrong author name

- Check `author.me_target` in `cv.yaml`: it uses case-insensitive prefix matching
  against the ADS author string.
- Use `--dry-run` to inspect output before writing.

## 4) Safe execution pattern

For destructive workflows, use this sequence:

1. run dry-run command first
2. inspect output
3. rerun with `--apply`

This is especially important for:

- `adsfolio pdf rename`
- `adsfolio pdf identify --output-unresolved`
- `adsfolio pdf push-library`
- `adsfolio pdf upgrade`
