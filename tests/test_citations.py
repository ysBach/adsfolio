import pytest

from adsfolio.citations import (
    ALLOWED_CITATION_FIELDS,
    CITATION_FIELD_HELP,
    DEFAULT_CITATION_STYLE_ID,
    CitationFormatter,
    CitationStyle,
    CitationStyleError,
    citation_preference_key,
    initial_citation_styles,
    new_citation_style,
    validate_citation_styles,
)

RECORD = {
    "metadata": {
        "bibcode": "2024A&A...123..456B",
        "year": "2024",
        "title": ["Dust <SUP>2</SUP> dynamics"],
        "author": ["Bach, Yoonsoo P.", "Doe, Alice", "Roe, Bob"],
        "bibstem": ["A&A"],
        "pub": "Astronomy &amp; Astrophysics",
        "volume": "123",
        "page": ["456"],
    }
}


def test_formatter_preserves_citation_text_and_expands_every_field() -> None:
    style = CitationStyle(
        id="style-1",
        name="Readable",
        template=(
            "{first_author_surname}|{first_author_initials}|{authors}|"
            "{authors_without_suffix}|{year}|{title}|{journal}|{bibstem}|"
            "{pub}|{volume}|{page}|{bibcode}|{{literal}}"
        ),
        max_authors=2,
        authors_when_truncated=1,
        author_separator=", ",
        author_truncation_suffix=" et al.",
    )
    assert CitationFormatter().render(style, RECORD) == (
        "Bach|BachYP|BachYP et al.|BachYP|2024|Dust 2 dynamics|A&A|A&A|"
        "Astronomy & Astrophysics|123|456|2024A&A...123..456B|{literal}"
    )


def test_formatter_handles_scalar_and_sequence_metadata() -> None:
    record = {
        "metadata": {
            "bibcode": ["2020ApJ...100..200Z"],
            "year": 2020,
            "title": "A &amp; B &lt;sub&gt;test&lt;/sub&gt;",
            "author": "Zwicky, Fritz",
            "bibstem": "ApJ",
            "pub": ["The Astrophysical Journal"],
            "volume": 100,
            "page": 200,
        }
    }
    style = CitationStyle(
        id="s",
        name="Scalar",
        template="{first_author_surname}|{first_author_initials}|{authors}|{year}|{title}|{journal}|{pub}|{volume}|{page}|{bibcode}",
    )
    assert CitationFormatter().render(style, record) == (
        "Zwicky|ZwickyF|ZwickyF|2020|A & B test|ApJ|The Astrophysical Journal|100|200|2020ApJ...100..200Z"
    )


def test_formatter_missing_known_fields_render_empty_strings() -> None:
    style = CitationStyle(
        id="s",
        name="Empty",
        template="[{bibcode}][{year}][{title}][{authors}][{journal}][{pub}][{volume}][{page}]",
    )
    assert CitationFormatter().render(style, {}) == "[][][][][][][][]"
    assert CitationFormatter().render(style, {"metadata": {}}) == "[][][][][][][][]"


def test_formatter_journal_falls_back_to_pub_only_when_bibstem_absent() -> None:
    style = CitationStyle(
        id="s",
        name="JournalTest",
        template="{bibstem}|{pub}|{journal}",
    )
    # Both present
    rec1 = {"metadata": {"bibstem": ["ApJ"], "pub": "Astrophysical Journal"}}
    assert CitationFormatter().render(style, rec1) == "ApJ|Astrophysical Journal|ApJ"

    # bibstem absent, pub present
    rec2 = {"metadata": {"pub": "Nature"}}
    assert CitationFormatter().render(style, rec2) == "|Nature|Nature"

    # bibstem empty list, pub present
    rec3 = {"metadata": {"bibstem": [], "pub": "Science"}}
    assert CitationFormatter().render(style, rec3) == "|Science|Science"

    # Both absent
    rec4 = {"metadata": {}}
    assert CitationFormatter().render(style, rec4) == "||"


@pytest.mark.parametrize(
    (
        "author_format",
        "author_separator",
        "truncation_suffix",
        "expected_authors",
        "expected_no_suffix",
    ),
    [
        ("surname", ", ", " et al.", "Bach, Doe et al.", "Bach, Doe"),
        ("surname_initials", " & ", "...", "BachYP & DoeA...", "BachYP & DoeA"),
        (
            "last_comma_first",
            "; ",
            " et al.",
            "Bach, Yoonsoo P.; Doe, Alice et al.",
            "Bach, Yoonsoo P.; Doe, Alice",
        ),
    ],
)
def test_formatter_author_formats_and_truncation(
    author_format: str,
    author_separator: str,
    truncation_suffix: str,
    expected_authors: str,
    expected_no_suffix: str,
) -> None:
    style = CitationStyle(
        id="s",
        name="AuthFmt",
        template="{authors}|{authors_without_suffix}",
        author_format=author_format,
        max_authors=2,
        authors_when_truncated=2,
        author_separator=author_separator,
        author_truncation_suffix=truncation_suffix,
    )
    assert (
        CitationFormatter().render(style, RECORD)
        == f"{expected_authors}|{expected_no_suffix}"
    )


def test_formatter_author_counts_below_equal_above_threshold() -> None:
    rec_1 = {"metadata": {"author": ["Author, One"]}}
    rec_2 = {"metadata": {"author": ["Author, One", "Author, Two"]}}
    rec_3 = {"metadata": {"author": ["Author, One", "Author, Two", "Author, Three"]}}

    style = CitationStyle(
        id="s",
        name="Threshold",
        template="{authors}",
        max_authors=2,
        authors_when_truncated=1,
        author_separator=", ",
        author_truncation_suffix=" et al.",
    )
    # Below max_authors: 1 <= 2 -> no truncation
    assert CitationFormatter().render(style, rec_1) == "AuthorO"
    # Equal to max_authors: 2 <= 2 -> no truncation
    assert CitationFormatter().render(style, rec_2) == "AuthorO, AuthorT"
    # Above max_authors: 3 > 2 -> truncate to authors_when_truncated (1) + suffix
    assert CitationFormatter().render(style, rec_3) == "AuthorO et al."


def test_formatter_preserves_unicode_and_punctuation_and_spaces() -> None:
    rec = {
        "metadata": {
            "title": ["Gravitational waves: Müller &amp; O'Connor (2024) [v2]"],
            "author": ["Müller, Hans-Jürgen", "O'Connor, Flannery"],
        }
    }
    style = CitationStyle(
        id="s",
        name="Unicode",
        template="{first_author_surname} / {first_author_initials} / {title} / {authors}",
        author_format="last_comma_first",
        author_separator=" & ",
    )
    assert CitationFormatter().render(style, rec) == (
        "Müller / MüllerHJ / Gravitational waves: Müller & O'Connor (2024) [v2] / "
        "Müller, Hans-Jürgen & O'Connor, Flannery"
    )


def test_formatter_no_comma_author_split() -> None:
    rec = {
        "metadata": {
            "author": ["Albert Einstein", "Plato"],
        }
    }
    style = CitationStyle(
        id="s",
        name="NoComma",
        template="{first_author_surname}|{first_author_initials}|{authors}",
        author_format="last_comma_first",
        author_separator="; ",
    )
    assert (
        CitationFormatter().render(style, rec)
        == "Einstein|EinsteinA|Einstein, Albert; Plato"
    )


def test_formatter_empty_rendered_output() -> None:
    style = CitationStyle(id="s", name="Empty", template="{title}")
    assert CitationFormatter().render(style, {"metadata": {}}) == ""


@pytest.mark.parametrize(
    "invalid_template",
    [
        "{first_author}",
        "{first_author.name}",
        "{author[0]}",
        "{year!r}",
        "{year:04}",
        "{unknown_field}",
        "Unmatched {brace",
        "Unmatched } brace",
        "{",
        "}",
        "{}",
    ],
)
def test_validation_rejects_invalid_templates(invalid_template: str) -> None:
    with pytest.raises(CitationStyleError):
        CitationStyle(id="s", name="Invalid", template=invalid_template)


@pytest.mark.parametrize(
    ("max_authors", "authors_when_truncated"),
    [
        (0, 1),
        (3, 0),
        (2, 3),  # authors_when_truncated > max_authors
        (100, 1),  # max_authors > 99
        (3, 100),
        (-1, -1),
    ],
)
def test_validation_rejects_invalid_author_limits(
    max_authors: int, authors_when_truncated: int
) -> None:
    with pytest.raises(CitationStyleError):
        CitationStyle(
            id="s",
            name="Limits",
            template="{authors}",
            max_authors=max_authors,
            authors_when_truncated=authors_when_truncated,
        )


@pytest.mark.parametrize(
    "invalid_author_format",
    [
        "ads_custom:",
        "ads_custom:%H%Y",
        "invalid_format",
        "",
    ],
)
def test_validation_rejects_invalid_author_format(invalid_author_format: str) -> None:
    with pytest.raises(CitationStyleError):
        CitationStyle(
            id="s",
            name="Fmt",
            template="{authors}",
            author_format=invalid_author_format,
        )


@pytest.mark.parametrize(
    ("id_val", "name_val", "template_val"),
    [
        ("", "Valid Name", "{bibcode}"),
        ("   ", "Valid Name", "{bibcode}"),
        ("s", "", "{bibcode}"),
        ("s", "   ", "{bibcode}"),
        ("s", "Valid Name", ""),
        ("s", "Valid Name", "   "),
    ],
)
def test_validation_rejects_empty_id_name_or_template(
    id_val: str, name_val: str, template_val: str
) -> None:
    with pytest.raises(CitationStyleError):
        CitationStyle(id=id_val, name=name_val, template=template_val)


def test_from_mapping_and_to_mapping_round_trip() -> None:
    payload = {
        "id": "  style-123  ",
        "name": "  Custom Style  ",
        "template": "  {bibcode} - {year}  ",
        "author_format": "surname",
        "max_authors": 5,
        "authors_when_truncated": 2,
        "author_separator": "; ",
        "author_truncation_suffix": " et al.",
    }
    style = CitationStyle.from_mapping(payload)
    assert style.id == "style-123"
    assert style.name == "Custom Style"
    assert style.template == "{bibcode} - {year}"
    assert style.author_format == "surname"
    assert style.max_authors == 5
    assert style.authors_when_truncated == 2

    dumped = style.to_mapping()
    assert dumped == {
        "id": "style-123",
        "name": "Custom Style",
        "template": "{bibcode} - {year}",
        "author_format": "surname",
        "max_authors": 5,
        "authors_when_truncated": 2,
        "author_separator": "; ",
        "author_truncation_suffix": " et al.",
    }


def test_from_mapping_normalizes_malformed_optional_controls() -> None:
    payload = {
        "id": "style-1",
        "name": "Name",
        "template": "{bibcode}",
        "author_format": "invalid_value",
        "max_authors": "not_an_int",
        "authors_when_truncated": -5,
        "author_separator": None,
        "author_truncation_suffix": None,
    }
    style = CitationStyle.from_mapping(payload)
    assert style.author_format == "surname_initials"
    assert style.max_authors == 3
    assert style.authors_when_truncated == 1
    assert style.author_separator == ", "
    assert style.author_truncation_suffix == " et al."

    bool_limits_style = CitationStyle.from_mapping(
        {
            "id": "style-bool",
            "name": "Bool Limits",
            "template": "{bibcode}",
            "author_format": ["not-a-string"],
            "max_authors": True,
            "authors_when_truncated": False,
        }
    )
    assert bool_limits_style.author_format == "surname_initials"
    assert bool_limits_style.max_authors == 3
    assert bool_limits_style.authors_when_truncated == 1


def test_constructor_rejects_boolean_limits_and_non_string_fields() -> None:
    with pytest.raises(CitationStyleError, match="(?i)author_format"):
        CitationStyle(id="s1", name="Name", template="{bibcode}", author_format=[])  # type: ignore[arg-type]
    with pytest.raises(CitationStyleError, match="(?i)integers"):
        CitationStyle(id="s1", name="Name", template="{bibcode}", max_authors=True)
    with pytest.raises(CitationStyleError, match="(?i)integers"):
        CitationStyle(
            id="s1",
            name="Name",
            template="{bibcode}",
            max_authors=3,
            authors_when_truncated=True,
        )
    with pytest.raises(CitationStyleError, match="(?i)separator"):
        CitationStyle(
            id="s1",
            name="Name",
            template="{bibcode}",
            author_separator=None,  # type: ignore[arg-type]
        )
    with pytest.raises(CitationStyleError, match="(?i)truncation suffix"):
        CitationStyle(
            id="s1",
            name="Name",
            template="{bibcode}",
            author_truncation_suffix=123,  # type: ignore[arg-type]
        )


def test_initial_citation_styles() -> None:
    styles = initial_citation_styles()
    assert len(styles) == 1
    style = styles[0]
    assert style.id == DEFAULT_CITATION_STYLE_ID
    assert style.id == "my-bibcode-1"
    assert style.name == "My bibcode 1"
    assert (
        style.template == "{first_author_initials} ({year}) {bibstem}, {volume}, {page}"
    )


def test_new_citation_style_generates_lowest_unused_name_and_hex_uuid() -> None:
    existing_names = ["My bibcode 1", "my bibcode 2", "other"]
    existing_ids = {"abc", "def"}

    style = new_citation_style(existing_names, existing_ids)
    assert style.name == "My bibcode 3"
    assert len(style.id) == 32  # uuid4().hex
    assert style.id not in existing_ids
    assert (
        style.template == "{first_author_initials} ({year}) {bibstem}, {volume}, {page}"
    )

    # Handles holes in numbering
    style2 = new_citation_style(["My bibcode 2"], set())
    assert style2.name == "My bibcode 1"


def test_citation_preference_key() -> None:
    assert citation_preference_key("my-bibcode-1") == "citation:my-bibcode-1"
    with pytest.raises(CitationStyleError):
        citation_preference_key("")
    with pytest.raises(CitationStyleError):
        citation_preference_key("   ")


def test_validate_citation_styles_empty_and_valid() -> None:
    validate_citation_styles(())
    validate_citation_styles([])

    initial = initial_citation_styles()
    validate_citation_styles(initial)


def test_validate_citation_styles_rejects_duplicates() -> None:
    s1 = CitationStyle(id="id-1", name="Alpha", template="{bibcode}")
    s2 = CitationStyle(id="id-1", name="Beta", template="{bibcode}")
    with pytest.raises(CitationStyleError, match="(?i)duplicate.*id"):
        validate_citation_styles([s1, s2])

    s3 = CitationStyle(id="id-2", name="  alpha  ", template="{bibcode}")
    with pytest.raises(CitationStyleError, match="(?i)duplicate.*name"):
        validate_citation_styles([s1, s3])


def test_allowed_citation_fields_and_help() -> None:
    assert ALLOWED_CITATION_FIELDS == frozenset(
        {
            "bibcode",
            "year",
            "title",
            "first_author_surname",
            "first_author_initials",
            "authors",
            "authors_without_suffix",
            "journal",
            "bibstem",
            "pub",
            "volume",
            "page",
        }
    )
    for field in ALLOWED_CITATION_FIELDS:
        assert f"{{{field}}}" in CITATION_FIELD_HELP


@pytest.mark.parametrize("inf_val", [float("inf"), float("-inf")])
@pytest.mark.parametrize("field_name", ["max_authors", "authors_when_truncated"])
def test_from_mapping_falls_back_on_infinite_counts(
    inf_val: float, field_name: str
) -> None:
    payload = {"id": "s1", "name": "Name", "template": "{bibcode}", field_name: inf_val}
    style = CitationStyle.from_mapping(payload)
    if field_name == "max_authors":
        assert style.max_authors == 3
    else:
        assert style.authors_when_truncated == 1


@pytest.mark.parametrize("inf_val", [float("inf"), float("-inf")])
@pytest.mark.parametrize("field_name", ["max_authors", "authors_when_truncated"])
def test_constructor_rejects_infinite_counts(inf_val: float, field_name: str) -> None:
    kwargs = {"id": "s1", "name": "Name", "template": "{bibcode}", field_name: inf_val}
    with pytest.raises(CitationStyleError, match="(?i)integers"):
        CitationStyle(**kwargs)  # type: ignore[arg-type]
