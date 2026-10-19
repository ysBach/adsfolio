"""
CLI command definitions and entry point for adsfolio.

Usage:
    adsfolio search "query" [options]
    adsfolio export BIBCODE [options]
    adsfolio library <subcommand> [options]
"""

import inspect
import json
import os
import sys
from pathlib import Path

import click
import requests

from .. import ADSClient, __version__
from ..config import ADSError, Config
from ..cv.cli import cv_group
from ..pdf.cli import pdf as pdf_group
from ..pdf.prefetch import PublicationProfile, load_profile, query_publications
from ..pubcache import cache_path
from ..pubcache import clear as clear_publication_cache
from ..pubcache import load_publications, save_publications
from ..reference_cli import reference_group


def _version_callback(ctx: click.Context, param: click.Parameter, value: bool) -> None:
    if not value or ctx.resilient_parsing:
        return
    click.echo(f"ADSfolio, version {__version__}")
    ctx.exit()


@click.group()
@click.option(
    "--version",
    is_flag=True,
    callback=_version_callback,
    expose_value=False,
    is_eager=True,
    help="Show the version and exit.",
)
@click.option("--token", envvar="ADS_API_TOKEN", help="NASA ADS token")
@click.pass_context
def main(ctx: click.Context, token: str | None = None) -> None:
    """
    ADSfolio research library tools.

    Comprehensive Python client for NASA ADS (Astrophysics Data System).
    """
    ctx.ensure_object(dict)
    ctx.obj["token"] = token


@main.command("gui")
def gui_command() -> None:
    """Launch the desktop PDF library browser."""
    try:
        from ..gui.app import run
    except ImportError as exc:
        raise click.ClickException(
            "GUI dependencies are not installed. Install with: "
            "pip install 'adsfolio[gui]'"
        ) from exc
    raise SystemExit(run())


def _parse_optional_year(raw: str) -> int | None:
    text = raw.strip()
    if not text:
        return None
    return int(text)


def _parse_existing_year(raw: object) -> int | None:
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _mask_token(token: str) -> str:
    clean = token.strip()
    if not clean:
        return ""
    if len(clean) <= 8:
        if len(clean) <= 2:
            return "*" * len(clean)
        return f"{clean[:2]}...{clean[-1:]}"
    return f"{clean[:4]}...{clean[-4:]}"


def _load_existing_token() -> str:
    env_token = os.environ.get(Config.ENV_VAR, "").strip()
    if env_token:
        return env_token

    for path in Config.get_token_paths():
        try:
            if not path.exists():
                continue
            token = path.read_text().strip()
            if token:
                return token
        except OSError:
            continue
    return ""


def _select_token(token_value: str | None, existing_token: str) -> str:
    explicit = (token_value or "").strip()
    if explicit:
        return explicit

    if existing_token:
        click.echo(f"Found existing NASA ADS token: {_mask_token(existing_token)}")
        if click.confirm("Use existing API token?", default=True):
            return existing_token

    click.echo("Get your API token at: https://scixplorer.org/user/settings/token")
    return click.prompt("NASA ADS token", hide_input=True).strip()


def _normalize_names(raw_names: object) -> list[str]:
    if not isinstance(raw_names, (list, tuple)):
        return []
    return [str(name).strip() for name in raw_names if str(name).strip()]


def _normalize_library_ids(raw_ids: object) -> list[str]:
    if isinstance(raw_ids, str):
        raw_values = [raw_ids]
    elif isinstance(raw_ids, (list, tuple)):
        raw_values = list(raw_ids)
    else:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        for lib_id in str(raw_value).replace(";", ",").split(","):
            value = lib_id.strip()
            if value and value not in seen:
                normalized.append(value)
                seen.add(value)
    return normalized


def _select_orcid(orcid: str | None, existing_orcid: str) -> str:
    if orcid is not None:
        return orcid.strip()

    if existing_orcid:
        click.echo(f"Found existing ORCID: {existing_orcid}")
        if click.confirm("Use existing ORCID?", default=True):
            return existing_orcid

    return click.prompt(
        "ORCID (recommended; leave empty to skip)",
        default="",
        show_default=False,
    ).strip()


def _select_query_names(
    query_names: tuple[str, ...],
    existing_names: list[str],
) -> list[str]:
    provided_names = _normalize_names(query_names)
    if provided_names:
        return provided_names

    if existing_names:
        click.echo(f"Found existing query names: {'; '.join(existing_names)}")
        if click.confirm("Use existing query names?", default=True):
            return existing_names

    names_raw = click.prompt(
        "Optional full-name query terms (semicolon-separated, leave empty to skip)",
        default="",
        show_default=False,
    )
    return _normalize_names(tuple(names_raw.split(";")))


def _select_library_ids(
    library_ids: tuple[str, ...],
    existing_library_ids: list[str],
) -> list[str]:
    provided_ids = _normalize_library_ids(library_ids)
    if provided_ids:
        return provided_ids

    if existing_library_ids:
        click.echo(f"Found existing library IDs: {'; '.join(existing_library_ids)}")
        if click.confirm("Use existing library IDs?", default=True):
            return existing_library_ids

    raw_ids = click.prompt(
        "Optional library IDs for prefetch (comma-separated, leave empty to skip)",
        default="",
        show_default=False,
    )
    return _normalize_library_ids(raw_ids)


def _save_setup_profile(
    *,
    orcid: str,
    query_names: list[str],
    debut_year: int | None,
    end_year: int | None,
    library_ids: list[str],
) -> Path:
    """Call current or legacy setup writers used by downstream integrations."""
    parameters = inspect.signature(Config.save_user_setup).parameters
    year_keyword = "end_year" if "end_year" in parameters else "retire_year"
    return Config.save_user_setup(
        orcid=orcid,
        query_names=query_names,
        debut_year=debut_year,
        library_ids=library_ids,
        **{year_keyword: end_year},
    )


def _refresh_publication_cache(
    profile: PublicationProfile,
    token: str,
) -> Path:
    """Fetch and save the publication cache for `profile`."""
    documents = query_publications(ADSClient(token=token), profile)
    path = save_publications(profile, documents)
    click.echo(f"Cached {len(documents)} publications -> {path}")
    return path


def _select_optional_year(
    provided_year: int | None,
    existing_year: int | None,
    *,
    field_label: str,
    prompt_label: str,
) -> int | None:
    if provided_year is not None:
        return provided_year

    if existing_year is not None:
        click.echo(f"Found existing {field_label}: {existing_year}")
        if click.confirm(f"Use existing {field_label}?", default=True):
            return existing_year

    return _parse_optional_year(
        click.prompt(
            prompt_label,
            default="",
            show_default=False,
        )
    )


@main.command("set")
@click.option("--token", "token_value", help="NASA ADS token.")
@click.option(
    "--orcid", default=None, help="ORCID identifier for publication prefetch."
)
@click.option(
    "--query-name",
    "query_names",
    multiple=True,
    help="Optional full-name query term. Repeat flag for multiple names.",
)
@click.option(
    "--debut-year",
    type=int,
    default=None,
    help="Earliest publication year for prefetch window.",
)
@click.option(
    "--end-year",
    "--retire-year",
    "end_year",
    type=int,
    default=None,
    help="Latest publication year for prefetch window.",
)
@click.option(
    "--library-id",
    "library_ids",
    multiple=True,
    help="ADS library ID to include in identify prefetch. Repeat for multiple IDs.",
)
def set_command(
    token_value: str | None,
    orcid: str | None,
    query_names: tuple[str, ...],
    debut_year: int | None,
    end_year: int | None,
    library_ids: tuple[str, ...],
) -> None:
    """
    Interactive first-time setup for token + identify optimization profile.

    This profile is used only to reduce ADS requests during initial `pdf identify`.
    It does not constrain normal ADS searches/exports.
    """
    click.echo("Initial setup for low-query PDF identification.")
    click.echo(
        "Saved fields (token, ORCID, optional names, year window, optional "
        "libraries) are used to prefetch publication candidates before per-file "
        "ADS queries."
    )

    setup_raw = Config.get_user_setup()
    existing_orcid = str(setup_raw.get("orcid", "")).strip()
    existing_names = _normalize_names(setup_raw.get("query_names", []))
    existing_debut_year = _parse_existing_year(setup_raw.get("debut_year"))
    existing_end_year = _parse_existing_year(
        setup_raw.get("end_year", setup_raw.get("retire_year"))
    )
    existing_library_ids = _normalize_library_ids(setup_raw.get("library_ids", []))
    existing_token = _load_existing_token()

    token = _select_token(token_value, existing_token)
    if not token:
        click.secho("Error: token is required.", fg="red", err=True)
        sys.exit(1)

    orcid = _select_orcid(orcid, existing_orcid)

    click.secho(
        (
            "Optional name queries can overlap with other researchers. "
            "Prefer ORCID; add full names only if ORCID misses your papers."
        ),
        fg="yellow",
    )
    names = _select_query_names(query_names, existing_names)
    click.echo(
        "Library prefetch can bootstrap identify using papers already in your ADS "
        "library, reducing per-file lookup queries."
    )
    selected_library_ids = _select_library_ids(library_ids, existing_library_ids)

    if debut_year is None:
        try:
            debut_year = _select_optional_year(
                provided_year=debut_year,
                existing_year=existing_debut_year,
                field_label="debut year",
                prompt_label="Debut year for prefetch window (empty for no lower bound)",
            )
        except ValueError:
            click.secho("Error: debut year must be an integer.", fg="red", err=True)
            sys.exit(1)

    if end_year is None:
        try:
            end_year = _select_optional_year(
                provided_year=end_year,
                existing_year=existing_end_year,
                field_label="end year",
                prompt_label="End year for prefetch window (empty for no upper bound)",
            )
        except ValueError:
            click.secho("Error: end year must be an integer.", fg="red", err=True)
            sys.exit(1)

    if debut_year is not None and end_year is not None and debut_year > end_year:
        click.secho(
            "Error: debut year cannot be greater than retire year/end year.",
            fg="red",
            err=True,
        )
        sys.exit(1)

    try:
        token_path = Config.save_token(token)
        setup_path = _save_setup_profile(
            orcid=orcid,
            query_names=names,
            debut_year=debut_year,
            end_year=end_year,
            library_ids=selected_library_ids,
        )
    except OSError as exc:
        click.secho(f"Error saving setup: {exc}", fg="red", err=True)
        sys.exit(1)

    click.secho(f"Token saved to: {token_path}", fg="green")
    click.secho(f"Identify profile saved to: {setup_path}", fg="green")
    for library_id in selected_library_ids:
        click.echo(
            f"Library {library_id}: https://ui.adsabs.harvard.edu/user/libraries/{library_id}"
        )
    profile = PublicationProfile(
        orcid=orcid,
        query_names=names,
        debut_year=debut_year,
        end_year=end_year,
        library_ids=selected_library_ids,
    )
    try:
        publication_cache_path = _refresh_publication_cache(profile, token)
        click.echo(f"Publication cache: {publication_cache_path}")
        click.echo("Remove that file (or run `adsfolio cache clear`) to reset.")
    except (ADSError, OSError, requests.RequestException) as exc:
        click.secho(f"Warning: could not build publication cache: {exc}", fg="yellow")
    click.echo(
        "During `adsfolio pdf identify`, this profile prefetches likely matches first "
        "to reduce per-file ADS requests, including direct library-content "
        "bootstrap when configured."
    )


@main.group("cache")
def cache() -> None:
    """Manage the user-level publication cache."""


@cache.command("status")
def cache_status() -> None:
    """Show publication cache metadata."""
    path = cache_path()
    cached = load_publications()
    click.echo(f"Cache path: {path}")
    if cached is None:
        click.echo("Status: missing")
        return
    click.echo(f"Fetched at: {cached.fetched_at}")
    click.echo(f"Documents: {len(cached.documents)}")
    click.echo(f"Profile: {cached.profile}")


@cache.command("refresh")
@click.pass_context
def cache_refresh(ctx: click.Context) -> None:
    """Refresh the publication cache using the saved setup profile."""
    profile = load_profile()
    if not profile.has_identity:
        raise click.ClickException(
            "No usable identify profile found. Run `adsfolio set` first."
        )
    token = str(ctx.obj.get("token") or _load_existing_token()).strip()
    if not token:
        raise click.ClickException("No NASA ADS token found. Run `adsfolio set` first.")
    try:
        _refresh_publication_cache(profile, token)
    except ADSError as exc:
        raise click.ClickException(str(exc)) from exc


@cache.command("clear")
def cache_clear() -> None:
    """Remove the user-level publication cache."""
    if clear_publication_cache():
        click.echo(f"Removed publication cache: {cache_path()}")
    else:
        click.echo(f"Publication cache already absent: {cache_path()}")


@main.command()
@click.argument("query")
@click.option(
    "-f",
    "--fields",
    default="bibcode,title,author,year",
    help="Comma-separated fields to return",
)
@click.option("-n", "--rows", default=10, type=int, help="Number of results (max 2000)")
@click.option("-s", "--sort", help="Sort order (e.g., 'citation_count desc')")
@click.option("--fq", help="Filter query")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def search(
    ctx,
    query: str,
    fields: str,
    rows: int,
    sort: str | None,
    fq: str | None,
    as_json: bool,
) -> None:
    """
    Search the ADS database.

    Examples:

        adsfolio search "author:Einstein"

        adsfolio search "black holes" -n 50 -s "citation_count desc"

        adsfolio search "author:Kurtz" --fq "property:refereed"
    """
    try:
        client = ADSClient(token=ctx.obj.get("token"))
        fl = [f.strip() for f in fields.split(",")]

        results = client.search.query(q=query, fl=fl, rows=rows, sort=sort, fq=fq)

        if as_json:
            output = {
                "num_found": results.num_found,
                "docs": results.docs,
            }
            click.echo(json.dumps(output, indent=2))
        else:
            click.echo(f"Found {results.num_found} results:\n")
            for i, doc in enumerate(results.docs, 1):
                bibcode = doc.get("bibcode", "")
                title = (
                    doc.get("title", [""])[0]
                    if isinstance(doc.get("title"), list)
                    else doc.get("title", "")
                )
                year = doc.get("year", "")
                click.echo(f"{i:3d}. [{year}] {bibcode}")
                click.echo(f"     {title[:80]}{'...' if len(title) > 80 else ''}")
                click.echo()

    except ADSError as e:
        click.secho(f"Error: {e}", fg="red", err=True)
        sys.exit(1)


@main.command()
@click.argument("bibcodes", nargs=-1, required=True)
@click.option(
    "-f",
    "--format",
    "fmt",
    default="bibtex",
    help="Export format (bibtex, aastex, mnras, custom, etc.)",
)
@click.option("-o", "--output", type=click.Path(), help="Output file")
@click.option("-s", "--sort", help="Sort order")
@click.option("--custom", help="Custom format string (use with -f custom)")
@click.option(
    "-j",
    "--journal",
    "journalname",
    default="ads",
    type=click.Choice(["ads", "full", "iso4"]),
    help="Journal name style: ads (macros), full, or iso4",
)
@click.pass_context
def export(
    ctx,
    bibcodes: tuple,
    fmt: str,
    output: str | None,
    sort: str | None,
    custom: str | None,
    journalname: str,
) -> None:
    """
    Export bibliographic data.

    Examples:

        adsfolio export 2020ApJ...900..100S

        adsfolio export 2020ApJ...900..100S 2021MNRAS.501..123A -f bibtex -o refs.bib

        adsfolio export 2020ApJ...900..100S -f custom --custom "%R %T"

        adsfolio export 2020ApJ...900..100S -j full  # Full journal names

        adsfolio export 2020ApJ...900..100S -j iso4  # ISO4 abbreviations
    """
    try:
        client = ADSClient(token=ctx.obj.get("token"))

        result = client.export.export(
            bibcodes=list(bibcodes),
            format=fmt,
            sort=sort,
            custom_format=custom,
        )

        # Apply journal name formatting
        if journalname != "ads":
            from ..bibtex.formatter import change_journal_name

            result = change_journal_name(result, format=journalname)

        if output:
            with open(output, "w") as f:
                f.write(result)
            click.echo(f"Exported {len(bibcodes)} entries to {output}")
        else:
            click.echo(result)

    except ADSError as e:
        click.secho(f"Error: {e}", fg="red", err=True)
        sys.exit(1)


@main.group()
def library() -> None:
    """Manage ADS libraries."""


@library.command("list")
@click.option("-n", "--rows", default=20, type=int, help="Number of libraries to show")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def library_list(ctx, rows: int, as_json: bool) -> None:
    """List your libraries."""
    try:
        client = ADSClient(token=ctx.obj.get("token"))
        libs = client.libraries.list_libraries(rows=rows)

        if as_json:
            output = [
                {"id": lib.id, "name": lib.name, "num_documents": lib.num_documents}
                for lib in libs
            ]
            click.echo(json.dumps(output, indent=2))
        else:
            click.echo(f"Found {len(libs)} libraries:\n")
            for lib in libs:
                click.echo(f"  {lib.id}  {lib.name} ({lib.num_documents} docs)")

    except ADSError as e:
        click.secho(f"Error: {e}", fg="red", err=True)
        sys.exit(1)


@library.command("get")
@click.argument("library_id")
@click.option("--bibtex", is_flag=True, help="Export as BibTeX")
@click.option("-o", "--output", type=click.Path(), help="Output file")
@click.option("--raw", is_flag=True, help="Return raw bibcodes")
@click.pass_context
def library_get(
    ctx, library_id: str, bibtex: bool, output: str | None, raw: bool
) -> None:
    """Get library contents."""
    try:
        client = ADSClient(token=ctx.obj.get("token"))
        lib = client.libraries.get_library(library_id, raw=raw)

        click.echo(f"Library: {lib.name}")
        click.echo(f"Description: {lib.description}")
        click.echo(f"Documents: {lib.num_documents}")
        click.echo()

        if bibtex and lib.documents:
            result = client.export.bibtex(lib.documents)
            if output:
                with open(output, "w") as f:
                    f.write(result)
                click.echo(f"Exported to {output}")
            else:
                click.echo(result)
        else:
            for bib in lib.documents:
                click.echo(bib)

    except ADSError as e:
        click.secho(f"Error: {e}", fg="red", err=True)
        sys.exit(1)


@library.command("watch")
@click.argument("library_id")
@click.option(
    "-o",
    "--output",
    default="references.bib",
    help="Output BibTeX file (default: references.bib)",
)
@click.option(
    "-n",
    "--num-iter",
    default=500,
    type=int,
    help="Number of iterations (default: 500, use 0 for infinite)",
)
@click.option(
    "-t",
    "--interval",
    default=5.0,
    type=float,
    help="Seconds between checks (default: 5)",
)
@click.option(
    "-i",
    "--info-interval",
    default=20,
    type=int,
    help="Iterations between status prints (default: 20)",
)
@click.option(
    "-s",
    "--sort",
    default="date asc",
    help="Sort order for export (default: 'date asc')",
)
@click.option(
    "-f", "--format", "fmt", default="bibtex", help="Export format (default: bibtex)"
)
@click.option(
    "-j",
    "--journal",
    "journalname",
    default="ads",
    type=click.Choice(["ads", "full", "iso4"]),
    help="Journal name style: ads (macros), full, or iso4",
)
@click.option(
    "-a",
    "--additional",
    type=click.Path(exists=True),
    help="Additional BibTeX file to append",
)
@click.pass_context
def library_watch(
    ctx,
    library_id: str,
    output: str,
    num_iter: int,
    interval: float,
    info_interval: int,
    sort: str,
    fmt: str,
    journalname: str,
    additional: str | None,
) -> None:
    """
    Watch a library for changes and export BibTeX.

    Continuously polls the library and updates the output file when changes
    are detected. Useful for keeping a references.bib file in sync.

    Examples:

        adsfolio library watch ABC123XYZ -o refs.bib

        adsfolio library watch ABC123XYZ -n 0 -t 10  # infinite, 10s interval

        adsfolio library watch ABC123XYZ -j full  # Full journal names
    """
    import time
    from datetime import datetime
    from pathlib import Path

    from ..bibtex.formatter import change_journal_name

    try:
        client = ADSClient(token=ctx.obj.get("token"))

        # Initial fetch
        click.echo(f"Fetching library {library_id}...")
        lib = client.libraries.get_library(library_id)
        click.echo(f"Library: {lib.name} ({lib.num_documents} documents)")
        click.echo(f"Watching for changes every {interval}s...")
        click.echo()

        last_modified = lib.date_last_modified
        docs_old = set(lib.documents)

        # Initial export
        if lib.documents:
            bibtex = client.export.export(lib.documents, format=fmt, sort=sort)
            if journalname != "ads":
                bibtex = change_journal_name(bibtex, format=journalname)
            content = bibtex

            # Append additional file if specified
            if additional:
                add_content = Path(additional).read_text()
                if journalname != "ads":
                    add_content = change_journal_name(add_content, format=journalname)
                content += "\n" + add_content

            Path(output).write_text(content)
            click.secho(f"[{datetime.now():%H:%M:%S}] Exported to {output}", fg="green")

        # Polling loop
        i = 0
        infinite = num_iter == 0
        while infinite or i < num_iter:
            i += 1
            time.sleep(interval)

            try:
                lib = client.libraries.get_library(library_id)
            except ADSError:
                # API error, skip this iteration
                continue

            docs_new = set(lib.documents)

            # Check for changes
            if lib.date_last_modified != last_modified:
                added = docs_new - docs_old
                removed = docs_old - docs_new

                # Print changes
                click.echo()
                click.secho(f"{'=' * 20} Library Changed {'=' * 20}", bold=True)
                click.echo(f"Documents: {len(docs_old)} → {len(docs_new)}")

                if added:
                    click.secho(f" + {len(added)} added:", fg="green")
                    for bib in list(added)[:5]:
                        click.echo(f"   {bib}")
                    if len(added) > 5:
                        click.echo(f"   ... and {len(added) - 5} more")

                if removed:
                    click.secho(f" - {len(removed)} removed:", fg="red")
                    for bib in list(removed)[:5]:
                        click.echo(f"   {bib}")
                    if len(removed) > 5:
                        click.echo(f"   ... and {len(removed) - 5} more")

                # Re-export
                if lib.documents:
                    bibtex = client.export.export(lib.documents, format=fmt, sort=sort)
                    if journalname != "ads":
                        bibtex = change_journal_name(bibtex, format=journalname)
                    content = bibtex
                    if additional:
                        add_content = Path(additional).read_text()
                        if journalname != "ads":
                            add_content = change_journal_name(
                                add_content, format=journalname
                            )
                        content += "\n" + add_content
                    Path(output).write_text(content)
                    click.secho(
                        f"[{datetime.now():%H:%M:%S}] Updated {output}", fg="green"
                    )

                last_modified = lib.date_last_modified
                docs_old = docs_new
                click.echo()

            # Progress info
            if info_interval > 0 and i % info_interval == 0:
                if infinite:
                    click.echo(f"[INFO] Iteration {i} - watching...")
                else:
                    pct = 100 * i / num_iter
                    click.echo(f"[INFO] Iteration {i}/{num_iter} ({pct:.1f}%)")

        click.echo("Watch completed.")

    except KeyboardInterrupt:
        click.echo("\nStopped by user.")
    except ADSError as e:
        click.secho(f"Error: {e}", fg="red", err=True)
        sys.exit(1)


@main.group()
def config() -> None:
    """Manage configuration."""


@config.command("set-token")
@click.argument("token")
def config_token(token: str) -> None:
    """
    Save API token to config file.

    This token will be used by default for all NASA ADS operations
    when no other token is provided via environment variable or arguments.
    """
    try:
        path = Config.save_token(token)
        click.secho(f"Token saved to: {path}", fg="green")
        click.echo("Permissions set to 600 (user read/write only)")
    except Exception as e:
        click.secho(f"Error saving token: {e}", fg="red", err=True)
        sys.exit(1)


@config.command("show")
def config_show() -> None:
    """Show current configuration paths."""
    click.echo(f"Config Dir: {Config.get_config_dir()}")

    click.echo("\nToken Search Paths:")
    found = False
    for p in Config.get_token_paths():
        status = "EXISTS" if p.exists() else "MISSING"
        color = "green" if p.exists() else "yellow"
        click.secho(f"  {p} -> {status}", fg=color)
        if p.exists():
            found = True

    if found:
        click.secho("\nToken found in at least one location.", fg="green")
    else:
        click.secho("\nNo token found in standard locations.", fg="red")


@main.command()
def rate_limit() -> None:
    """Show current rate limit status."""
    click.echo(f"Config Dir: {Config.get_config_dir()}")
    for p in Config.get_token_paths():
        click.echo(f"Checking {p} -> {'EXISTS' if p.exists() else 'MISSING'}")

    limits = Config.get_rate_limits()
    click.echo("\nRate Limits:")
    click.echo(f"  Limit:     {limits.get('limit', 'Unknown')}")
    click.echo(f"  Remaining: {limits.get('remaining', 'Unknown')}")
    click.echo(f"  Reset:     {limits.get('reset', 'Unknown')}")


main.add_command(pdf_group)
main.add_command(cv_group)
main.add_command(reference_group)


if __name__ == "__main__":
    main()
