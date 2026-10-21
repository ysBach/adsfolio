"""User-defined CV templates: placeholders, escaping, presets, and YAML."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from adsfolio.cv import render_cv
from adsfolio.cv.config import CVConfig, MarkerConfig, load_config
from adsfolio.cv.template import TEMPLATE_PRESETS, CVTemplate, fill_template

# ADS sends titles as HTML. Plain-text and HTML output drop the markup the
# same way as the rest of the application; LaTeX output converts it.
TRICKY_TITLE = "Dust &amp; Ice in H<sub>2</sub>O comet_tails"
PLAIN_TITLE = "Dust & Ice in H2O comet_tails"


def _cfg(template: CVTemplate, **overrides) -> CVConfig:
    values = {
        "query": 'author:"doe, jane"',
        "orcid": "",
        "use_orcid": False,
        "debut_year": None,
        "retire_year": None,
        "exclude_bibstems": (),
        "first_author_prefix": "doe, j",
        "me_target": "Doe, J.",
        "me_display": "Jane Doe",
        "output_format": "template",
        "style": "compact",
        "n_max_authors": 5,
        "n_min_authors": 3,
        "n_me": 6,
        "author_sep": "; ",
        "me_fmt": "<b>{}</b>",
        "author_format": "cv_default",
        "markers": {
            "highlight": MarkerConfig(
                symbol="<sup>H</sup>",
                description="Selected",
                bibcodes=frozenset({"2024A&A...684A..80D"}),
            )
        },
        "status": {"2024A&A...684A..80D": "accepted"},
        "output_dir": Path("."),
        "output_filename": "publ.html",
        "journal_legend": False,
        "latex_list_env": "itemize",
        "latex_left_margin": "-14em",
        "latex_item_sep": "6pt",
        "template": template,
    }
    values.update(overrides)
    return CVConfig(**values)


def _doc(bibcode: str, first_author: str, title: str = TRICKY_TITLE) -> dict:
    return {
        "bibcode": bibcode,
        "title": [title],
        "author": [first_author, "O'Brien & Sons, Kim", "Roe, Alice"],
        "first_author": [first_author],
        "year": "2024",
        "author_count": 3,
        "citation_count": 7,
        "bibstem": ["A&A"],
        "volume": "684",
        "page": ["A80"],
        "doi": ["10.1051/demo"],
        "property": ["REFEREED"],
    }


DOCS = [
    _doc("2024A&A...684A..80D", "Doe, Jane"),
    _doc("2023A&A...600A...1S", "Smith, John", title="Plain title"),
]


def test_known_placeholders_are_replaced_and_other_braces_stay():
    text = r"\begin{itemize}\item \textit{{title}} {unknown} {year}"
    assert fill_template(text, {"title": "T", "year": "2024"}) == (
        r"\begin{itemize}\item \textit{T} {unknown} 2024"
    )


def test_html_escapes_ads_values_but_keeps_user_markup():
    template = CVTemplate(
        section="<h2>{section} ({count})</h2>",
        entry="<li>{authors}: <i>{title}</i> {journal} {markers} {status}</li>",
        escape="html",
    )
    output = render_cv(DOCS, _cfg(template))
    assert "Dust &amp; Ice in H2O comet_tails" in output
    assert "<b>Doe, J.</b>" in output
    assert "O&#x27;Brien &amp; Sons, K." in output
    assert "<sup>H</sup> accepted" in output
    assert "<h2>First-authored (1)</h2>" in output
    assert "<h2>Co-authored (1)</h2>" in output
    assert "<sub>" not in output


def test_latex_escapes_ads_values_but_keeps_user_markup():
    template = CVTemplate(
        entry=r"\item {authors} ``{title}'' {bibcode}", escape="latex"
    )
    cfg = _cfg(template, me_fmt=r"\textbf{{{}}}")
    output = render_cv(DOCS, cfg)
    assert r"Dust \& Ice in H$_{2}$O comet\_tails" in output
    assert r"\textbf{Doe, J.}" in output
    assert r"2024A\&A...684A..80D" in output


def test_none_escape_keeps_text_as_is():
    template = CVTemplate(entry="{index}. {title} | {doi} | {citations}")
    output = render_cv(DOCS, _cfg(template, me_fmt="*{}*"))
    assert f"1. {PLAIN_TITLE} | 10.1051/demo | 7" in output


def test_header_section_end_and_footer_placement():
    template = CVTemplate(
        header="HEAD {me} {total} {first_count} {co_count}",
        section="START {section}",
        entry="ITEM {index}",
        section_end="END {section}",
        footer="FOOT {total}",
    )
    lines = render_cv(DOCS, _cfg(template)).splitlines()
    assert lines == [
        "HEAD Jane Doe 2 1 1",
        "START First-authored",
        "ITEM 1",
        "END First-authored",
        "START Co-authored",
        "ITEM 1",
        "END Co-authored",
        "FOOT 2",
    ]


@pytest.mark.parametrize("name", sorted(TEMPLATE_PRESETS))
def test_every_preset_renders_both_sections(name):
    preset = TEMPLATE_PRESETS[name]
    cfg = _cfg(preset.template, me_fmt=preset.me_fmt, author_sep=preset.author_sep)
    output = render_cv(DOCS, cfg)
    assert "First-authored (1)" in output
    assert "Co-authored (1)" in output
    assert "{title}" not in output and "{authors}" not in output


def test_missing_entry_template_is_rejected():
    with pytest.raises(ValueError, match="template.entry"):
        render_cv(DOCS, _cfg(CVTemplate(header="only a header")))


def test_invalid_escape_is_rejected():
    with pytest.raises(ValueError, match="template.escape"):
        CVTemplate(entry="{title}", escape="rtf")


def _write(path: Path, payload: dict) -> Path:
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


def test_yaml_preset_supplies_format_defaults_and_keys_override(tmp_path):
    path = _write(
        tmp_path / "cv.yaml",
        {
            "author": {"query": 'author:"doe, jane"'},
            "format": {"output": "template"},
            "template": {"preset": "html", "entry": "<p>{title}</p>"},
        },
    )
    cfg = load_config(path)
    assert cfg.output_format == "template"
    assert cfg.template.escape == "html"
    assert cfg.template.entry == "<p>{title}</p>"
    assert cfg.template.header == TEMPLATE_PRESETS["html"].template.header
    assert cfg.me_fmt == "<b>{}</b>"
    assert cfg.output_filename == "publications.html"


def test_yaml_explicit_format_values_beat_the_preset(tmp_path):
    path = _write(
        tmp_path / "cv.yaml",
        {
            "author": {"query": "q"},
            "format": {"output": "template", "me_fmt": "<u>{}</u>"},
            "template": {"preset": "html"},
            "output": {"filename": "mine.html"},
        },
    )
    cfg = load_config(path)
    assert cfg.me_fmt == "<u>{}</u>"
    assert cfg.output_filename == "mine.html"


def test_yaml_unknown_preset_is_rejected(tmp_path):
    path = _write(
        tmp_path / "cv.yaml",
        {"author": {"query": "q"}, "template": {"preset": "rtf"}},
    )
    with pytest.raises(ValueError, match="template.preset"):
        load_config(path)


def test_cli_generate_accepts_the_template_format(tmp_path, monkeypatch):
    from adsfolio.cli import main

    path = _write(
        tmp_path / "cv.yaml",
        {
            "author": {"query": "q", "first_author_prefix": "doe, j"},
            "format": {"output": "latex"},
            "template": {"preset": "text"},
        },
    )
    monkeypatch.setattr("adsfolio.cv.cli.ADSClient", lambda token=None: object())
    monkeypatch.setattr("adsfolio.cv.fetch_publications", lambda client, cfg: DOCS)
    result = CliRunner().invoke(
        main,
        ["cv", "generate", "--config", str(path), "--format", "template", "--dry-run"],
    )
    assert result.exit_code == 0, result.output
    assert "First-authored (1)" in result.output
    assert "*Doe, J.*" in result.output
    assert PLAIN_TITLE in result.output


def test_builtin_formats_still_render_with_default_template():
    cfg = replace(
        _cfg(CVTemplate()), output_format="markdown", me_fmt="**{}**", style="compact"
    )
    assert "## First-authored (1)" in render_cv(DOCS, cfg)
