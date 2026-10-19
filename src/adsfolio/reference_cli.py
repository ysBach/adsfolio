"""Import external references and export local bibliographies from the CLI."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TextIO

import click

from .pdf.catalog import PdfCatalog
from .references import LocalReferences, parse_bibtex


@contextmanager
def _open_library(ctx: click.Context) -> Iterator[LocalReferences]:
    """Open the selected catalog and report recoverable errors as CLI failures."""
    catalog = None
    try:
        catalog = PdfCatalog(ctx.obj.get("reference_catalog"))
        yield LocalReferences(catalog)
    except (ValueError, OSError, sqlite3.Error) as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        if catalog is not None:
            catalog.close()


def _parse_fields(values: tuple[str, ...]) -> dict[str, str]:
    """Parse explicit field assignments without discarding duplicate names."""
    fields: dict[str, str] = {}
    for value in values:
        name, separator, content = value.partition("=")
        name = name.strip().lower()
        if not separator or not name:
            raise click.BadParameter("Use NAME=VALUE for each --field.")
        if name in fields:
            raise click.BadParameter(f"Field {name!r} was supplied more than once.")
        fields[name] = content
    return fields


@click.group("reference")
@click.option(
    "--catalog",
    "catalog_path",
    type=click.Path(path_type=Path, dir_okay=False),
    help="Use a separate catalog instead of the shared local library.",
)
@click.pass_context
def reference_group(ctx: click.Context, catalog_path: Path | None) -> None:
    """Keep external references alongside ADS records; no token is required."""
    ctx.ensure_object(dict)
    ctx.obj["reference_catalog"] = catalog_path


@reference_group.command("import")
@click.argument("input_file", type=click.File("r", encoding="utf-8"), default="-")
@click.option(
    "--preview", is_flag=True, help="Show parsed entries without saving references."
)
@click.option(
    "--source", default="bibtex", show_default=True, help="Import provenance label."
)
@click.pass_context
def import_references(
    ctx: click.Context, input_file: TextIO, source: str, preview: bool
) -> None:
    """Import a .bib file, or paste BibTeX through standard input (-)."""
    with _open_library(ctx) as library:
        text = input_file.read()
        if preview:
            entries = [
                {key: record[key] for key in ("cite_key", "entry_type", "fields")}
                for record in parse_bibtex(text)
            ]
            click.echo(json.dumps(entries, ensure_ascii=False, indent=2))
            return
        records = library.import_bibtex(text, source=source)
        for record in records:
            click.echo(f"{record['id']}\t{record['cite_key']}")
        click.echo(f"Imported {len(records)} reference(s).", err=True)


@reference_group.command("add")
@click.option("--key", "cite_key", required=True, help="Stable LaTeX citation key.")
@click.option("--type", "entry_type", default="misc", show_default=True)
@click.option(
    "--field",
    "field_values",
    multiple=True,
    help="Bibliographic NAME=VALUE; repeat per field.",
)
@click.option("--bibcode", default=None, help="Optional actual ADS identifier.")
@click.pass_context
def add_reference(
    ctx: click.Context,
    cite_key: str,
    entry_type: str,
    field_values: tuple[str, ...],
    bibcode: str | None,
) -> None:
    """Save a manual reference without requiring a DOI or PDF."""
    fields = _parse_fields(field_values)
    with _open_library(ctx) as library:
        record = library.add(
            cite_key=cite_key, entry_type=entry_type, fields=fields, bibcode=bibcode
        )
        click.echo(f"{record['id']}\t{record['cite_key']}")


@reference_group.command("list")
@click.option(
    "--json", "as_json", is_flag=True, help="Include fields and attachments as JSON."
)
@click.pass_context
def list_references(ctx: click.Context, as_json: bool) -> None:
    """List local reference IDs, including cataloged ADS documents."""
    with _open_library(ctx) as library:
        records = library.list()
        if as_json:
            click.echo(json.dumps(records, ensure_ascii=False, indent=2))
            return
        for record in records:
            title = record["fields"].get("title", "").replace("\n", " ")
            linkage = "ADS" if record["bibcode"] else "Local"
            click.echo(f"{record['id']}\t{record['cite_key']}\t{linkage}\t{title}")
        if not records:
            click.echo(
                "No references. Use 'adsfolio reference import references.bib'.",
                err=True,
            )


@reference_group.command("edit")
@click.argument("document_id", type=int)
@click.option("--key", "cite_key", default=None)
@click.option("--type", "entry_type", default=None)
@click.option(
    "--field",
    "field_values",
    multiple=True,
    help="Set NAME=VALUE; other fields are preserved.",
)
@click.option(
    "--remove-field",
    "removed_fields",
    multiple=True,
    help="Remove one named field; repeat as needed.",
)
@click.pass_context
def edit_reference(
    ctx: click.Context,
    document_id: int,
    cite_key: str | None,
    entry_type: str | None,
    field_values: tuple[str, ...],
    removed_fields: tuple[str, ...],
) -> None:
    """Edit one reference while keeping its local identity and attachments."""
    updates = _parse_fields(field_values)
    removals = {name.strip().lower() for name in removed_fields}
    if removals & updates.keys():
        raise click.UsageError("A field cannot be set and removed in the same command.")
    with _open_library(ctx) as library:
        fields = dict(library.get(document_id)["fields"])
        for name in removals:
            if name not in fields:
                raise ValueError(f"Reference {document_id} has no field {name!r}.")
            del fields[name]
        fields.update(updates)
        record = library.update(
            document_id, cite_key=cite_key, entry_type=entry_type, fields=fields
        )
        click.echo(f"Updated {record['id']}\t{record['cite_key']}")


@reference_group.command("export")
@click.argument("document_ids", type=int, nargs=-1)
@click.option("--all", "export_all", is_flag=True, help="Export every local reference.")
@click.option(
    "-o",
    "--output",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Write atomically to a .bib file; default is standard output.",
)
@click.pass_context
def export_references(
    ctx: click.Context,
    document_ids: tuple[int, ...],
    export_all: bool,
    output: Path | None,
) -> None:
    """Export selected local IDs as BibTeX, including ADS and external entries."""
    if bool(document_ids) == export_all:
        raise click.UsageError("Supply reference IDs or --all, but not both.")
    with _open_library(ctx) as library:
        ids = (
            [record["id"] for record in library.list()]
            if export_all
            else list(document_ids)
        )
        if not ids:
            raise ValueError("No references to export.")
        text = library.export_bibtex(ids)
        if output is None or str(output) == "-":
            click.echo(text, nl=False)
        else:
            with click.open_file(
                str(output), "w", encoding="utf-8", atomic=True
            ) as stream:
                stream.write(text)
            click.echo(f"Exported bibliography to {output}.", err=True)


@reference_group.command("attach")
@click.argument("document_id", type=int)
@click.argument(
    "pdf_path", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.pass_context
def attach_reference_pdf(ctx: click.Context, document_id: int, pdf_path: Path) -> None:
    """Link an existing PDF to a reference without moving the file."""
    with _open_library(ctx) as library:
        library.attach_pdf(document_id, pdf_path)
        click.echo(f"Attached {pdf_path} to reference {document_id}.")
