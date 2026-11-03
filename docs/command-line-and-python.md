# CLI and Python workflows

Use the [App GUI guide](gui.md) for interactive library management.
This page covers scripts and the Python client.

Learning path:

1. Install and configure ADS access on this page.
2. Follow the [tutorials](#tutorials) in order: setup and search,
   export and BibTeX, ADS libraries, PDFs, operations, CVs, and a Python script.
3. Look up details in the [command-line reference](cli.rst) and the
   [Python API](api/index.rst).

## Install

ADSfolio requires Python 3.10 or newer. Install the current PyPI release:

```sh
pip install adsfolio            # CLI and Python client
pip install "adsfolio[full]"    # adds PyMuPDF text extraction and folder watching
pip install "adsfolio[full,gui]"  # adds the desktop application
```

`uv tool install adsfolio` or `pipx install adsfolio` installs the CLI in its own
environment.

### CLI and API without the desktop app

The base package includes the CLI and Python API. It does not install Qt,
PyMuPDF, Watchdog, or the native desktop bundle. It still includes basic PDF
and citation dependencies. Python remains necessary for this installation.

For an AI agent or script, use JSON output:

```sh
adsfolio search 'author:Einstein' --fields bibcode,title,year --rows 5 --json
```

Without a persistent CLI installation, use:

```sh
uvx --from adsfolio adsfolio search 'author:Einstein' --rows 5 --json
```

ADS queries require an ADS token. Provide `ADS_API_TOKEN` through your environment
or secret manager. Do not place tokens in agent instructions or command arguments.
For repeatable environments, pin the package to your chosen release.

Download the [CLI/API agent skill](https://github.com/ysBach/adsfolio/releases/latest/download/adsfolio-cli-api-SKILL.md).
Save it as `adsfolio-cli-api/SKILL.md` in your agent's skills directory.
The skill covers queries, JSON output, Python access, and mutation boundaries.

### From source

```sh
git clone https://github.com/ysBach/adsfolio.git
cd adsfolio
uv sync --all-extras
uv run adsfolio --help
uv run adsfolio-gui      # the desktop app from this checkout
```

`uv sync` creates the project's `.venv`. Do not synchronize a shared environment.
Prefix the commands below with `uv run` when using this checkout.
Homebrew installations already provide the `adsfolio` command.

## Configure ADS access

ADSfolio uses the official [NASA ADS API](https://github.com/adsabs/adsabs-dev-api)
for searches, citation exports, metrics, ADS libraries, and journal information.
The linked ADS repository provides API documentation and examples.
ADSfolio is an independent project, not an official ADS application.

Get your token from [ADS settings](https://ui.adsabs.harvard.edu/user/settings/token).
Use the desktop token dialog, or run interactive setup:

```sh
adsfolio set
```

Setup saves your token and publication profile. It also builds the publication cache.
You can supply `ADS_API_TOKEN` through your environment instead.
Local references and DOI lookups do not require an ADS token.

## Search and export

```sh
adsfolio search "author:Einstein" --fields bibcode,title --rows 5
adsfolio export 2020ApJ...900..100S -f bibtex
adsfolio library list
adsfolio library get LIBRARY_ID --bibtex
```

Replace `LIBRARY_ID` with your ADS library identifier.
See [search](cli-tutorials/01-setup-and-search.md),
[export](cli-tutorials/02-export-and-bibtex.md), and
[remote libraries](cli-tutorials/03-library-workflows.md).

## Identify and rename local PDFs

```sh
adsfolio cache status
adsfolio pdf identify ~/Papers --recursive
adsfolio pdf rename ~/Papers
adsfolio pdf rename ~/Papers --apply
```

Identify records publication matches. Rename previews changes unless you supply
`--apply`. Inspect the preview before applying it.
Both commands check each match against the arXiv stamp and DOIs that the PDF
states about itself. They skip PDFs whose identifiers contradict the record or
cannot confirm it. See [PDF identity checks](gui.md#pdf-identity-checks).
ADSfolio recognizes supported display markup in provider titles during the
title comparison. The record must still pass the other identity checks.

To retry verified external metadata without an ADS bibcode, enable ADS and
allow ADS requests. This command selects ADS for one run:

```sh
adsfolio pdf identify ~/Papers --provider ads --allow-ads
```

A verified ADS match can add its bibcode. ADSfolio may also use a cached ADS
record without network permission. If no ADS match passes the identity check,
it keeps the existing metadata. Check the output before retrying.

`pdf rename` and `pdf watch` load saved global Rename settings by default. Add
`--settings-scope library` to use the library's saved Rename settings. If the
library has no saved settings, ADSfolio uses the global settings. Explicit
Rename flags, such as `--scheme`, override the named settings for that run.

If a template field lacks metadata, Rename skips the file and names the missing
fields in its output. Add the metadata or choose a different `--scheme`.
Then retry the rename.

See [PDF workflows](cli-tutorials/04-pdf-workflows.md) for provider order,
filename templates, OCR preparation, watching, and annotation transfer.

The catalog stores file locations, metadata, and history outside your PDF folders.
Use `adsfolio pdf export DIR -o snapshot.json` for a readable registry backup.
Keep a database backup when testing migrations or changing library tools.

## Watch, upgrade, and export notes

```sh
adsfolio pdf watch ~/Papers --recursive --apply
adsfolio pdf upgrade ~/Papers
adsfolio pdf obsidian ~/Papers
adsfolio pdf status ~/Papers --recursive
```

Watch with `--apply` can identify and rename arriving PDFs that pass the
identity check. A unique exact ADS bibcode in a filename can associate a PDF
without independently verifying its contents. Review saved policies before
starting Watch on an existing collection.
For a proven incremental annotation save, Watch keeps the saved identification.
Other changes still need an identity check.
Watch also rechecks existing records with enabled ADS at startup and every 24
hours. Set `--upgrade-interval` to change the interval. A verified new match
or bibcode updates metadata and uses the saved Rename settings with `--apply`.
Without `--apply`, it previews those changes. A failed identity check or
filename collision keeps the files in place. Resolve the reported issue and
retry.
If the template requires missing metadata, Watch keeps the filename and reports
the missing fields. Add the metadata or choose another template before retrying.
Upgrade and Obsidian commands preview their work by default.
Read their `--help` output before applying changes.

See [operations and troubleshooting](cli-tutorials/05-operations-and-troubleshooting.md)
and [CV workflows](cli-tutorials/06-cv-workflows.md) for further examples.

## Python client

```python
from adsfolio import ADSClient

client = ADSClient()
try:
    results = client.search.query(
        "author:Einstein", fl=["bibcode", "title"], rows=5
    )
    bibtex = client.export.export(["2020ApJ...900..100S"], format="bibtex")
    libraries = client.libraries.list_libraries()
finally:
    client.close()
```

See the [API reference](api/index.rst) for public modules and interfaces.
The [query-to-document example](query-to-document-example.md) covers a complete
bibliography workflow.

## Tutorials

Follow the tutorials in order. Each one builds on the previous one.

ADS search and libraries:

1. [Setup and first search](cli-tutorials/01-setup-and-search.md)
2. [Export and BibTeX](cli-tutorials/02-export-and-bibtex.md)
3. [Library workflows](cli-tutorials/03-library-workflows.md)

PDFs, operations, and documents:

4. [PDF workflows](cli-tutorials/04-pdf-workflows.md)
5. [Operations and troubleshooting](cli-tutorials/05-operations-and-troubleshooting.md)
6. [CV workflows](cli-tutorials/06-cv-workflows.md)
7. [From an ADS query to a document with Python](query-to-document-example.md)

For a PDF without a readable own identifier, review the PDF and use
`pdf refresh-ads ROOT --identifier ID --relative-path FILE --confirm-identity`.
This confirms one file. It cannot override a conflicting identifier or an own
identifier that the provider does not corroborate.
