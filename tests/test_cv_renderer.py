from pathlib import Path

from adsapi.cv.config import CVConfig, MarkerConfig
from adsapi.cv.render_factory import create_renderer, style_examples


def _cfg(output_format: str, style: str) -> CVConfig:
    return CVConfig(
        query='author:"doe, jane"',
        orcid="",
        use_orcid=False,
        debut_year=None,
        retire_year=None,
        exclude_bibstems=(),
        first_author_prefix="doe, j",
        me_target="Doe, J.",
        me_display="Jane Doe",
        output_format=output_format,  # type: ignore[arg-type]
        style=style,
        n_max_authors=5,
        n_min_authors=3,
        n_me=6,
        author_sep="; ",
        me_fmt=r"\textbf{{{}}}",
        author_format="cv_default",
        markers={
            "highlight": MarkerConfig(
                symbol="[H]",
                description="Selected",
                bibcodes=frozenset({"2024ApJ...900..100S"}),
            )
        },
        status={"2024ApJ...900..100S": "ACCEPTED"},
        output_dir=Path("."),
        output_filename="publ.tex",
        journal_legend=True,
        latex_list_env="itemize",
        latex_left_margin="-14em",
        latex_item_sep="6pt",
    )


def _doc(bibcode: str, first_author: str) -> dict:
    return {
        "bibcode": bibcode,
        "title": ["Sample title"],
        "author": ["Doe, Jane", "Smith, John", "Roe, Alice"],
        "first_author": [first_author],
        "year": "2024",
        "author_count": 3,
        "citation_count": 12,
        "bibstem": ["ApJ"],
        "volume": "900",
        "page": ["100"],
        "property": [],
    }


def test_latex_renderer_compact_contains_sections(monkeypatch) -> None:
    monkeypatch.setattr(
        "adsapi.cv.renderer.format_journal",
        lambda journal, fmt, latex: "The Astrophysical Journal",
    )
    cfg = _cfg("latex", "compact")
    renderer = create_renderer("latex", "compact")

    first_docs = [_doc("2024ApJ...900..100S", "Doe, Jane")]
    co_docs = [_doc("2023ApJ...800..090A", "Smith, John")]
    output = renderer.render_document(first_docs, co_docs, cfg)

    assert r"\textbf{FIRST authored}" in output
    assert r"\textbf{CO-authored}" in output
    assert "2024ApJ...900..100S" in output
    assert "ACCEPTED" in output
    assert "[H]" in output
    assert r"\item[*]" in output


def test_markdown_renderer_includes_status_and_journal_legend(monkeypatch) -> None:
    monkeypatch.setattr(
        "adsapi.cv.renderer.format_journal",
        lambda journal, fmt, latex: "The Astrophysical Journal",
    )
    cfg = _cfg("markdown", "detailed")
    renderer = create_renderer("markdown", "detailed")

    first_docs = [_doc("2024ApJ...900..100S", "Doe, Jane")]
    output = renderer.render_document(first_docs, [], cfg)

    assert output.startswith("# Publications")
    assert "First-authored (1)" in output
    assert "[H]" in output
    assert "[ACCEPTED]" in output
    assert "## Journal legend" in output
    assert "`ApJ`" in output


def test_style_examples_cover_all_styles() -> None:
    previews = style_examples()
    assert set(previews) == {"compact", "detailed", "bibcode-first"}


def test_renderer_prefers_ads_custom_author_override(monkeypatch) -> None:
    monkeypatch.setattr(
        "adsapi.cv.renderer.format_journal",
        lambda journal, fmt, latex: "The Astrophysical Journal",
    )
    cfg = _cfg("latex", "compact")
    renderer = create_renderer("latex", "compact")

    docs = [_doc("2024ApJ...900..100S", "Doe, Jane")]
    docs[0]["_adsapi_author_text"] = "Custom, Author and Team"
    output = renderer.render_document(docs, [], cfg)
    assert "Custom, Author and Team" in output
