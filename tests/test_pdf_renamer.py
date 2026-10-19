import json
from types import SimpleNamespace

import pytest

from adsfolio.pdf.renamer import DEFAULT_SCHEME, PdfRenamer
from adsfolio.pdf.sidecar import SIDECAR_FILENAME, Sidecar


class _ExportStub:
    def __init__(self, output):
        self.output = output
        self.calls = []

    def custom(self, bibcodes, format_string, sort=None):
        self.calls.append(
            {
                "bibcodes": bibcodes,
                "format_string": format_string,
                "sort": sort,
            }
        )
        return self.output


def _sample_metadata(**overrides):
    base = {
        "bibcode": "2024ApJ...900..100S",
        "title": ["Paper Title"],
        "author": ["Smith, John", "Doe, Alice", "Lee, Chris"],
        "year": "2024",
        "bibstem": ["ApJ"],
        "volume": "900",
        "page": ["100"],
        "pub": "The Astrophysical Journal",
    }
    base.update(overrides)
    return base


def test_sanitize_filename_removes_invalid_characters():
    renamer = PdfRenamer()
    name = renamer.sanitize_filename(' A/B:C*D?"E"<F>|.pdf ')
    assert name == "A_B_C_D_E_F_.pdf"


def test_render_default_scheme_with_ampersand_bibcode():
    renamer = PdfRenamer()
    metadata = _sample_metadata(bibcode="2021A&A...654A.113B", title=["My Paper"])

    rendered = renamer.render(DEFAULT_SCHEME, metadata, max_authors=2)
    assert rendered == "SmithJ+_2021A&A...654A.113B-My_Paper.pdf"


@pytest.mark.parametrize(
    ("authors", "max_authors", "expected"),
    [
        (["Smith, John"], 2, "SmithJ.pdf"),
        (["Smith, John", "Doe, Alice"], 2, "SmithJ+DoeA.pdf"),
        (["Smith, John", "Doe, Alice", "Lee, Chris"], 2, "SmithJ+.pdf"),
    ],
)
def test_render_uses_max_authors_as_inclusive_truncation_threshold(
    authors, max_authors, expected
):
    rendered = PdfRenamer().render(
        "{authors}",
        _sample_metadata(author=authors),
        max_authors=max_authors,
    )

    assert rendered == expected


def test_render_uses_configured_retained_count_only_after_threshold():
    metadata = _sample_metadata()

    rendered = PdfRenamer().render(
        "{authors}|{authors_N}",
        metadata,
        max_authors=2,
        authors_when_truncated=2,
    )

    assert rendered == "SmithJ+DoeA+_SmithJ+DoeA.pdf"


def test_render_uses_custom_author_separator_and_truncation_suffix():
    renamer = PdfRenamer()
    metadata = _sample_metadata()

    rendered = renamer.render(
        "{authors}",
        metadata,
        max_authors=2,
        authors_when_truncated=2,
        author_separator="~",
        author_truncation_suffix="++",
    )

    assert rendered == "SmithJ~DoeA++.pdf"


def test_render_omits_custom_truncation_suffix_when_all_authors_are_included():
    renamer = PdfRenamer()
    metadata = _sample_metadata(author=["Smith, John", "Doe, Alice"])

    rendered = renamer.render(
        "{authors}",
        metadata,
        max_authors=2,
        author_separator="~",
        author_truncation_suffix="++",
    )

    assert rendered == "SmithJ~DoeA.pdf"


def test_render_supports_author_style_modes():
    renamer = PdfRenamer()
    metadata = _sample_metadata(author=["Smith, John", "Doe, Alice"])

    surname = renamer.render(
        "{authors}", metadata, author_format="surname", max_authors=99
    )
    assert surname == "Smith+Doe.pdf"

    comma_first = renamer.render(
        "{authors}",
        metadata,
        author_format="last_comma_first",
        max_authors=99,
    )
    assert comma_first == "Smith,_John+Doe,_Alice.pdf"


def test_render_ads_custom_delegates_to_export_api():
    export = _ExportStub("SmithJ+DoeA")
    client = SimpleNamespace(export=export)
    renamer = PdfRenamer(client=client)
    metadata = _sample_metadata()

    rendered = renamer.render("{authors}", metadata, author_format="ads_custom:%3.1m")
    assert rendered == "SmithJ+DoeA.pdf"
    assert export.calls == [
        {
            "bibcodes": ["2024ApJ...900..100S"],
            "format_string": "%3.1m",
            "sort": None,
        }
    ]


def test_render_char_policy_ascii_transliterates_author_names():
    renamer = PdfRenamer()
    metadata = _sample_metadata(author=["Hanus\u030c, Jan"])

    rendered = renamer.render(
        "{authors}",
        metadata,
        author_format="surname_initials",
        max_authors=99,
        char_policy="ascii",
    )
    assert rendered == "HanusJ.pdf"


def test_render_char_policy_ascii_transliterates_non_decomposable_latin_letters():
    renamer = PdfRenamer()
    metadata = _sample_metadata(author=["Wesołowski, Marcin"])

    rendered = renamer.render(
        "{authors}",
        metadata,
        author_format="surname_initials",
        max_authors=99,
        char_policy="ascii",
    )

    assert rendered == "WesolowskiM.pdf"


def test_render_default_char_policy_preserves_unicode():
    renamer = PdfRenamer()
    metadata = _sample_metadata(author=["Wesołowski, Marcin"])

    rendered = renamer.render(
        "{authors}",
        metadata,
        author_format="surname_initials",
        max_authors=99,
    )

    assert rendered == "WesołowskiM.pdf"


def test_render_char_policy_keep_preserves_unicode_author_names():
    renamer = PdfRenamer()
    metadata = _sample_metadata(author=["Hanus\u030c, Jan"])

    rendered = renamer.render(
        "{authors}",
        metadata,
        author_format="surname_initials",
        max_authors=99,
        char_policy="keep",
    )
    assert rendered == "HanušJ.pdf"


@pytest.mark.parametrize(
    ("max_authors", "authors_when_truncated"),
    [(0, 1), (2, 0), (2, 3), (100, 1)],
)
def test_render_rejects_invalid_author_limits(max_authors, authors_when_truncated):
    with pytest.raises(ValueError, match="authors"):
        PdfRenamer().render(
            "{authors}",
            _sample_metadata(),
            max_authors=max_authors,
            authors_when_truncated=authors_when_truncated,
        )


def test_batch_rename_uses_authors_when_truncated(tmp_path):
    source = tmp_path / "old.pdf"
    source.write_bytes(b"%PDF")
    sidecar = Sidecar(tmp_path)
    sidecar.upsert_file(
        "original.pdf",
        _sample_metadata(current_filename="old.pdf"),
    )

    plans = PdfRenamer().batch_rename(
        sidecar,
        template="{authors}",
        max_authors=2,
        authors_when_truncated=2,
    )

    assert plans == [("old.pdf", "SmithJ+DoeA+.pdf")]


def test_pdf_renamer_strips_ads_html_markup_from_title():
    """ADS subscript markup never leaks into a generated filename."""
    result = PdfRenamer().render(
        "{bibcode}_{title}",
        {
            "bibcode": "2023MNRAS.525L..17G",
            "title": ["Spectral type of 98943 (2001 CC<sub>21</sub>), the target"],
        },
    )

    assert "SUB" not in result
    assert "CC21" in result


def test_batch_rename_dry_run_does_not_change_files(tmp_path):
    source = tmp_path / "old.pdf"
    source.write_bytes(b"%PDF")

    sidecar = Sidecar(tmp_path)
    sidecar.upsert_file(
        "original.pdf",
        _sample_metadata(current_filename="old.pdf"),
    )

    renamer = PdfRenamer()
    plans = renamer.batch_rename(sidecar, template="{bibcode}", dry_run=True)

    assert plans == [("old.pdf", "2024ApJ...900..100S.pdf")]
    assert source.exists()
    assert not (tmp_path / "2024ApJ...900..100S.pdf").exists()
    assert sidecar.get_file("original.pdf")["current_filename"] == "old.pdf"


def test_batch_rename_apply_updates_sidecar_and_filesystem(tmp_path):
    source = tmp_path / "old.pdf"
    source.write_bytes(b"%PDF")

    sidecar = Sidecar(tmp_path)
    sidecar.upsert_file(
        "original.pdf",
        _sample_metadata(current_filename="old.pdf"),
    )

    renamer = PdfRenamer()
    plans = renamer.batch_rename(sidecar, template="{bibcode}", dry_run=False)

    target = tmp_path / "2024ApJ...900..100S.pdf"
    assert plans == [("old.pdf", "2024ApJ...900..100S.pdf")]
    assert target.exists()
    assert not source.exists()

    record = sidecar.get_file("original.pdf")
    assert record is not None
    assert record["current_filename"] == "2024ApJ...900..100S.pdf"

    sidecar_data = json.loads((tmp_path / SIDECAR_FILENAME).read_text(encoding="utf-8"))
    assert (
        sidecar_data["files"]["original.pdf"]["current_filename"]
        == "2024ApJ...900..100S.pdf"
    )


def test_batch_rename_relocates_resolved_files_from_unresolved(tmp_path):
    unresolved = tmp_path / "unresolved"
    unresolved.mkdir()
    resolved_source = unresolved / "old.pdf"
    unresolved_source = unresolved / "still-unresolved.pdf"
    resolved_source.write_bytes(b"%PDF")
    unresolved_source.write_bytes(b"%PDF")

    sidecar = Sidecar(tmp_path)
    sidecar.upsert_file(
        "original.pdf",
        _sample_metadata(
            current_filename="unresolved/old.pdf",
        ),
    )
    sidecar.upsert_file(
        "unresolved-original.pdf",
        {"current_filename": "unresolved/still-unresolved.pdf", "status": "unresolved"},
    )

    renamer = PdfRenamer()
    plans = renamer.batch_rename(
        sidecar,
        template="{bibcode}",
        dry_run=False,
        move_resolved_from=unresolved,
        move_resolved_to=tmp_path,
    )

    target = tmp_path / "2024ApJ...900..100S.pdf"
    assert plans == [("unresolved/old.pdf", "2024ApJ...900..100S.pdf")]
    assert target.exists()
    assert not resolved_source.exists()
    assert unresolved_source.exists()
    assert sidecar.get_file("original.pdf")["current_filename"] == target.name
