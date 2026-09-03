"""Click command group for ``adsapi cv``."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import click

from ..api.search import escape_query_phrase
from ..client import ADSClient
from ..config import ADSError, Config
from . import generate_cv
from .config import (
    DEFAULT_QUERY,
    SUPPORTED_OUTPUT_FORMATS,
    SUPPORTED_STYLES,
    load_config,
    normalize_author_format,
)
from .config_starter import make_starter_config
from .render_factory import style_examples

__all__ = ["cv_group"]


@click.group("cv")
def cv_group() -> None:
    """Generate publication fragments for CV workflows."""


def _as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _setup_names(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _year_window_clause(start: int | None, end: int | None) -> str:
    left = "*" if start is None else str(start)
    right = "*" if end is None else str(end)
    if left == "*" and right == "*":
        return ""
    return f"year:[{left} TO {right}]"


def _query_default(
    names: list[str],
    debut_year: int | None,
    retire_year: int | None,
    *,
    fallback: str,
) -> str:
    clauses = [f'author:"{escape_query_phrase(name)}"' for name in names]
    base = ""
    if clauses:
        base = " OR ".join(clauses)
        if len(clauses) > 1:
            base = f"({base})"
    year_clause = _year_window_clause(debut_year, retire_year)
    if base and year_clause:
        return f"{base} AND {year_clause}"
    if base:
        return base
    return fallback


def _first_author_prefix(names: list[str]) -> str:
    for name in names:
        if "," not in name:
            continue
        surname, given = name.split(",", 1)
        token = given.strip().split()
        if token:
            initial = token[0].replace(".", "").strip()
            if initial:
                return f"{surname.strip().lower()}, {initial[0].lower()}"
    return "doe, j"


def _me_target_default(names: list[str]) -> str:
    return names[0] if names else "Doe, J."


def _init_defaults(no_orcid: bool) -> dict[str, Any]:
    profile = Config.load_user_profile()
    orcid = profile.orcid
    names = list(profile.author_names)
    debut_year = profile.first_publication_year
    retire_year = profile.last_publication_year
    use_orcid = bool(orcid) and not no_orcid
    query_names = [] if use_orcid else names
    query_fallback = "*:*" if use_orcid else DEFAULT_QUERY

    return {
        "query": _query_default(
            query_names,
            debut_year,
            retire_year,
            fallback=query_fallback,
        ),
        "first_author_prefix": _first_author_prefix(names),
        "me_target": _me_target_default(names),
        "me_display": "Jane Doe",
        "orcid": orcid,
        "use_orcid": use_orcid,
        "debut_year": debut_year,
        "retire_year": retire_year,
    }


def _prompt_init_fields(defaults: dict[str, Any]) -> dict[str, str]:
    query = click.prompt("ADS Solr query", default=defaults["query"]).strip()
    first_author_prefix = click.prompt(
        "First-author prefix",
        default=defaults["first_author_prefix"],
    ).strip()
    me_target = click.prompt(
        "Name token to highlight",
        default=defaults["me_target"],
    ).strip()
    me_display = click.prompt(
        "Display name",
        default=defaults["me_display"],
    ).strip()
    author_format = normalize_author_format(
        click.prompt(
            "Author format (cv_default or ads_custom:<format>)",
            default="cv_default",
        ).strip()
    )
    return {
        "query": query,
        "first_author_prefix": first_author_prefix,
        "me_target": me_target,
        "me_display": me_display,
        "author_format": author_format,
    }


def _write_yaml_config(output_path: Path, payload: dict[str, Any]) -> None:
    try:
        import yaml
    except ModuleNotFoundError as exc:
        click.secho(
            "Error: PyYAML is required for `adsapi cv init`.",
            fg="red",
            err=True,
        )
        raise SystemExit(1) from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=False),
        encoding="utf-8",
    )


@cv_group.command("generate")
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=Path("cv.yaml"),
    show_default=True,
    help="Path to cv.yaml",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Override output file path.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(SUPPORTED_OUTPUT_FORMATS),
    default=None,
    help="Override output format from config.",
)
@click.option(
    "--style",
    type=click.Choice(SUPPORTED_STYLES),
    default=None,
    help="Override style from config.",
)
@click.option(
    "--author-format",
    default=None,
    help="Override author format (cv_default or ads_custom:<format>).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print output to stdout without writing files.",
)
@click.pass_context
def generate_command(
    ctx: click.Context,
    config_path: Path,
    output_path: Path | None,
    output_format: str | None,
    style: str | None,
    author_format: str | None,
    dry_run: bool,
) -> None:
    """Query ADS and generate CV output."""
    try:
        cfg = load_config(config_path)
        if author_format is not None:
            cfg = replace(cfg, author_format=normalize_author_format(author_format))
        client = ADSClient(token=(ctx.obj or {}).get("token"))
        rendered = generate_cv(
            client,
            cfg,
            output_path=output_path,
            output_format=output_format,  # type: ignore[arg-type]
            style=style,
            write_output=not dry_run,
        )
    except (ADSError, OSError, RuntimeError, ValueError) as exc:
        click.secho(f"Error: {exc}", fg="red", err=True)
        sys.exit(1)

    if dry_run:
        click.echo(rendered)
        return

    target = output_path or cfg.output_path
    click.secho(f"Wrote CV output to: {target}", fg="green")


@cv_group.command("init")
@click.option(
    "--output",
    "output_path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=Path("cv.yaml"),
    show_default=True,
    help="Path for starter cv config.",
)
@click.option("--force", is_flag=True, help="Overwrite output file if it exists.")
@click.option(
    "--no-orcid",
    is_flag=True,
    help="Do not auto-use ORCID from `adsapi set` profile.",
)
def init_command(output_path: Path, force: bool, no_orcid: bool) -> None:
    """Create an interactive starter ``cv.yaml``."""
    if output_path.exists() and not force:
        click.secho(
            f"Error: {output_path} already exists. Use --force to overwrite.",
            fg="red",
            err=True,
        )
        sys.exit(1)

    defaults = _init_defaults(no_orcid=no_orcid)
    if defaults["orcid"] and defaults["use_orcid"]:
        click.echo(
            "Using ORCID from `adsapi set` profile by default. "
            "Use --no-orcid to disable this."
        )
    prompted = _prompt_init_fields(defaults)
    payload = make_starter_config(
        prompted["query"],
        prompted["first_author_prefix"],
        prompted["me_target"],
        prompted["me_display"],
        orcid=defaults["orcid"],
        use_orcid=defaults["use_orcid"],
        debut_year=defaults["debut_year"],
        retire_year=defaults["retire_year"],
        author_format=prompted["author_format"],
    )

    try:
        _write_yaml_config(output_path, payload)
    except OSError as exc:
        click.secho(f"Error writing config: {exc}", fg="red", err=True)
        sys.exit(1)

    click.secho(f"Created starter config: {output_path}", fg="green")


@cv_group.command("show-styles")
def show_styles_command() -> None:
    """Show compact previews for each supported rendering style."""
    for style, preview in style_examples().items():
        click.secho(style, bold=True)
        click.echo(preview)
        click.echo()
