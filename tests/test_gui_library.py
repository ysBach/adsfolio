import pytest

from adsfolio.gui.library import (
    BASE_COLUMNS,
    COLUMNS,
    ISO8601_TIMESTAMP_FORMAT,
    FileQuery,
    LibraryView,
    ResolutionState,
    ads_url,
    arxiv_url,
    build_columns,
    format_count,
    format_mtime,
    resolution_state,
)


def test_file_query_maps_views_and_search():
    assert FileQuery().to_filters() == {}
    assert FileQuery(view=LibraryView.RESOLVED).to_filters() == {"resolved": True}
    assert FileQuery(view=LibraryView.UNRESOLVED).to_filters() == {"resolved": False}
    assert FileQuery(view=LibraryView.ARXIV_ONLY).to_filters() == {"source": "arxiv"}
    assert FileQuery(view=LibraryView.DUPLICATES).to_filters() == {
        "duplicates_only": True
    }
    assert FileQuery(view=LibraryView.RESOLVED, search=" Bach ").to_filters() == {
        "resolved": True,
        "search": " Bach ",
    }


def test_file_query_defaults_to_added_newest_first() -> None:
    query = FileQuery()

    assert query.order_by == "first_seen_at"
    assert query.descending


def test_columns_render_full_record():
    record = {
        "current_path": "subdir/paper.pdf",
        "size_bytes": 1_468_006,
        "first_seen_at": "2026-08-29T12:34:56+00:00",
        "mtime_ns": 1_700_000_000_000_000_000,
        "year": "2024",
        "bibcode": "2024A&A...1A...1B",
        "status": "resolved",
        "source": "published",
        "metadata": {
            "title": ["A title"],
            "author": ["Bach, Y.", "Doe, J."],
            "bibstem": ["A&A"],
            "doi": ["10.48550/arXiv.2606.12345v2"],
        },
    }
    values = {column.key: column.getter(record) for column in COLUMNS}
    assert values["title"] == "A title"
    assert values["author"] == "Bach, Y."
    assert values["year"] == "2024"
    assert values["pub"] == "A&A"
    assert values["arxiv"] == "2606.12345v2"
    assert values["filename"] == "paper.pdf"
    assert values["size_bytes"].endswith("MB")
    assert "T" in values["first_seen_at"]
    assert values["mtime_ns"]
    assert set(values) == {
        "title",
        "author",
        "author_count",
        "year",
        "pub",
        "citation_count",
        "bibcode",
        "arxiv",
        "status",
        "filename",
        "size_bytes",
        "first_seen_at",
        "mtime_ns",
    }


def test_added_column_precedes_modified_and_tolerates_bad_timestamps() -> None:
    keys = [column.key for column in COLUMNS]

    assert "first_seen_at" in keys
    assert keys.index("first_seen_at") + 1 == keys.index("mtime_ns")
    added = COLUMNS[keys.index("first_seen_at")]
    assert added.header == "Added"
    assert added.getter({"first_seen_at": None}) == ""
    assert added.getter({"first_seen_at": "not-a-timestamp"}) == ""


def test_columns_tolerate_empty_and_scalar_records():
    empty = {column.key: column.getter({}) for column in COLUMNS}
    assert all(isinstance(value, str) for value in empty.values())
    record = {
        "current_path": "paper.pdf",
        "metadata": {
            "title": "Scalar title",
            "author": "Scalar author",
            "bibstem": "AJ",
        },
    }
    values = {column.key: column.getter(record) for column in COLUMNS}
    assert values["title"] == "Scalar title"
    assert values["author"] == "Scalar author"
    assert values["pub"] == "AJ"
    assert values["filename"] == "paper.pdf"


def test_arxiv_column_extracts_legacy_bibcode_identifier():
    record = {"metadata": {"bibcode": "2024arXiv240112345B"}}

    arxiv_column = next(column for column in COLUMNS if column.key == "arxiv")

    assert arxiv_column.getter(record) == "2401.12345"


def test_external_link_urls_use_stable_public_endpoints():
    assert ads_url("2024A&A...1A...1B") == (
        "https://adsabs.harvard.edu/abs/2024A%26A...1A...1B"
    )
    assert arxiv_url("2606.12345") == "https://arxiv.org/abs/2606.12345"


def test_resolution_column_has_a_compact_blank_header():
    """The resolution marker leaves space for bibliographic columns."""
    status_column = next(column for column in COLUMNS if column.key == "status")

    assert status_column.header == ""
    assert status_column.width <= 24


def test_format_mtime_supports_iso8601_and_custom_patterns():
    record = {"mtime_ns": 1_700_000_000_000_000_000}

    assert "T" in format_mtime(record, ISO8601_TIMESTAMP_FORMAT)
    assert format_mtime(record, "%Y-%m-%d")


def test_base_columns_compatibility_alias():
    assert COLUMNS == BASE_COLUMNS
    for col in BASE_COLUMNS:
        assert col.sortable is True


def test_build_columns_defaults_to_base_columns():
    assert build_columns() == BASE_COLUMNS
    assert build_columns([]) == BASE_COLUMNS


def test_build_columns_inserts_after_bibcode_with_correct_properties():
    from adsfolio.citations import CitationStyle

    style_a = CitationStyle(id="alpha", name="Alpha Style", template="{year}: {title}")
    style_b = CitationStyle(id="beta", name="Beta Style", template="{bibcode}")

    columns = build_columns([style_a, style_b])
    keys = [c.key for c in columns]

    bibcode_idx = keys.index("bibcode")
    assert keys[bibcode_idx + 1] == "citation:alpha"
    assert keys[bibcode_idx + 2] == "citation:beta"
    assert keys[bibcode_idx + 3] == "arxiv"

    col_a = columns[bibcode_idx + 1]
    col_b = columns[bibcode_idx + 2]

    assert col_a.header == "Alpha Style"
    assert col_a.sortable is False
    assert col_b.header == "Beta Style"
    assert col_b.sortable is False

    # Check that base columns remain sortable
    assert columns[bibcode_idx].sortable is True
    assert columns[bibcode_idx + 3].sortable is True

    # Test rendering via getter
    record = {
        "metadata": {
            "year": ["2024"],
            "title": ["Test Title"],
            "bibcode": ["2024TEST...1A"],
        }
    }
    assert col_a.getter(record) == "2024: Test Title"
    assert col_b.getter(record) == "2024TEST...1A"


def test_arxiv_column_uses_only_arxiv_dois():
    arxiv_column = next(column for column in COLUMNS if column.key == "arxiv")

    assert arxiv_column.getter({"doi": "10.1006/icar.1998.6100"}) == ""
    assert (
        arxiv_column.getter({"metadata": {"doi": ["10.48550/arXiv.2401.12345"]}})
        == "2401.12345"
    )


def test_title_column_converts_html_in_list_metadata():
    title_column = next(column for column in COLUMNS if column.key == "title")
    record = {"metadata": {"title": ["2001 CC<SUB>21</SUB> &amp; more"]}}

    assert title_column.getter(record) == "2001 CC21 & more"


def test_saved_counts_show_zero_but_leave_missing_values_empty() -> None:
    assert format_count({"metadata": {"author_count": 0}}, "author_count") == "0"
    assert format_count({"metadata": {"citation_count": 12}}, "citation_count") == "12"
    assert format_count({"metadata": {"citation_count": "7"}}, "citation_count") == "7"
    assert format_count({"metadata": {}}, "citation_count") == ""
    assert format_count({"metadata": {"citation_count": None}}, "citation_count") == ""
    # A short author list does not establish how many authors the paper has.
    assert (
        format_count({"metadata": {"author": ["A, B.", "C, D."]}}, "author_count") == ""
    )


def _local_metadata() -> dict:
    return {
        "title": ["Notes"],
        "author": ["Bach, Y."],
        "year": "2024",
        "_adsfolio_locked_fields": ["title"],
    }


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        (
            {"status": "resolved", "bibcode": "2024A&A...1A", "metadata": {}},
            ResolutionState.ADS_LINKED,
        ),
        (
            {"status": "unresolved", "metadata": _local_metadata()},
            ResolutionState.LOCAL_METADATA,
        ),
        # Partly entered manual metadata is not rename-ready.
        (
            {
                "status": "unresolved",
                "metadata": {"title": ["Notes"], "_adsfolio_locked_fields": ["title"]},
            },
            ResolutionState.UNRESOLVED,
        ),
        ({"status": "unresolved", "metadata": {}}, ResolutionState.UNRESOLVED),
        (
            {
                "status": "resolved",
                "bibcode": "2024A&A...1A",
                "sha256": "now",
                "metadata": {"_adsfolio_metadata_sha256": "before"},
            },
            ResolutionState.CHANGED,
        ),
        (
            {
                "status": "unresolved",
                "sha256": "now",
                "metadata": {
                    **_local_metadata(),
                    "_adsfolio_metadata_sha256": "before",
                },
            },
            ResolutionState.CHANGED,
        ),
    ],
)
def test_resolution_state_separates_ads_local_unresolved_and_changed(
    record: dict, expected: ResolutionState
) -> None:
    assert resolution_state(record) is expected
