#!/usr/bin/env python3
"""
Build Markdown CV sections from ADS search results and an optional talks CSV.

This example is designed to mirror a thesis/CV workflow:
1. Query ADS with a custom Solr query.
2. Split publications into first-authored vs co-authored groups.
3. Optionally merge a talks spreadsheet exported as CSV.
4. Write publication/talk markdown files that are easy to convert to LaTeX.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import adsfolio


DEFAULT_QUERY = (
    'author:"doe, jane" '
    "year:2010- "
    "bibstem:(- zndo) bibstem:(- yCat) bibstem:(- EPSC) bibstem:(- cosp) "
    "bibstem:(- AGUFM) bibstem:(- BKAS) bibstem:(- LPICo) bibstem:(- IAUFM) "
    "bibstem:(- AAS)"
)

DEFAULT_FIELDS = [
    "bibcode",
    "title",
    "author",
    "first_author",
    "year",
    "date",
    "author_count",
    "citation_count",
    "bibstem",
    "volume",
    "issue",
    "page",
    "doi",
    "property",
    "comment",
]

MONTHS = {
    1: "Jan",
    2: "Feb",
    3: "Mar",
    4: "Apr",
    5: "May",
    6: "Jun",
    7: "Jul",
    8: "Aug",
    9: "Sep",
    10: "Oct",
    11: "Nov",
    12: "Dec",
}


@dataclass(frozen=True)
class RenderConfig:
    highlight_marker: str = "[H]"
    student_marker: str = "[S]"
    non_peer_reviewed_marker: str = "[NPR]"
    n_min_author: int = 3
    n_me: int = 6
    me_target: str = "Doe, J."
    me_fmt: str = "**{}**"


HIGHLIGHT_BIBCODES = {
    "2024A&A...684A..80B",
    "2024A&A...684A..81B",
    "2021A&A...654A.113B",
    "2025arXiv251207318L",
    "2025ApJS..281...10C",
    "2022MNRAS.509.4128I",
    "2018NatCo...9.2486I",
}

STUDENT_BIBCODES: set[str] = set()

STATUS_BY_BIBCODE = {
    "2025arXiv251207318L": "ACCEPTED by ApJL",
    "2026arXiv260108591C": "ACCEPTED by ApJL",
    "2026arXiv260212930P": "ACCEPTED by ApJ",
    "2026arXiv260209139H": "ACCEPTED by ApJS",
    "2025arXiv251115823A": "Submitted to ApJS",
    "2026arXiv260208037L": "Under review at JKAS",
    "2026arXiv260203233J": "Under review at JKAS",
}


def get_first_item(doc: dict[str, Any], field: str) -> Any:
    """Return first item when ADS field is list-like, otherwise return as-is."""
    value = doc.get(field)
    if isinstance(value, list):
        return value[0] if value else None
    return value


def as_str_list(value: Any) -> list[str]:
    """Normalize ADS values to string list."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def to_int(value: Any, default: int = 0) -> int:
    """Safely cast value to int."""
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default


def classify_publications(
    docs: list[dict[str, Any]],
    first_author_prefix: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split docs into first-authored and co-authored groups."""
    first_docs: list[dict[str, Any]] = []
    co_docs: list[dict[str, Any]] = []

    prefix = first_author_prefix.lower()
    for doc in docs:
        first_author = str(get_first_item(doc, "first_author") or "").lower()
        if first_author.startswith(prefix):
            first_docs.append(doc)
        else:
            co_docs.append(doc)
    return first_docs, co_docs


def format_publication_markdown(
    doc: dict[str, Any],
    cfg: RenderConfig,
    non_peer_bibcodes: set[str],
) -> str:
    """Render a single publication line in Markdown."""
    bibcode = str(get_first_item(doc, "bibcode") or "").strip()
    if not bibcode:
        return "- [missing bibcode] Incomplete ADS record."

    title = str(get_first_item(doc, "title") or "(untitled)")
    title = adsfolio.format_title_latex(title).replace("\n", " ").strip()

    authors = as_str_list(doc.get("author"))
    author_text = adsfolio.format_author_list_cv(
        authors,
        n_max=5,
        n_min=cfg.n_min_author,
        n_me=cfg.n_me,
        me_target=cfg.me_target,
        me_fmt=cfg.me_fmt,
        authorsep="; ",
        latex=False,
    )

    journal = adsfolio.format_journal(
        str(get_first_item(doc, "bibstem") or ""),
        fmt="full",
        latex=False,
    )
    is_arxiv = journal.lower() == "arxiv e-prints"
    if is_arxiv:
        journal = "arXiv"

    properties = {p.upper() for p in as_str_list(doc.get("property"))}
    is_non_peer_reviewed = ("NOT REFEREED" in properties) and not is_arxiv
    if is_non_peer_reviewed:
        non_peer_bibcodes.add(bibcode)

    volume = get_first_item(doc, "volume")
    page = get_first_item(doc, "page")
    year = to_int(doc.get("year"), default=0)
    n_authors = to_int(doc.get("author_count"), default=len(authors))
    citations = to_int(doc.get("citation_count"), default=0)

    venue_parts = [journal]
    if volume:
        venue_parts.append(str(volume))
    if page:
        venue_parts.append(str(page))
    venue = ", ".join([part for part in venue_parts if part]) or "Unknown venue"

    markers: list[str] = []
    if bibcode in HIGHLIGHT_BIBCODES:
        markers.append(cfg.highlight_marker)
    if bibcode in STUDENT_BIBCODES:
        markers.append(cfg.student_marker)
    if is_non_peer_reviewed:
        markers.append(cfg.non_peer_reviewed_marker)
    marker_text = f" [{' '.join(markers)}]" if markers else ""

    status = STATUS_BY_BIBCODE.get(bibcode, "")
    status_text = f" [{status}]" if status else ""
    ads_url = adsfolio.ADSClient.abstract_url(bibcode)

    return (
        f"- [`{bibcode}`]({ads_url}){marker_text}: "
        f"**{title}**. {author_text} ({year}). "
        f"*{venue}*. {n_authors} authors, cited {citations}.{status_text}"
    )


def format_talk_authors(
    row: dict[str, Any],
    me_code: str,
    me_display: str,
) -> tuple[str, str]:
    """Build compact talk author string from auth1/auth2/auth3/index fields."""
    authors: list[str] = []
    index_me = to_int(row.get("index_me"), default=0)
    n_author = to_int(row.get("num_author"), default=0)

    for i in range(1, 4):
        author = str(row.get(f"auth{i}", "")).strip()
        if not author:
            continue
        if author.lower() == me_code.lower() or i == index_me:
            authors.append(f"**{me_display}**")
        else:
            authors.append(author)

    if index_me == 4:
        authors.append(f"**{me_display}**")

    if n_author > 3 and authors:
        authors[-1] = f"{authors[-1]} et al."
        suffix = f" ({n_author} authors)"
    else:
        suffix = ""

    return "; ".join(authors), suffix


def format_talk_markdown(row: dict[str, Any], me_code: str, me_display: str) -> str:
    """Render a talk line in Markdown."""
    authors, nauthor_suffix = format_talk_authors(
        row,
        me_code=me_code,
        me_display=me_display,
    )

    yyyy = to_int(row.get("yyyy"), default=0)
    mm = to_int(row.get("mm"), default=1)
    month = MONTHS.get(mm, f"M{mm}")

    city = str(row.get("city", "")).strip()
    country = str(row.get("country", "")).strip()
    place = ", ".join([p for p in [city, country] if p]) or "Unknown place"

    oral_or_poster = str(row.get("oral/poster", "")).strip().lower()
    poster_text = " (poster)" if oral_or_poster and oral_or_poster != "o" else ""

    title = str(row.get("title", "")).strip()
    conference = str(row.get("name", "")).replace("DESTINY+", "DESTINY$^+$").strip()

    return (
        f"- {authors} ({yyyy} {month}). "
        f'"*{title}*". {conference}{poster_text}, {place}{nauthor_suffix}.'
    )


def write_publications_markdown(
    out_path: Path,
    first_docs: list[dict[str, Any]],
    co_docs: list[dict[str, Any]],
    cfg: RenderConfig,
) -> None:
    """Write publication markdown output."""
    non_peer_bibcodes: set[str] = set()

    lines: list[str] = []
    lines.append("# Publications")
    lines.append("")
    lines.append("Generated from NASA ADS search results via `adsfolio`.")
    lines.append("")
    lines.append("## First-authored")
    lines.append(f"Total: {len(first_docs)}")
    lines.append("")
    for doc in first_docs:
        lines.append(
            format_publication_markdown(
                doc,
                cfg=cfg,
                non_peer_bibcodes=non_peer_bibcodes,
            )
        )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Co-authored")
    lines.append(f"Total: {len(co_docs)}")
    lines.append("")
    for doc in co_docs:
        lines.append(
            format_publication_markdown(
                doc,
                cfg=cfg,
                non_peer_bibcodes=non_peer_bibcodes,
            )
        )

    lines.append("")
    lines.append("## Markers")
    lines.append(
        f"- `{cfg.highlight_marker}` selected publications: {len(HIGHLIGHT_BIBCODES)}"
    )
    lines.append(
        f"- `{cfg.student_marker}` student-led publications: {len(STUDENT_BIBCODES)}"
    )
    lines.append(
        f"- `{cfg.non_peer_reviewed_marker}` non-peer-reviewed publications: "
        f"{len(non_peer_bibcodes)}"
    )
    lines.append("")
    lines.append(
        "Tip: this Markdown can be converted to LaTeX directly "
        "(for example with Pandoc),"
    )
    lines.append(
        "or you can swap the line formatter to emit TeX strings "
        "as in your original workflow."
    )

    out_path.write_text("\n".join(lines), encoding="utf-8")


def write_talks_markdown(
    out_path: Path,
    talks_csv_url: str,
    me_code: str,
    me_display: str,
) -> None:
    """Write talks markdown output from a CSV URL."""
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError(
            "pandas is required for talks export. Install with `uv add pandas`."
        ) from exc

    df = pd.read_csv(talks_csv_url).fillna("")
    if "date" in df.columns:
        df = df.sort_values(by=["date"], ascending=[False])
    elif {"yyyy", "mm"} <= set(df.columns):
        df = df.sort_values(by=["yyyy", "mm"], ascending=[False, False])

    records = df.to_dict(orient="records")
    invited_rows: list[dict[str, Any]] = []
    contributed_presented_rows: list[dict[str, Any]] = []
    contributed_other_rows: list[dict[str, Any]] = []

    for row in records:
        invited = str(row.get("invited", "")).strip().lower() == "invited"
        if invited:
            invited_rows.append(row)
            continue

        index_me = to_int(row.get("index_me"), default=0)
        index_presenter = to_int(row.get("index_presenter"), default=-1)
        if index_me == index_presenter:
            contributed_presented_rows.append(row)
        else:
            contributed_other_rows.append(row)

    lines: list[str] = []
    lines.append("# Talks")
    lines.append("")
    lines.append(f"Source CSV: {talks_csv_url}")
    lines.append("")
    lines.append("## Invited")
    lines.append(f"Total: {len(invited_rows)}")
    lines.append("")
    for row in invited_rows:
        lines.append(format_talk_markdown(row, me_code=me_code, me_display=me_display))
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Contributed (presented by me)")
    lines.append(f"Total: {len(contributed_presented_rows)}")
    lines.append("")
    for row in contributed_presented_rows:
        lines.append(format_talk_markdown(row, me_code=me_code, me_display=me_display))
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Contributed (co-author participation)")
    lines.append(f"Total: {len(contributed_other_rows)}")
    lines.append("")
    for row in contributed_other_rows:
        lines.append(format_talk_markdown(row, me_code=me_code, me_display=me_display))

    out_path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Build Markdown CV files from ADS and optional talks CSV.",
    )
    parser.add_argument(
        "--query",
        default=DEFAULT_QUERY,
        help="ADS Solr query string.",
    )
    parser.add_argument(
        "--rows",
        type=int,
        default=9999,
        help="Maximum number of records to fetch.",
    )
    parser.add_argument(
        "--first-author-prefix",
        default="bach, y",
        help="Prefix used to classify first-authored records.",
    )
    parser.add_argument(
        "--me-target",
        default="Bach, Y.",
        help="Author short name used for highlighting in formatted author lists.",
    )
    parser.add_argument(
        "--talks-csv-url",
        default="",
        help="Optional talks CSV URL (e.g., Google Sheets export URL).",
    )
    parser.add_argument(
        "--me-code",
        default="ypb",
        help="Code/name used in talks CSV auth fields for yourself.",
    )
    parser.add_argument(
        "--me-display",
        default="Y. P. Bach",
        help="Display name used for your highlighted author name in talks output.",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="ADS API token. If omitted, ADS_API_TOKEN/config is used.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("cv_example_output"),
        help="Directory where markdown outputs will be written.",
    )
    return parser.parse_args()


def main() -> None:
    """Run end-to-end example pipeline."""
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    cfg = RenderConfig(me_target=args.me_target)
    client = adsfolio.ADSClient(token=args.token)
    results = client.search.query_all(
        q=args.query,
        fl=DEFAULT_FIELDS,
        page_size=2000,
        sort="date desc",
        max_records=max(args.rows, 0),
    )

    first_docs, co_docs = classify_publications(
        docs=results.docs,
        first_author_prefix=args.first_author_prefix,
    )

    pub_md = args.output_dir / "publications.md"
    write_publications_markdown(
        out_path=pub_md,
        first_docs=first_docs,
        co_docs=co_docs,
        cfg=cfg,
    )

    if args.talks_csv_url:
        talks_md = args.output_dir / "talks.md"
        write_talks_markdown(
            out_path=talks_md,
            talks_csv_url=args.talks_csv_url,
            me_code=args.me_code,
            me_display=args.me_display,
        )

    index_md = args.output_dir / "cv_bundle.md"
    lines = [
        "# CV Document Bundle",
        "",
        f"- Publications: [{pub_md.name}]({pub_md.name})",
    ]
    if args.talks_csv_url:
        lines.append("- Talks: [talks.md](talks.md)")
    lines.extend(
        [
            "",
            "This bundle is Markdown-first. You can convert to LaTeX with tools like Pandoc,",
            "or replace the formatters to emit native TeX lines directly.",
        ]
    )
    index_md.write_text("\n".join(lines), encoding="utf-8")

    print(f"Wrote: {pub_md}")
    if args.talks_csv_url:
        print(f"Wrote: {args.output_dir / 'talks.md'}")
    print(f"Wrote: {index_md}")


if __name__ == "__main__":
    main()
