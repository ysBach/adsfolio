# Tutorial 1: Setup and First Search

This tutorial gets you from install to your first useful `adsfolio search` commands.

## 0) Start with `--help`

Run these first to view the full command interface before executing examples:

~~~bash
adsfolio --help
adsfolio set --help
adsfolio search --help
adsfolio config --help
adsfolio config set-token --help
adsfolio config show --help
adsfolio rate-limit --help
~~~

Key search options to understand:

| Option | Meaning | When to use |
| --- | --- | --- |
| `--fields` / `-f` | Comma-separated response fields (e.g. `bibcode,title,author,year`) | Reduce output to what your script or workflow needs |
| `--rows` / `-n` | Number of returned records (max 2000) | Increase for broader scans, lower for quick checks |
| `--sort` / `-s` | Sort expression like `"citation_count desc"` | Prioritize highly cited or most recent results |
| `--fq` | Extra filter query | Narrow by database, property, etc. without changing main query |
| `--json` | Print machine-readable JSON output | Pipe to tools (`jq`, Python, shell scripts) |
| `--token` | Override token only for this command | Temporary token switching without changing saved config |

## 1) Install

Requires Python 3.10 or newer.

```bash
pip install adsfolio
```

If you also want all optional features (watchdog, annotation porting, ISO4):

```bash
pip install adsfolio[full]
```

## 2) Run first-time setup

Get your ADS token from [SciX settings](https://scixplorer.org/user/settings/token).

Run interactive setup once (you usually do this only once unless values change):

```bash
adsfolio set
```

This stores:

- ADS token
- ORCID (recommended)
- optional full-name query terms (repeat `--query-name` flag or enter semicolon-separated names at the prompt)
- debut/end year window for prefetch (`retire_year` remains readable from legacy setup files)
- optional ADS library IDs for prefetch bootstrap
- optional expansion to references/citations of those library papers

These fields are used to reduce ADS requests during `adsfolio pdf identify` runs.
They do not limit normal search/export behavior.

You can pass values non-interactively:

```bash
adsfolio set \
  --token <your-token> \
  --orcid 0000-0002-1825-0097 \
  --query-name "Bach, Yoonsoo P." \
  --debut-year 2010 --end-year 2026 \
  --library-id ABC123XYZ
```

Or use environment variable / token-only config:

```bash
export ADS_API_TOKEN=<your-token>
adsfolio config set-token <your-token>
adsfolio config show
```

## 3) First query

```bash
adsfolio search "author:Einstein"
```

This prints a compact list with year, bibcode, and title snippet.

## 4) Control search fields and size

```bash
adsfolio search "gravitational waves" \
  --fields "bibcode,title,author,year,citation_count" \
  --rows 20
```

## 5) Sort and filter

```bash
adsfolio search "black holes" \
  --sort "citation_count desc" \
  --fq "database:astronomy" \
  --rows 10
```

## 6) JSON output for scripting

```bash
adsfolio search "author:Einstein year:1905" --json
```

## 7) Override token per command

If you need to run a command with a different token:

```bash
adsfolio --token "<other-token>" search "author:Hubble" --rows 5
```

## 8) Check rate limits

```bash
adsfolio rate-limit
```

Shows current limit, remaining requests, and reset time from the last API response.

## Next

Continue with [Tutorial 2: Export and BibTeX](./02-export-and-bibtex.md).
