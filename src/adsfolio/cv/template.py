"""User-defined CV templates for LaTeX, HTML, Markdown, or plain text.

A template has five parts: a header, a section start, one entry per
publication, a section end, and a footer. The header and footer appear once.
The section parts appear around each of the two sections. Each part is text with ``{name}`` placeholders.
Only the names listed in `HEADER_FIELDS`, `SECTION_FIELDS`, and
`ENTRY_FIELDS` are replaced. All other braces stay as written, so LaTeX such
as ``\\textit{{title}}`` or ``\\begin{itemize}`` needs no doubled braces.

Values that come from ADS are escaped for the output format chosen by
`CVTemplate.escape`. Text that the user writes is never escaped: the template
itself, the name-highlight markup (``me_fmt``), the author separator, status
notes, and marker symbols.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from ..client import ADSClient
from ..formatting import format_author_list_cv, html_to_latex, html_to_text
from ..journal_utils import format_journal

if TYPE_CHECKING:
    from .config import CVConfig
    from .renderer import RenderState

__all__ = [
    "ENTRY_FIELDS",
    "FIELD_HELP",
    "HEADER_FIELDS",
    "SECTION_FIELDS",
    "TEMPLATE_ESCAPES",
    "TEMPLATE_PRESETS",
    "CVTemplate",
    "TemplatePreset",
    "fill_template",
    "render_template_document",
]

TEMPLATE_ESCAPES = ("none", "latex", "html")
HEADER_FIELDS = ("me", "date", "total", "first_count", "co_count")
SECTION_FIELDS = ("section", "count")
ENTRY_FIELDS = (
    "index",
    "authors",
    "title",
    "year",
    "journal",
    "volume",
    "page",
    "venue",
    "bibcode",
    "url",
    "doi",
    "citations",
    "n_authors",
    "status",
    "markers",
)
FIELD_HELP = {
    "me": "Your display name",
    "date": "Generation date (YYYY-MM-DD)",
    "total": "Number of publications",
    "first_count": "Number of first-authored publications",
    "co_count": "Number of co-authored publications",
    "section": "Section name: First-authored or Co-authored",
    "count": "Number of publications in the section",
    "index": "Position in the section, starting at 1",
    "authors": "Author list with your name highlighted",
    "title": "Title",
    "year": "Year",
    "journal": "Journal name",
    "volume": "Volume",
    "page": "Page or article number",
    "venue": "Journal, volume, and page, without empty parts",
    "bibcode": "ADS bibcode",
    "url": "ADS abstract page",
    "doi": "DOI",
    "citations": "Citation count",
    "n_authors": "Number of authors",
    "status": "Your status note for this bibcode",
    "markers": "Marker symbols for this bibcode",
}
SECTION_LABELS = ("First-authored", "Co-authored")
UNGROUPED_LABEL = "Publications"


@dataclass(frozen=True)
class CVTemplate:
    """Text templates and the escaping rule for one output format.

    Parameters
    ----------
    header, section, entry, section_end, footer : `str`
        Template text. Empty parts are omitted from the output. `section` and
        `section_end` surround each section, for example to open and close a
        list.
    escape : `str`
        ``"latex"``, ``"html"``, or ``"none"``. Selects how ADS values are
        escaped before they replace placeholders.
    group : `bool`
        `True` splits the list into First-authored and Co-authored sections.
        `False` makes one section, named Publications, in the input order.
    """

    header: str = ""
    section: str = ""
    entry: str = ""
    section_end: str = ""
    footer: str = ""
    escape: str = "none"
    group: bool = True

    def __post_init__(self) -> None:
        if self.escape not in TEMPLATE_ESCAPES:
            choices = ", ".join(TEMPLATE_ESCAPES)
            raise ValueError(f"template.escape must be one of: {choices}.")


@dataclass(frozen=True)
class TemplatePreset:
    """A complete starting point for one output format."""

    label: str
    template: CVTemplate
    me_fmt: str
    author_sep: str
    filename: str


TEMPLATE_PRESETS: dict[str, TemplatePreset] = {
    "latex": TemplatePreset(
        label="LaTeX",
        template=CVTemplate(
            header="% Publications of {me} from NASA ADS, {date}.",
            section="\\subsection*{{section} ({count})}\n\\begin{enumerate}",
            entry="\\item {authors} ({year}) ``{title},'' \\textit{{venue}}. "
            "\\href{{url}}{\\texttt{{bibcode}}} {markers}",
            section_end="\\end{enumerate}",
            escape="latex",
        ),
        me_fmt=r"\textbf{{{}}}",
        author_sep="; ",
        filename="publications.tex",
    ),
    "markdown": TemplatePreset(
        label="Markdown",
        template=CVTemplate(
            header="# Publications of {me}\n\nFrom NASA ADS, {date}.",
            section="\n## {section} ({count})\n",
            entry="{index}. {authors} ({year}). *{title}*. {venue}. "
            "[{bibcode}]({url}) {markers}",
            escape="none",
        ),
        me_fmt="**{}**",
        author_sep="; ",
        filename="publications.md",
    ),
    "html": TemplatePreset(
        label="HTML",
        template=CVTemplate(
            header="<h1>Publications of {me}</h1>\n<p>From NASA ADS, {date}.</p>",
            section="<h2>{section} ({count})</h2>\n<ol>",
            entry="<li>{authors} ({year}). <i>{title}</i>. {venue}. "
            '<a href="{url}">{bibcode}</a> {markers}</li>',
            section_end="</ol>",
            escape="html",
        ),
        me_fmt="<b>{}</b>",
        author_sep="; ",
        filename="publications.html",
    ),
    "text": TemplatePreset(
        label="Plain text",
        template=CVTemplate(
            header="Publications of {me} (NASA ADS, {date})",
            section="\n{section} ({count})\n",
            entry="[{index}] {authors} ({year}) {title}. {venue}. {url}",
            escape="none",
        ),
        me_fmt="*{}*",
        author_sep="; ",
        filename="publications.txt",
    ),
}


def _placeholder_pattern(names: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile(r"\{(" + "|".join(map(re.escape, names)) + r")\}")


_PATTERNS = {
    names: _placeholder_pattern(names)
    for names in (HEADER_FIELDS, SECTION_FIELDS, ENTRY_FIELDS)
}


def fill_template(text: str, values: Mapping[str, str]) -> str:
    """Replace known ``{name}`` placeholders in `text` with `values`.

    Placeholders whose names are not keys of `values` stay unchanged.
    """
    if not text:
        return ""
    pattern = _PATTERNS.get(tuple(values)) or _placeholder_pattern(tuple(values))
    return pattern.sub(lambda match: values[match.group(1)], text)


def _escaper(escape: str) -> Callable[[str], str]:
    """Return the function that prepares ADS text for the output format."""
    if escape == "latex":
        return html_to_latex
    if escape == "html":
        return lambda text: html.escape(html_to_text(text))
    return html_to_text


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def _authors(doc: Mapping[str, Any], cfg: CVConfig, escape: str) -> str:
    override = str(doc.get("_adsfolio_author_text", "")).strip()
    if override:
        return _escaper(escape)(override)
    names = _as_str_list(doc.get("author"))
    target = cfg.me_target
    if escape == "html":
        # Escape names before the user's markup wraps the matching name.
        names = [html.escape(name) for name in names]
        target = html.escape(target)
    return format_author_list_cv(
        names,
        n_max=cfg.n_max_authors,
        n_min=cfg.n_min_authors,
        n_me=cfg.n_me,
        me_target=target,
        me_fmt=cfg.me_fmt,
        authorsep=cfg.author_sep,
        latex=escape == "latex",
    )


def _entry_values(
    doc: Mapping[str, Any],
    cfg: CVConfig,
    state: RenderState,
    index: int,
) -> dict[str, str]:
    from .query import get_first_item
    from .renderer import _bibstem_for_legend, _is_non_peer_reviewed, _marker_symbols

    escape = cfg.template.escape
    esc = _escaper(escape)
    bibcode = str(get_first_item(doc, "bibcode") or "").strip()
    journal = format_journal(
        str(get_first_item(doc, "bibstem") or "").strip(),
        fmt="full",
        latex=escape == "latex",
    )
    is_arxiv = journal.lower() == "arxiv e-prints"
    if is_arxiv:
        journal = "arXiv"
    elif not journal:
        # Records without an ADS journal code keep their publication name.
        journal = esc(str(get_first_item(doc, "pub") or ""))
    elif escape == "html":
        journal = html.escape(journal)
    bibstem = _bibstem_for_legend(dict(doc), bibcode) if bibcode else ""
    if bibstem and journal:
        state.bibstem_to_journal.setdefault(bibstem, journal)
    markers = _marker_symbols(
        bibcode,
        _is_non_peer_reviewed(dict(doc), is_arxiv=is_arxiv),
        cfg,
        state,
        escape == "latex",
    )
    doi = str(get_first_item(doc, "doi") or "").strip()
    if bibcode:
        url = ADSClient.abstract_url(bibcode)
    elif doi:
        url = f"https://doi.org/{doi}"
    else:
        url = ""
    authors = _as_str_list(doc.get("author"))
    volume = esc(str(get_first_item(doc, "volume") or ""))
    page = esc(str(get_first_item(doc, "page") or ""))
    if is_arxiv:
        venue = page or journal
    else:
        numbers = ", ".join(part for part in (volume, page) if part)
        venue = " ".join(part for part in (journal, numbers) if part)
    return {
        "index": str(index),
        "authors": _authors(doc, cfg, escape),
        "title": esc(str(get_first_item(doc, "title") or "(untitled)")).strip(),
        "year": esc(str(doc.get("year") or "")),
        "journal": journal,
        "volume": volume,
        "page": page,
        "venue": venue,
        "bibcode": esc(bibcode),
        "url": html.escape(url) if escape == "html" else url,
        "doi": esc(doi),
        "citations": str(doc.get("citation_count") or 0),
        "n_authors": str(doc.get("author_count") or len(authors)),
        "status": str(cfg.status.get(bibcode, "")).strip(),
        "markers": " ".join(symbol for symbol in markers if symbol),
    }


def render_template_document(
    first_docs: list[dict[str, Any]],
    co_docs: list[dict[str, Any]],
    cfg: CVConfig,
) -> str:
    """Render both publication sections with the configured template.

    Raises
    ------
    ValueError
        If the configuration has no entry template.
    """
    from .renderer import RenderState

    template = cfg.template
    if not template.entry.strip():
        raise ValueError("template.entry is required for the template format.")
    me = cfg.me_display
    if template.escape == "latex":
        me = html_to_latex(me)
    elif template.escape == "html":
        me = html.escape(me)
    summary = {
        "me": me,
        "date": f"{datetime.now(timezone.utc).astimezone():%Y-%m-%d}",
        "total": str(len(first_docs) + len(co_docs)),
        "first_count": str(len(first_docs)),
        "co_count": str(len(co_docs)),
    }
    state = RenderState()
    parts = [fill_template(template.header, summary)]
    if template.group:
        sections = tuple(zip(SECTION_LABELS, (first_docs, co_docs), strict=True))
    else:
        sections = ((UNGROUPED_LABEL, [*first_docs, *co_docs]),)
    for label, docs in sections:
        parts.append(
            fill_template(template.section, {"section": label, "count": str(len(docs))})
        )
        for index, doc in enumerate(docs, start=1):
            parts.append(
                fill_template(template.entry, _entry_values(doc, cfg, state, index))
            )
        parts.append(
            fill_template(
                template.section_end, {"section": label, "count": str(len(docs))}
            )
        )
    parts.append(fill_template(template.footer, summary))
    return "\n".join(part.rstrip() for part in parts if part.strip()) + "\n"
