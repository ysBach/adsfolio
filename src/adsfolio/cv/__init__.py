"""Public API for CV publication list generation."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Literal

from ..client import ADSClient
from .config import CVConfig, load_config
from .query import (
    apply_author_format_overrides,
    classify_publications,
    fetch_publications,
)
from .render_factory import create_renderer

__all__ = ["CVConfig", "generate_cv", "load_config", "render_cv"]


def render_cv(docs: list[dict], cfg: CVConfig) -> str:
    """Render already-fetched ADS documents without network access.

    Parameters
    ----------
    docs : list of dict
        ADS search documents with the fields in ``query.CV_FIELDS``.
    cfg : `CVConfig`
        Output format, style, template, and author settings.

    Returns
    -------
    str
        The rendered publication list.
    """
    first_docs, co_docs = classify_publications(docs, cfg.first_author_prefix)
    renderer = create_renderer(cfg.output_format, cfg.style)
    return renderer.render_document(first_docs, co_docs, cfg)


def generate_cv(
    client: ADSClient,
    cfg: CVConfig,
    *,
    output_path: Path | None = None,
    output_format: Literal["latex", "markdown", "template"] | None = None,
    style: str | None = None,
    write_output: bool = True,
) -> str:
    """Generate CV publication output and optionally write it to disk."""
    effective_cfg = cfg
    if output_format is not None:
        effective_cfg = replace(effective_cfg, output_format=output_format)
    if style is not None:
        effective_cfg = replace(effective_cfg, style=style)

    docs = fetch_publications(client, effective_cfg)
    docs = apply_author_format_overrides(docs, client, effective_cfg)
    rendered = render_cv(docs, effective_cfg)

    target_path = output_path or effective_cfg.output_path
    if write_output:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(rendered, encoding="utf-8")

    return rendered
