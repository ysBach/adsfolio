"""Shared helpers for pdf CLI commands."""

from pathlib import Path

import click

from ..client import ADSClient
from .files import iter_pdf_paths
from .readiness import METADATA_STALE_KEY

__all__ = [
    "get_client",
    "handle_unresolved",
    "is_resolved_record",
    "iter_pdf_paths",
    "unique_destination",
    "validate_author_format",
    "validate_char_policy",
]


def get_client(ctx: click.Context) -> ADSClient:
    token = None
    if ctx.obj:
        token = ctx.obj.get("token")
    return ADSClient(token=token)


def unique_destination(output_dir: Path, filename: str) -> Path:
    destination = output_dir / filename
    if not destination.exists():
        return destination

    stem = Path(filename).stem
    suffix = Path(filename).suffix
    counter = 1
    while True:
        candidate = output_dir / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def is_resolved_record(record: dict[str, object]) -> bool:
    """Return whether a registry record contains a successful ADS match."""
    raw_metadata = record.get("metadata")
    metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    if bool(record.get(METADATA_STALE_KEY) or metadata.get(METADATA_STALE_KEY)):
        return False
    bibcode = record.get("bibcode", "")
    if isinstance(bibcode, list):
        has_bibcode = any(str(value).strip() for value in bibcode)
    else:
        has_bibcode = bool(str(bibcode).strip())
    return has_bibcode and str(record.get("status", "")).strip() != "unresolved"


def handle_unresolved(
    pdf_path: Path,
    output_dir: Path | None,
    apply: bool,
    report: bool = True,
    label: str = "unresolved",
) -> Path:
    if output_dir is None:
        return pdf_path
    try:
        already_in_output = pdf_path.parent.resolve() == output_dir.resolve()
    except OSError:
        already_in_output = pdf_path.parent == output_dir
    if already_in_output:
        return pdf_path
    if not apply:
        if report:
            click.echo(f"[dry-run] move {label} {pdf_path} -> {output_dir}")
        return pdf_path

    output_dir.mkdir(parents=True, exist_ok=True)
    target = unique_destination(output_dir, pdf_path.name)
    pdf_path.rename(target)
    if report:
        click.echo(f"Moved {label}: {pdf_path.name} -> {target}")
    return target


def validate_author_format(_: click.Context, __: click.Parameter, value: str) -> str:
    allowed = {"surname", "surname_initials", "last_comma_first"}
    if value in allowed or value.startswith("ads_custom:"):
        return value
    raise click.BadParameter(
        "must be surname, surname_initials, last_comma_first, or ads_custom:<format>"
    )


def validate_char_policy(_: click.Context, __: click.Parameter, value: str) -> str:
    allowed = {"ascii", "keep"}
    if value in allowed:
        return value
    raise click.BadParameter("must be ascii or keep")
