# Getting Started

## Installation

Requires Python 3.10 or newer.

```bash
pip install adsapi
```

Install all optional features:

```bash
pip install adsapi[full]
```

`adsapi[full]` includes:

- `watchdog` for filesystem watch mode
- `pymupdf` for annotation porting during PDF upgrade
- `iso4` for journal abbreviation conversion

## Configure API token

1. Create an account at [SciX / ADS](https://scixplorer.org).
2. Get your token from [settings](https://scixplorer.org/user/settings/token).
3. Run interactive setup (recommended):

```bash
adsapi set
```

`adsapi set` is usually needed only once. It saves the profile and immediately
builds the publication cache. Re-run it when your token or identify profile changes.

## PDF setup, cache, and state

Run the PDF workflow in this order:

```bash
adsapi set
adsapi cache status
adsapi pdf identify ~/Papers --recursive
adsapi pdf rename ~/Papers --apply
```

`adsapi cache refresh` re-downloads the user publication universe; `adsapi cache clear`
removes it. Folder state is incremental SQLite at `~/Papers/.adsapi-pdfs.sqlite`.
Use `adsapi pdf export ~/Papers -o pdfs-backup.json` for a human-readable backup.
Legacy `.adsapi-pdfs.json` files are imported once and renamed with a `.migrated`
suffix. Do not edit or watch the same synced folder from two machines concurrently:
Dropbox/OneDrive can create a conflicted copy of the SQLite file.

Or set the token directly:

```bash
adsapi config set-token <your-token>
```

Or via environment variable:

```bash
export ADS_API_TOKEN=<your-token>
```

## Quick CLI examples

```bash
adsapi search "author:Einstein" --fields bibcode,title --rows 5
adsapi export 2024A&A...684A..80B 2021A&A...654A.113B -f bibtex
adsapi library list
adsapi pdf ocr ~/Papers --recursive -o ~/Papers/ocr_required
adsapi pdf identify ~/Papers --recursive
adsapi cv init
adsapi cv generate --config cv.yaml --dry-run
```

## Python example

```python
from adsapi import ADSClient

client = ADSClient()
results = client.search.query("author:Einstein", fl=["bibcode", "title"], rows=5)
print(results.num_found)
```

## Continue

For step-by-step command-line walkthroughs, see [CLI Tutorials](./cli-tutorials/index.md).
