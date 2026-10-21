"""Renderer factory and style previews for ``adsfolio cv``."""

from __future__ import annotations

from typing import Any

from .config import CVConfig
from .renderer import CVRenderer, LatexRenderer, MarkdownRenderer
from .template import render_template_document

STYLE_EXAMPLES = {
    "compact": r"\item[] \href{...}{\footnotesize \texttt{BIBCODE}}, N authors. Cited: C.\par Authors (Year) {\textit{``Title,'' \textbf{JOUR}, \textit{VOL}, PAGE.}}",
    "detailed": r"\item Authors (Year) ``Title,'' \textbf{JOUR}, \textit{VOL}, PAGE.\\ ADS: \href{...}{\small \texttt{BIBCODE}}",
    "bibcode-first": r"\item[\href{...}{\small \texttt{BIBCODE}}] Authors (Year) {\textit{``Title,'' \textbf{JOUR}, \textit{VOL}, PAGE. { Cited: C. }}}",
}


def style_examples() -> dict[str, str]:
    """Return minimal style previews for ``adsfolio cv show-styles``."""
    return dict(STYLE_EXAMPLES)


class TemplateRenderer:
    """Render publications with the configured `CVTemplate`."""

    def render_document(
        self,
        first_docs: list[dict[str, Any]],
        co_docs: list[dict[str, Any]],
        cfg: CVConfig,
    ) -> str:
        return render_template_document(first_docs, co_docs, cfg)


def create_renderer(output_format: str, style: str) -> CVRenderer:
    """Create renderer implementation for the selected output format."""
    if output_format == "latex":
        return LatexRenderer(style=style)
    if output_format == "markdown":
        return MarkdownRenderer(style=style)
    if output_format == "template":
        return TemplateRenderer()
    raise ValueError(f"Unsupported output format: {output_format}")
