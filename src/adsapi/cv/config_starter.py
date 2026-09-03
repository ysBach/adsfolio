"""Starter ``cv.yaml`` payload helpers for ``adsapi cv init``."""

from __future__ import annotations

from typing import Any

from .config import (
    DEFAULT_EXCLUDE_BIBSTEMS,
    DEFAULT_ME_FMT,
    DEFAULT_QUERY,
    normalize_author_format,
)


def _starter_author(
    query: str,
    first_author_prefix: str,
    me_target: str,
    me_display: str,
    *,
    orcid: str,
    use_orcid: bool,
    debut_year: int | None,
    retire_year: int | None,
) -> dict[str, Any]:
    return {
        "query": query or DEFAULT_QUERY,
        "orcid": orcid,
        "use_orcid": use_orcid,
        "debut_year": debut_year,
        "retire_year": retire_year,
        "exclude_bibstems": list(DEFAULT_EXCLUDE_BIBSTEMS),
        "first_author_prefix": first_author_prefix or "doe, j",
        "me_target": me_target or "Doe, J.",
        "me_display": me_display or "Jane Doe",
    }


def _starter_format(author_format: str) -> dict[str, Any]:
    return {
        "output": "latex",
        "style": "compact",
        "n_max_authors": 5,
        "n_min_authors": 3,
        "n_me": 6,
        "author_sep": "; ",
        "me_fmt": DEFAULT_ME_FMT,
        "author_format": normalize_author_format(author_format),
    }


def _starter_markers() -> dict[str, Any]:
    return {
        "highlight": {
            "symbol": r"\textbf{\color{myblue} \small \ddagger}",
            "description": "Selected publications",
            "bibcodes": [],
        },
        "student": {
            "symbol": "*",
            "description": "Student-led publications",
            "bibcodes": [],
        },
    }


def _starter_output() -> dict[str, Any]:
    return {
        "dir": ".",
        "filename": "publ.tex",
        "journal_legend": True,
        "latex": {
            "list_env": "itemize",
            "left_margin": "-14em",
            "item_sep": "6pt",
        },
    }


def make_starter_config(
    query: str,
    first_author_prefix: str,
    me_target: str,
    me_display: str,
    *,
    orcid: str,
    use_orcid: bool,
    debut_year: int | None,
    retire_year: int | None,
    author_format: str,
) -> dict[str, Any]:
    """Create a starter config payload for ``adsapi cv init``."""
    return {
        "author": _starter_author(
            query,
            first_author_prefix,
            me_target,
            me_display,
            orcid=orcid,
            use_orcid=use_orcid,
            debut_year=debut_year,
            retire_year=retire_year,
        ),
        "format": _starter_format(author_format),
        "markers": _starter_markers(),
        "status": {},
        "output": _starter_output(),
    }
