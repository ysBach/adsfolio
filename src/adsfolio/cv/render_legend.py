"""Legend and section-label helpers for CV renderers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..formatting import html_to_latex
from .config import CVConfig

if TYPE_CHECKING:
    from .renderer import RenderState


def style_header_label(style: str, section: str) -> str:
    """Build LaTeX section label with style-specific item formatting."""
    if style == "compact":
        return rf"\item[] \textbf{{{section}}}"
    return rf"\item[\textbf{{{section}}}]"


def marker_summary_latex(cfg: CVConfig, state: RenderState) -> str:
    """Build one-line LaTeX marker summary text."""
    fragments: list[str] = []
    for name, marker in cfg.markers.items():
        count = state.marker_counts.get(name, 0)
        if count <= 0 or not marker.symbol:
            continue
        description = marker.description or name.replace("_", " ")
        fragments.append(f"{marker.symbol}: {description} (total {count}).")
    npr_count = len(state.non_peer_bibcodes)
    if npr_count > 0 and "non_peer_reviewed" not in cfg.markers:
        fragments.append(
            rf"\textsuperscript{{NPR}}: Non-peer-reviewed (total {npr_count})."
        )
    return " ".join(fragments).strip()


def marker_legend_markdown(cfg: CVConfig, state: RenderState) -> list[str]:
    """Build Markdown marker legend lines."""
    lines: list[str] = []
    for name, marker in cfg.markers.items():
        count = state.marker_counts.get(name, 0)
        if count <= 0 or not marker.symbol:
            continue
        description = marker.description or name.replace("_", " ")
        lines.append(f"- `{marker.symbol}`: {description} (total {count})")
    npr_count = len(state.non_peer_bibcodes)
    if npr_count > 0 and "non_peer_reviewed" not in cfg.markers:
        lines.append(f"- `[NPR]`: Non-peer-reviewed (total {npr_count})")
    return lines


def journal_legend_lines(state: RenderState, *, latex: bool) -> list[str]:
    """Build journal legend lines in LaTeX or Markdown format."""
    entries = sorted(state.bibstem_to_journal.items())
    if not entries:
        return []
    if latex:
        joined = "~;~~".join(
            rf"{{\footnotesize \textbf{{{html_to_latex(stem)}}}: {journal} }}"
            for stem, journal in entries
        )
        return [rf"\item[*]{joined}"]
    return [f"- `{stem}`: {journal}" for stem, journal in entries]
