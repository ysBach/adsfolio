# Tutorial 1: Setup and first search

This tutorial takes you from install to your first useful `adsfolio search` commands.

## 0) Start with `--help`

Before you run the examples, run these commands to see the full command interface:

~~~bash
adsfolio --help
adsfolio set --help
adsfolio search --help
adsfolio config --help
adsfolio config set-token --help
adsfolio config show --help
adsfolio rate-limit --help
~~~

Key search options:

| Option | Meaning | When to use |
| --- | --- | --- |
| `--fields` / `-f` | Comma-separated response fields (e.g. `bibcode,title,author,year`) | Reduce output to what your script or workflow needs |
| `--rows` / `-n` | Number of returned records (max 2000) | Increase for broader scans, lower for quick checks |
| `--sort` / `-s` | Sort expression like `"citation_count desc"` | Prioritize highly cited or most recent results |
| `--fq` | Extra filter query | Narrow by database, property, etc. without changing main query |
| `--json` | Print machine-readable JSON output | Pipe to tools (`jq`, Python, shell scripts) |
| `adsfolio --token` | Global option. Put it before the subcommand (`adsfolio --token T search ...`). Not accepted after `search` | Temporary token switching without changing saved config |

## 1) Install

You need Python 3.10 or newer.

```bash
pip install adsfolio
```

To also install all optional features (watchdog, annotation porting, ISO4), run:

```bash
pip install 'adsfolio[full]'
```

## 2) Run first-time setup

Get your ADS token from [SciX settings](https://scixplorer.org/user/settings/token).

Run the interactive setup. You usually do this only once, unless values change:

```bash
adsfolio set
```

The setup stores these account and author values:

- ADS token
- ORCID (recommended)
- optional full-name query terms (repeat `--query-name` flag or enter semicolon-separated names at the prompt)

The setup also stores these prefetch values:

- debut/end year window for prefetch (`retire_year` remains readable from legacy setup files)
- optional ADS library IDs for prefetch bootstrap
- optional expansion to references/citations of those library papers

During `adsfolio pdf identify` runs, adsfolio uses these fields to reduce ADS requests.
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

Alternatively, use an environment variable or the token-only config:

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

To run a command with a different token:

```bash
adsfolio --token "<other-token>" search "author:Hubble" --rows 5
```

## 8) Check rate limits

```bash
adsfolio rate-limit
```

This command shows the current limit, remaining requests, and reset time from the last API response.

## Next

Continue with [Tutorial 2: Export and BibTeX](./02-export-and-bibtex.md).
