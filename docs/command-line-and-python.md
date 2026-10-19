# CLI and Python workflows

Use the [application guide](gui.md) for interactive library management.
This page covers scripts and the Python client.

## Install from source

Python 3.10 or newer is required. PyPI publication is not enabled for 0.1.0.

```sh
git clone https://github.com/ysBach/adsfolio.git
cd adsfolio
uv sync --all-extras
uv run adsfolio --help
```

`uv sync` creates the project's `.venv`. Do not synchronize a shared environment.
Prefix the commands below with `uv run` when using this checkout.
Homebrew installations already provide the `adsfolio` command.

## Configure ADS access

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
adsfolio search "author:Einstein" --fl bibcode,title --rows 5
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

Watch with `--apply` can identify and rename arriving PDFs. Review its saved
policies before starting it on an existing collection.
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
