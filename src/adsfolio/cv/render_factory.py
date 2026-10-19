"""Renderer factory and style previews for ``adsfolio cv``."""

from __future__ import annotations

from .renderer import CVRenderer, LatexRenderer, MarkdownRenderer

STYLE_EXAMPLES = {
    "compact": r"\item[] \href{...}{\footnotesize \texttt{BIBCODE}}, N authors. Cited: C.\par Authors (Year) {\textit{``Title,'' \textbf{JOUR}, \textit{VOL}, PAGE.}}",
    "detailed": r"\item Authors (Year) ``Title,'' \textbf{JOUR}, \textit{VOL}, PAGE.\\ ADS: \href{...}{\small \texttt{BIBCODE}}",
    "bibcode-first": r"\item[\href{...}{\small \texttt{BIBCODE}}] Authors (Year) {\textit{``Title,'' \textbf{JOUR}, \textit{VOL}, PAGE. { Cited: C. }}}",
}


def style_examples() -> dict[str, str]:
    """Return minimal style previews for ``adsfolio cv show-styles``."""
    return dict(STYLE_EXAMPLES)


def create_renderer(output_format: str, style: str) -> CVRenderer:
    """Create renderer implementation for the selected output format."""
    if output_format == "latex":
        return LatexRenderer(style=style)
    if output_format == "markdown":
        return MarkdownRenderer(style=style)
    raise ValueError(f"Unsupported output format: {output_format}")
