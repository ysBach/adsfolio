# Tutorial 5: Operations and Troubleshooting

This tutorial summarizes daily usage patterns and common CLI issues.

## 0) Start with `--help`

Run these first whenever behavior is unclear or you need exact flag names:

~~~bash
adsapi --help
adsapi set --help
adsapi search --help
adsapi export --help
adsapi library --help
adsapi pdf --help
adsapi pdf upgrade --help
adsapi cv --help
~~~

High-value options to check first:

| Command | Option | What it clarifies |
| --- | --- | --- |
| `adsapi search --help` | `--fields`, `--rows`, `--sort`, `--fq`, `--json` | Query scope and output format |
| `adsapi export --help` | `--format`, `--custom`, `--journal`, `--output` | Export style and journal naming behavior |
| `adsapi library watch --help` | `--num-iter`, `--interval`, `--info-interval`, `--additional` | Polling behavior and output composition |
| `adsapi pdf ocr --help` | `--recursive`, `--output`, `--make-ocr-sh`, `--apply`, `-v` | Optional non-OCR routing before identify |
| `adsapi pdf rename --help` | `--scheme`, `--author-format`, `--max-authors`, `--allow-ads`, `--output-unresolved`, `--apply` | Filename rendering, optional ADS identify, and unresolved routing |
| `adsapi pdf identify --help` | `--recursive`, `--output-unresolved`, `--ai-csv`, `-v`, `--verbose-parser` | Identification scope, unresolved routing, AI roundtrip input, parser diagnostics |
| `adsapi cache --help` | `status`, `refresh`, `clear` | Publication-cache management |
| `adsapi pdf watch --help` | `--use-watchdog`, `--auto-upgrade`, `--upgrade-interval` | Real-time ingestion and background upgrade cadence |
| `adsapi pdf upgrade --help` | `--overwrite-arxiv-with-official`, `--arxiv-backup-if-overwritten`, `--skip-annotation-port`, `--force` | Upgrade safety and annotation transfer behavior |
| `adsapi cv generate --help` | `--config`, `--format`, `--style`, `--author-format`, `--dry-run` | CV output rendering options |

## 1) Configuration commands

```bash
adsapi config show
adsapi rate-limit
adsapi set
```

Set or rotate token:

```bash
adsapi config set-token <new-token>
```

Refresh identify profile (ORCID/names/year window) and publication cache:

```bash
adsapi set
```

`adsapi set` is usually one-time setup. Re-run only when token/profile values change.

## 2) Daily command map

| Task | Command |
| --- | --- |
| Find papers | `adsapi search "query"` |
| Export selected bibcodes | `adsapi export <bibcode...> --output refs.bib` |
| Inspect libraries | `adsapi library list` |
| Keep a library bib up to date | `adsapi library watch <library_id> -n 0` |
| Route likely non-OCR PDFs (optional) | `adsapi pdf ocr <dir> --recursive -o ocr_required` |
| Identify PDFs on disk | `adsapi pdf identify <dir> --recursive` |
| Inspect publication cache | `adsapi cache status` |
| Export folder registry | `adsapi pdf export <dir> -o pdfs-backup.json` |
| Preview PDF rename plan | `adsapi pdf rename <dir>` |
| Apply PDF rename plan | `adsapi pdf rename <dir> --apply` |
| Watch folder for new PDFs | `adsapi pdf watch <dir> --recursive --apply` |
| Upgrade arXiv PDFs | `adsapi pdf upgrade <dir> --apply` |
| Generate CV output | `adsapi cv generate --config cv.yaml` |

## 3) Common issues

### Authentication errors

- Symptom: command fails with auth error.
- Check: `adsapi config show`.
- Fix: set token again with `adsapi config set-token` or export `ADS_API_TOKEN`.

### Watchdog unavailable

- Symptom: `--use-watchdog` fails.
- Fix: install optional extras: `pip install adsapi[full]`.

### ISO4 journal conversion unavailable

- Symptom: `--journal iso4` warns and returns full names.
- Fix: install optional extras: `pip install adsapi[full]`.

### PDF rename changed nothing

- Symptom: `adsapi pdf rename` outputs "No files to rename."
- Check: run `adsapi pdf identify <dir>` first to populate the registry.

### `pdf identify` does not use the cache

- Symptom: identify is slow even though you ran it recently.
- Cause: PDFs whose SHA-256 is already in the SQLite registry are skipped automatically;
  publication matching also uses the user cache and folder ADS-document cache.
  If all 1000+ files appear as "pending", the registry is either missing, in the
  wrong directory, or the files have been moved/renamed since last run.
- Fix:
  - run `adsapi pdf status <dir> --recursive` to confirm registry record counts
  - check that `.adsapi-pdfs.sqlite` exists at the scan root directory
  - run `adsapi cache status` and `adsapi cache refresh` if the publication cache is missing
  - if files were renamed externally, the SHA match will still find them; if the
    registry is missing entirely, re-run identify once to rebuild it

### `pdf identify` warns about large query batch

- Symptom: command asks to confirm ADS fallback queries for many unmatched files.
- Cause: cache/profile matching left many files unresolved.
- Fix:
  - use `adsapi set` and confirm ORCID/year window
  - run `adsapi pdf ocr` first if many files are scanned/non-OCR
  - run identify and only approve fallback when needed
  - use unresolved AI roundtrip (`unresolved.csv` -> AI CSV -> `--ai-csv`)
  - rerun `adsapi pdf identify <dir> --recursive --allow-ads` after improving OCR or supplying AI hints
  - run `adsapi pdf status <dir>` to confirm identified records

### CV init fails with PyYAML error

- Symptom: `adsapi cv init` exits with a PyYAML import error.
- Fix: `pip install pyyaml` or `pip install adsapi[full]`.

### CV generate produces wrong author name

- Check `author.me_target` in `cv.yaml` — it must match the ADS author string exactly
  (case-insensitive prefix match is used).
- Use `--dry-run` to inspect output before writing.

## 4) Safe execution pattern

For destructive workflows, use this sequence:

1. run dry-run command first
2. inspect output
3. rerun with `--apply`

This is especially important for:

- `adsapi pdf rename`
- `adsapi pdf identify --output-unresolved`
- `adsapi pdf push-library`
- `adsapi pdf upgrade`
