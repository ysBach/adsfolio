# Tutorial 3: Library workflows

This tutorial covers `adsfolio library` commands for listing, reading, and syncing a library BibTeX file.

## 0) Start with `--help`

First, inspect the library command group and its subcommands:

~~~bash
adsfolio library --help
adsfolio library list --help
adsfolio library get --help
adsfolio library watch --help
~~~

Key library options:

| Option | Meaning | When to use |
| --- | --- | --- |
| `library list --rows` | Number of libraries to fetch | Accounts with many libraries |
| `library list --json` | JSON output for list result | Scripted selection by id/name |
| `library get --raw` | Return bibcodes exactly as stored | Debugging library state or ADS normalization differences |
| `library get --bibtex --output` | Export one library directly to a `.bib` file | One-shot sync for LaTeX projects |
| `library watch --num-iter` | Polling iteration count (`0` = infinite) | Long-running background sync |
| `library watch --interval` | Seconds between checks | Tune API usage vs freshness |
| `library watch --info-interval` | Status-print cadence | Reduce noisy logs in long sessions |
| `library watch --additional` | Append local BibTeX file to watched export | Merge ADS refs + local/manual entries |

## 1) List your libraries

```bash
adsfolio library list
```

For JSON output:

```bash
adsfolio library list --rows 50 --json
```

## 2) Inspect one library

```bash
adsfolio library get <library_id>
```

To return the raw bibcodes exactly as stored:

```bash
adsfolio library get <library_id> --raw
```

## 3) Export library as BibTeX

```bash
adsfolio library get <library_id> --bibtex --output references.bib
```

## 4) Watch library and auto-refresh BibTeX

```bash
adsfolio library watch <library_id> \
  --output references.bib \
  --num-iter 0 \
  --interval 10
```

Notes:

- `--num-iter 0` means run indefinitely.
- To stop the watch, press `Ctrl+C`.
- The watcher writes the output file when it starts and again each time the
  library changes.
- Point `--output` at a folder that your LaTeX project reads, for example a
  folder that you sync with Overleaf.

## 5) Add style and extra BibTeX content during watch

```bash
adsfolio library watch <library_id> \
  --output references.bib \
  --journal full \
  --additional local_notes.bib \
  --num-iter 0
```

## 6) Tune status logs

```bash
adsfolio library watch <library_id> \
  --info-interval 10 \
  --interval 5 \
  --num-iter 200
```

## Next

Continue with [Tutorial 4: PDF workflows](./04-pdf-workflows.md).
