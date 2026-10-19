"""Upgrade-specific CLI command for ``adsfolio pdf``."""

import sys
from pathlib import Path

import click

from ..config import ADSError
from .cli_common import get_client as _get_client
from .registry import open_registry_state
from .upgrade import PdfUpgrader

__all__ = ["upgrade_command"]


@click.command("upgrade")
@click.argument(
    "directory", type=click.Path(exists=True, file_okay=False, path_type=Path)
)
@click.option("--apply", is_flag=True, help="Execute upgrades. Default is dry-run.")
@click.option(
    "--overwrite-arxiv-with-official",
    is_flag=True,
    help="Overwrite tracked arXiv PDF with downloaded official PDF.",
)
@click.option(
    "--arxiv-backup-if-overwritten",
    type=click.Path(file_okay=False, path_type=Path),
    help="Backup directory for arXiv files before overwrite.",
)
@click.option(
    "--skip-annotation-port",
    is_flag=True,
    help="Skip annotation transfer from arXiv to official PDF.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Force re-check even if entry already marked as published/upgraded.",
)
@click.pass_context
def upgrade_command(
    ctx: click.Context,
    directory: Path,
    apply: bool,
    overwrite_arxiv_with_official: bool,
    arxiv_backup_if_overwritten: Path | None,
    skip_annotation_port: bool,
    force: bool,
) -> None:
    """Upgrade arXiv PDFs to published versions using ADS metadata."""
    try:
        sidecar = open_registry_state(directory)
        client = _get_client(ctx)
        upgrader = PdfUpgrader(client=client)
        results = upgrader.batch_upgrade(
            sidecar=sidecar,
            apply=apply,
            overwrite_arxiv_with_official=overwrite_arxiv_with_official,
            arxiv_backup_if_overwritten=arxiv_backup_if_overwritten,
            skip_annotation_port=skip_annotation_port,
            force=force,
        )
        if not results:
            click.echo("No arXiv entries found in sidecar.")
            return

        for result in results:
            target_bibcode = result.new_bibcode or result.old_bibcode
            click.echo(
                f"{result.original_filename}: {result.status} "
                f"{result.old_bibcode} -> {target_bibcode}"
            )
            if result.message:
                click.echo(f"  {result.message}")

        if not apply:
            click.echo("Dry-run only. Re-run with --apply to execute upgrades.")
    except ADSError as exc:
        click.secho(f"Error: {exc}", fg="red", err=True)
        sys.exit(1)
