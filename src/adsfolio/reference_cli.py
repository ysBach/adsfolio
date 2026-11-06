"""Import external references and export local bibliographies from the CLI."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import TextIO

import click

from .bibtex.sanitize import DEFAULT_EXCLUDED_FIELDS
from .citekeys import KeyStyle
from .config import Config
from .pdf.catalog import PdfCatalog
from .references import CitationKeyError, LocalReferences, parse_bibtex


@contextmanager
def _open_library(ctx: click.Context) -> Iterator[LocalReferences]:
    """Open the selected catalog and report recoverable errors as CLI failures."""
    catalog = None
    try:
        catalog = PdfCatalog(ctx.obj.get("reference_catalog"))
        yield LocalReferences(catalog, Config.load_key_policy())
    except CitationKeyError as exc:
        raise click.ClickException(_key_problem_report(exc)) from exc
    except (ValueError, OSError, sqlite3.Error) as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        if catalog is not None:
            catalog.close()


def _key_problem_report(error: CitationKeyError) -> str:
    """List the papers that need a key, and the command that sets one."""
    lines = [str(error)]

    def describe(document_id: int) -> str:
        fields = error.references[document_id]["fields"]
        title = fields.get("title", "Untitled")
        return f"  {document_id}: {fields.get('year', '?')} {title}"

    for document_id in error.assignment.missing:
        lines.append(describe(document_id) + "  (no key)")
    for key, ids in sorted(error.assignment.clashes.items()):
        lines.extend(describe(document_id) + f"  (shares {key})" for document_id in ids)
    lines.append("Set a key with: adsfolio reference set-key ID KEY")
    return "\n".join(lines)


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
    """Keep external references alongside ADS records. Only export --fetch-ads needs a token."""
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
@click.option(
    "--fetch-ads",
    is_flag=True,
    help="First get ADS's own BibTeX for papers without a saved copy. Needs a token.",
)
@click.option(
    "--leave-out",
    default=", ".join(DEFAULT_EXCLUDED_FIELDS),
    show_default=True,
    help="Fields to leave out of every entry, separated by commas. Use '' to keep all.",
)
@click.pass_context
def export_references(
    ctx: click.Context,
    document_ids: tuple[int, ...],
    export_all: bool,
    output: Path | None,
    fetch_ads: bool,
    leave_out: str,
) -> None:
    """Export selected local IDs as BibTeX, including ADS and external entries.

    Without --fetch-ads, the command works offline. An ADS paper without a
    saved ADS entry then uses its stored fields. With --fetch-ads, the command
    gets and saves the missing ADS entries first. It stops and writes nothing
    when ADS fails. An output file with the same text is not written again.
    As in the desktop export, the abstract and adsnote fields are left out
    unless --leave-out names other fields.
    """
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
        if fetch_ads:
            _fetch_ads_bibtex(ctx, library, ids)
        excluded = [name.strip() for name in leave_out.split(",") if name.strip()]
        text = library.export_bibtex(ids, exclude_fields=excluded)
        if output is None or str(output) == "-":
            click.echo(text, nl=False)
        elif output.is_file() and output.read_text(encoding="utf-8") == text:
            click.echo(f"{output} is up to date.", err=True)
        else:
            with click.open_file(
                str(output), "w", encoding="utf-8", atomic=True
            ) as stream:
                stream.write(text)
            click.echo(f"Exported bibliography to {output}.", err=True)


def _fetch_ads_bibtex(
    ctx: click.Context, library: LocalReferences, ids: list[int]
) -> None:
    """Get and save ADS BibTeX for the papers in `ids` without a saved copy."""
    wanted = library.ads_bibtex_requests(ids)
    if not wanted:
        return
    import requests

    from .client import ADSClient
    from .config import ADSError, AuthenticationError

    click.echo(f"Getting ADS BibTeX for {len(wanted):,} papers...", err=True)
    try:
        client = ADSClient(token=ctx.obj.get("token"))
        try:
            entries = client.export.export_entries(list(wanted))
        finally:
            client.close()
    except (ADSError, AuthenticationError, requests.RequestException) as exc:
        raise click.ClickException(
            f"Could not get ADS BibTeX: {exc} No file was written."
        ) from exc
    saved = library.store_ads_bibtex(entries, wanted)
    click.echo(f"Saved ADS BibTeX for {saved:,} papers.", err=True)


@reference_group.command("keys")
@click.option(
    "--ads",
    "ads_style",
    type=click.Choice([style.value for style in KeyStyle]),
    help="Key style for papers with an ADS bibcode.",
)
@click.option(
    "--other",
    "other_style",
    type=click.Choice([s.value for s in KeyStyle if s is not KeyStyle.BIBCODE]),
    help="Key style for papers without an ADS bibcode.",
)
@click.option(
    "--template",
    help="Template for the 'template' style, such as '{first_author_initials}{year}'.",
)
def citation_keys(
    ads_style: str | None, other_style: str | None, template: str | None
) -> None:
    """Show or set how BibTeX export makes citation keys.

    Styles: bibcode (ADS papers only), doi, template, scholar (Google Scholar
    style, such as roe2021theory), and manual (type each key with set-key).
    A key that you set with set-key, or that came with imported BibTeX,
    always wins. Without options, this shows the current styles.
    """
    policy = Config.load_key_policy()
    if ads_style or other_style or template is not None:
        try:
            policy = replace(
                policy,
                ads=KeyStyle(ads_style or policy.ads),
                other=KeyStyle(other_style or policy.other),
                template=policy.template if template is None else template,
            )
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        Config.save_key_policy(policy)
    click.echo(f"ADS papers: {policy.ads.value}")
    click.echo(f"Papers outside ADS: {policy.other.value}")
    click.echo(f"Template: {policy.template}")


_AUX_CITATION = re.compile(r"\\citation\{([^}]*)\}")
_AUX_INPUT = re.compile(r"\\@input\{([^}]*)\}")


def _aux_citations(path: Path, seen: set[Path] | None = None) -> list[str]:
    """Return the keys in the ``\\citation`` lines of an ``.aux`` file.

    The ``.aux`` files of ``\\include`` files, named in ``\\@input`` lines, are
    read too. The ``*`` of ``\\nocite{*}`` is not a key and is left out.
    """
    seen = set() if seen is None else seen
    path = path.resolve()
    if path in seen:
        return []
    seen.add(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    keys = [
        key.strip()
        for group in _AUX_CITATION.findall(text)
        for key in group.split(",")
        if key.strip() and key.strip() != "*"
    ]
    for name in _AUX_INPUT.findall(text):
        child = path.parent / name
        if child.is_file():
            keys.extend(_aux_citations(child, seen))
    return keys


@reference_group.command("check")
@click.argument(
    "aux_file", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.pass_context
def check_citations(ctx: click.Context, aux_file: Path) -> None:
    """Find old citation keys in a LaTeX .aux file. Show the current keys.

    Run LaTeX first. The command reads the \\citation lines that BibTeX uses.
    It does not read biblatex .aux files. It does not change files.

    A preprint key becomes old when ADS publishes the paper with a new
    bibcode. A key also becomes old when the key style changes.
    """
    keys = _aux_citations(aux_file)
    if not keys:
        raise click.ClickException(
            f"{aux_file} has no \\citation lines. Run LaTeX with BibTeX first."
        )
    with _open_library(ctx) as library:
        problems = library.check_citations(keys)
    old = {key: found for key, found in problems.items() if found}
    unknown = [key for key, found in problems.items() if not found]
    cited = len(dict.fromkeys(keys))
    click.echo(
        f"{cited} cited keys: {cited - len(problems)} current, "
        f"{len(old)} old, {len(unknown)} not in the library."
    )
    if old:
        click.echo("Old keys (replace them in your .tex files):")
        for key, found in old.items():
            click.echo(f"  {key} -> {' or '.join(found)}")
    if unknown:
        click.echo("Keys not in the ADSfolio library (check them by hand):")
        for key in unknown:
            click.echo(f"  {key}")


@reference_group.command("set-key")
@click.argument("document_id", type=int)
@click.argument("key", default="")
@click.pass_context
def set_reference_key(ctx: click.Context, document_id: int, key: str) -> None:
    """Set the citation key of one reference. Leave KEY out to remove it.

    The key wins over the key style in the settings. To see reference IDs,
    run ``adsfolio reference list``.
    """
    with _open_library(ctx) as library:
        library.set_cite_key(document_id, key)
        current = library.get(document_id)["cite_key"]
    click.echo(f"Reference {document_id} uses the key {current or '(none)'}.")


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
