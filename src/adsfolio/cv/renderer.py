"""Render ADS publication docs into LaTeX or Markdown CV fragments."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from ..client import ADSClient
from ..formatting import format_author_list_cv, format_title_latex, html_to_latex
from ..journal_utils import format_journal
from .config import SUPPORTED_STYLES, CVConfig
from .query import get_first_item
from .render_legend import (
    journal_legend_lines,
    marker_legend_markdown,
    marker_summary_latex,
    style_header_label,
)

NON_PEER_MARKER_KEYWORDS = {"non_peer", "non-peer", "non_peer_reviewed", "nonpeer"}


@dataclass
class RenderState:
    """Mutable state accumulated while rendering a document."""

    bibstem_to_journal: dict[str, str] = field(default_factory=dict)
    non_peer_bibcodes: set[str] = field(default_factory=set)
    marker_counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class PublicationRow:
    """Normalized publication view model used by renderers."""

    bibcode_raw: str
    bibcode_text: str
    ads_url: str
    title: str
    authors: str
    journal: str
    volume: str
    page: str
    year: str
    n_authors: int
    cited_count: int
    status: str
    marker_symbols: tuple[str, ...]
    is_arxiv: bool

    @property
    def marker_label(self) -> str:
        return " ".join(symbol for symbol in self.marker_symbols if symbol).strip()


class CVRenderer(Protocol):
    """Protocol shared by all renderer implementations."""

    def render_publication(
        self,
        doc: dict[str, Any],
        cfg: CVConfig,
        state: RenderState,
    ) -> str: ...

    def render_document(
        self,
        first_docs: list[dict[str, Any]],
        co_docs: list[dict[str, Any]],
        cfg: CVConfig,
    ) -> str: ...


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _non_peer_marker_key(cfg: CVConfig) -> str | None:
    for name in cfg.markers:
        normalized = name.lower().replace(" ", "_")
        if normalized in NON_PEER_MARKER_KEYWORDS:
            return name
    return None


def _increment_count(state: RenderState, key: str) -> None:
    state.marker_counts[key] = state.marker_counts.get(key, 0) + 1


def _marker_symbols(
    bibcode: str,
    is_non_peer_reviewed: bool,
    cfg: CVConfig,
    state: RenderState,
    latex: bool,
) -> tuple[str, ...]:
    symbols: list[str] = []
    non_peer_key = _non_peer_marker_key(cfg)
    for name, marker in cfg.markers.items():
        if name == non_peer_key:
            continue
        if bibcode in marker.bibcodes and marker.symbol:
            symbols.append(marker.symbol)
            _increment_count(state, name)

    if is_non_peer_reviewed:
        state.non_peer_bibcodes.add(bibcode)
        key = non_peer_key or "non_peer_reviewed"
        symbol = (
            cfg.markers[non_peer_key].symbol
            if non_peer_key and cfg.markers[non_peer_key].symbol
            else (r"\textsuperscript{NPR}" if latex else "[NPR]")
        )
        symbols.append(symbol)
        _increment_count(state, key)

    return tuple(symbols)


def _bibstem_for_legend(doc: dict[str, Any], bibcode: str) -> str:
    bibstem = str(get_first_item(doc, "bibstem") or "").strip().replace(".", "")
    if bibstem:
        return bibstem
    if len(bibcode) >= 9:
        return bibcode[4:9].replace(".", "")
    return ""


def _author_text(
    doc: dict[str, Any], cfg: CVConfig, *, latex: bool
) -> tuple[list[str], str]:
    override = str(doc.get("_adsfolio_author_text", "")).strip()
    if override:
        return _as_str_list(doc.get("author")), override

    authors = _as_str_list(doc.get("author"))
    text = format_author_list_cv(
        authors,
        n_max=cfg.n_max_authors,
        n_min=cfg.n_min_authors,
        n_me=cfg.n_me,
        me_target=cfg.me_target,
        me_fmt=cfg.me_fmt,
        authorsep=cfg.author_sep,
        latex=latex,
    )
    return authors, text


def _journal_and_arxiv(doc: dict[str, Any], *, latex: bool) -> tuple[str, bool]:
    bibstem_raw = str(get_first_item(doc, "bibstem") or "").strip()
    journal = format_journal(bibstem_raw, fmt="full", latex=latex)
    if journal.lower() == "arxiv e-prints":
        return "arXiv", True
    return journal, False


def _is_non_peer_reviewed(doc: dict[str, Any], *, is_arxiv: bool) -> bool:
    properties = {entry.upper() for entry in _as_str_list(doc.get("property"))}
    return ("NOT REFEREED" in properties) and not is_arxiv


def _normalize_row(
    doc: dict[str, Any],
    cfg: CVConfig,
    state: RenderState,
    *,
    latex: bool,
) -> PublicationRow | None:
    bibcode_raw = str(get_first_item(doc, "bibcode") or "").strip()
    if not bibcode_raw:
        return None
    title_raw = str(get_first_item(doc, "title") or "(untitled)")
    title = format_title_latex(title_raw).replace("\n", " ").strip()
    authors, author_text = _author_text(doc, cfg, latex=latex)
    journal, is_arxiv = _journal_and_arxiv(doc, latex=latex)
    is_non_peer_reviewed = _is_non_peer_reviewed(doc, is_arxiv=is_arxiv)
    marker_symbols = _marker_symbols(
        bibcode_raw,
        is_non_peer_reviewed,
        cfg,
        state,
        latex,
    )
    bibstem = _bibstem_for_legend(doc, bibcode_raw)
    if bibstem and journal:
        state.bibstem_to_journal.setdefault(bibstem, journal)
    volume = str(get_first_item(doc, "volume") or "")
    page = str(get_first_item(doc, "page") or "")
    year = str(doc.get("year") or "")
    n_authors = _to_int(doc.get("author_count"), default=len(authors))
    cited_count = _to_int(doc.get("citation_count"), default=0)
    status = str(cfg.status.get(bibcode_raw, "")).strip()
    return PublicationRow(
        bibcode_raw=bibcode_raw,
        bibcode_text=html_to_latex(bibcode_raw),
        ads_url=ADSClient.abstract_url(bibcode_raw),
        title=title,
        authors=author_text,
        journal=journal,
        volume=volume,
        page=page,
        year=year,
        n_authors=n_authors,
        cited_count=cited_count,
        status=status,
        marker_symbols=marker_symbols,
        is_arxiv=is_arxiv,
    )


def _latex_ref_base(row: PublicationRow) -> str:
    if row.is_arxiv:
        page = html_to_latex(row.page) if row.page else "e-print"
        return rf"\textit{{{page}}}."
    bibstem = html_to_latex(row.bibcode_raw[4:9].replace(".", ""))
    vol = html_to_latex(row.volume) if row.volume else "?"
    page = html_to_latex(row.page) if row.page else "?"
    return rf"\textbf{{{bibstem}}}, \textit{{{vol}}}, {page}."


def _markdown_venue(row: PublicationRow) -> str:
    parts = [row.journal, row.volume, row.page]
    clean = [part for part in parts if part]
    return ", ".join(clean) if clean else "Unknown venue"


class LatexRenderer:
    """Render publications as a LaTeX fragment."""

    def __init__(self, style: str):
        if style not in SUPPORTED_STYLES:
            raise ValueError(f"Unsupported style: {style}")
        self._style = style

    def _render_bibcode_first(self, row: PublicationRow) -> str:
        label = f"{row.marker_label} ".strip()
        link = rf"\href{{{row.ads_url}}}{{\small \texttt{{{row.bibcode_text}}}}}"
        ref = _latex_ref_base(row)
        citation = rf"{{ Cited: {row.cited_count}. }}"
        if row.authors.endswith("et al."):
            citation = rf"{{ {row.n_authors} authors. }} {citation}"
        return (
            rf"\item[{label}{link}] {row.authors} ({row.year}) "
            rf"{{\textit{{``{row.title},'' {ref} {citation}}}}}"
        )

    def _render_detailed(self, row: PublicationRow) -> str:
        marker = row.marker_label
        marker_text = f"{marker} " if marker else ""
        ref = _latex_ref_base(row)
        first_line = (
            rf"\item {marker_text}{row.authors} ({row.year}) "
            rf"``{row.title},'' {ref}\\"
        )
        second_line = (
            rf"ADS: \href{{{row.ads_url}}}{{\small \texttt{{{row.bibcode_text}}}}}"
        )
        if row.status:
            second_line += rf" [{row.status}]"
        return f"{first_line}\n{second_line}"

    def _render_compact(self, row: PublicationRow) -> str:
        status = rf"~[{row.status}]" if row.status else ""
        label = row.marker_label
        label_text = f"{label}" if label else ""
        line1 = (
            rf"\item[] \href{{{row.ads_url}}}{{\footnotesize \texttt{{{row.bibcode_text}}}}}, "
            rf"{row.n_authors} authors. Cited: {row.cited_count}{status}.{label_text}"
        )
        line2 = (
            rf"{row.authors} ({row.year}) "
            rf"{{\textit{{``{row.title},'' {_latex_ref_base(row)}}}}}"
        )
        return line1 + r"\par " + line2

    def render_publication(
        self,
        doc: dict[str, Any],
        cfg: CVConfig,
        state: RenderState,
    ) -> str:
        row = _normalize_row(doc, cfg, state, latex=True)
        if row is None:
            return r"\item[] [missing bibcode] Incomplete ADS record."
        if self._style == "bibcode-first":
            return self._render_bibcode_first(row)
        if self._style == "detailed":
            return self._render_detailed(row)
        return self._render_compact(row)

    def render_document(
        self,
        first_docs: list[dict[str, Any]],
        co_docs: list[dict[str, Any]],
        cfg: CVConfig,
    ) -> str:
        state = RenderState()
        lines = [
            (
                "The contents are based on the NASA ADS database "
                f"({datetime.now():%Y %b}) and may differ from Google Scholar."
            )
        ]
        lines.append(
            rf"\begin{{{cfg.latex_list_env}}}[leftmargin={cfg.latex_left_margin}, parsep=0pt, itemsep={cfg.latex_item_sep}]"
        )

        lines.append(
            f"{style_header_label(self._style, 'FIRST authored')} Total {len(first_docs)} entries"
        )
        for doc in first_docs:
            lines.append(self.render_publication(doc, cfg, state))

        lines.append(r"\item[------] " + "---" * 24)
        lines.append(
            f"{style_header_label(self._style, 'CO-authored')} Total {len(co_docs)} entries"
        )
        for doc in co_docs:
            lines.append(self.render_publication(doc, cfg, state))

        marker_summary = marker_summary_latex(cfg, state)
        if marker_summary:
            lines.insert(1, marker_summary)
        if cfg.journal_legend:
            lines.extend(journal_legend_lines(state, latex=True))
        lines.append(rf"\end{{{cfg.latex_list_env}}}")
        return "\n\n".join(lines).strip() + "\n"


class MarkdownRenderer:
    """Render publications as Markdown."""

    def __init__(self, style: str):
        if style not in SUPPORTED_STYLES:
            raise ValueError(f"Unsupported style: {style}")
        self._style = style

    def render_publication(
        self,
        doc: dict[str, Any],
        cfg: CVConfig,
        state: RenderState,
    ) -> str:
        row = _normalize_row(doc, cfg, state, latex=False)
        if row is None:
            return "- [missing bibcode] Incomplete ADS record."
        marker_text = f" [{' '.join(row.marker_symbols)}]" if row.marker_symbols else ""
        status_text = f" [{row.status}]" if row.status else ""
        return (
            f"- [`{row.bibcode_raw}`]({row.ads_url}){marker_text}: **{row.title}**. "
            f"{row.authors} ({row.year}). *{_markdown_venue(row)}*. "
            f"{row.n_authors} authors, cited {row.cited_count}.{status_text}"
        )

    def render_document(
        self,
        first_docs: list[dict[str, Any]],
        co_docs: list[dict[str, Any]],
        cfg: CVConfig,
    ) -> str:
        state = RenderState()
        lines = [
            f"# Publications (ADS snapshot: {datetime.now():%Y-%m-%d})",
            f"## First-authored ({len(first_docs)})",
        ]
        lines.extend(self.render_publication(doc, cfg, state) for doc in first_docs)
        lines.append("")
        lines.append(f"## Co-authored ({len(co_docs)})")
        lines.extend(self.render_publication(doc, cfg, state) for doc in co_docs)

        marker_legend = marker_legend_markdown(cfg, state)
        if marker_legend:
            lines.append("")
            lines.append("## Marker legend")
            lines.extend(marker_legend)

        if cfg.journal_legend:
            journal_lines = journal_legend_lines(state, latex=False)
            if journal_lines:
                lines.append("")
                lines.append("## Journal legend")
                lines.extend(journal_lines)
        return "\n".join(lines).strip() + "\n"
