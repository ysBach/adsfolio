"""Protect tracked PDFs when a filename template needs missing metadata."""

from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest
from pdf_identity_fixtures import confirm_metadata, write_pdf

from adsfolio.pdf.files import sha256_file
from adsfolio.pdf.identifier import PdfIdentifier
from adsfolio.pdf.identify_settings import IdentifySettings
from adsfolio.pdf.registry import open_registry_state
from adsfolio.pdf.rename_settings import RenameSettings
from adsfolio.pdf.renamer import DEFAULT_SCHEME, MissingTemplateFieldsError, PdfRenamer
from adsfolio.pdf.sidecar import Sidecar
from adsfolio.pdf.watch_session import ArrivalIdentity, WatchEvent, WatchState
from adsfolio.pdf.watcher import PdfWatcher


def _crossref_record(path: Path) -> dict:
    doi = "10.1234/example"
    write_pdf(path, doi=doi)
    return PdfIdentifier(None).build_record(
        {
            "provider": "crossref",
            "doi": [doi],
            "author": ["Doe, Jane"],
            "title": ["Dust Tail"],
            "year": "2024",
        },
        path,
    )


@pytest.fixture
def crossref_library(tmp_path):
    source = tmp_path / "download.pdf"
    metadata = _crossref_record(source)
    state = Sidecar(tmp_path)
    state.upsert_file(source.name, metadata)
    state.save()
    return source, metadata, state


def _assert_unchanged(source, state, digest, records, catalog_bytes):
    assert source.is_file()
    assert sha256_file(source) == digest
    assert state.data == records
    assert state.path.read_bytes() == catalog_bytes
    assert list(source.parent.glob("*.pdf")) == [source]


@pytest.mark.parametrize("dry_run", [True, False])
def test_batch_skips_crossref_record_missing_bibcode(crossref_library, dry_run):
    source, _, state = crossref_library
    digest, records, catalog_bytes = (
        sha256_file(source),
        state.data,
        state.path.read_bytes(),
    )
    renamer = PdfRenamer()

    assert renamer.batch_rename(state, template=DEFAULT_SCHEME, dry_run=dry_run) == []

    assert "bibcode" in renamer.skipped[source.name]
    assert "template" in renamer.skipped[source.name].lower()
    assert not renamer.identity_refused
    assert not renamer.unchanged
    _assert_unchanged(source, state, digest, records, catalog_bytes)


@pytest.mark.parametrize("dry_run", [True, False])
def test_single_raises_before_moving_or_changing_catalog(crossref_library, dry_run):
    source, metadata, state = crossref_library
    digest, records, catalog_bytes = (
        sha256_file(source),
        state.data,
        state.path.read_bytes(),
    )
    target_directory = source.parent / "not-created"

    with pytest.raises(MissingTemplateFieldsError) as exc:
        PdfRenamer().rename_tracked_file(
            state, source, metadata, dry_run=dry_run, target_directory=target_directory
        )

    assert isinstance(exc.value, ValueError)
    assert exc.value.missing_fields == ("bibcode",)
    assert "bibcode" in str(exc.value)
    assert not target_directory.exists()
    _assert_unchanged(source, state, digest, records, catalog_bytes)


@pytest.mark.parametrize("single", [False, True])
@pytest.mark.parametrize("dry_run", [True, False])
def test_missing_field_is_not_hidden_by_an_unchanged_target(tmp_path, single, dry_run):
    source = tmp_path / "DoeJ_-Dust_Tail.pdf"
    metadata = _crossref_record(source)
    state = Sidecar(tmp_path)
    state.upsert_file(source.name, metadata)
    state.save()
    digest, records, catalog_bytes = (
        sha256_file(source),
        state.data,
        state.path.read_bytes(),
    )
    renamer = PdfRenamer()

    if single:
        with pytest.raises(MissingTemplateFieldsError):
            renamer.rename_tracked_file(state, source, metadata, dry_run=dry_run)
    else:
        assert renamer.batch_rename(state, dry_run=dry_run) == []
        assert "bibcode" in renamer.skipped[source.name]
        assert not renamer.unchanged

    _assert_unchanged(source, state, digest, records, catalog_bytes)


@pytest.mark.parametrize("single", [False, True])
@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize(
    "template",
    [
        "{authors}_{year}-{title}",
        "{{bibcode}}_{authors}_{year}-{title}",
        "{authors!s}_{year:>4}-{title:.4}",
        "{title:>{year}}",
        "paper",
    ],
)
def test_unreferenced_missing_fields_do_not_block_rename(
    crossref_library, single, dry_run, template
):
    source, metadata, state = crossref_library
    digest = sha256_file(source)
    before = state.get_file(source.name)
    expected_name = PdfRenamer().render(template, metadata)

    if single:
        target = PdfRenamer().rename_tracked_file(
            state, source, metadata, template=template, dry_run=dry_run
        )
        assert target.name == expected_name
    else:
        assert PdfRenamer().batch_rename(state, template=template, dry_run=dry_run) == [
            (source.name, expected_name)
        ]

    output = source if dry_run else source.parent / expected_name
    assert sha256_file(output) == digest
    after = state.get_file(source.name)
    assert after == {**before, "current_filename": output.name}


@pytest.mark.parametrize("single", [False, True])
@pytest.mark.parametrize(
    "template",
    [
        "{bibcode!s}",
        "{bibcode!r}",
        "{bibcode!a}",
        "{bibcode:>12}",
        "{{{bibcode}}}",
        "{title:>{bibcode}}",
        "{bibcode[0]}",
    ],
)
def test_conversions_specs_and_nested_fields_require_their_metadata(
    crossref_library, single, template
):
    source, metadata, state = crossref_library
    digest, records, catalog_bytes = (
        sha256_file(source),
        state.data,
        state.path.read_bytes(),
    )
    renamer = PdfRenamer()

    if single:
        with pytest.raises(MissingTemplateFieldsError) as exc:
            renamer.rename_tracked_file(state, source, metadata, template=template)
        assert exc.value.missing_fields == ("bibcode",)
    else:
        assert renamer.batch_rename(state, template=template, dry_run=False) == []
        assert "bibcode" in renamer.skipped[source.name]

    _assert_unchanged(source, state, digest, records, catalog_bytes)


@pytest.mark.parametrize(
    ("field", "key", "value"),
    [
        ("bibcode", "bibcode", None),
        ("bibcode", "bibcode", []),
        ("bibcode", "bibcode", [None]),
        ("bibcode", "bibcode", "  "),
        ("year", "year", ""),
        ("title", "title", [None]),
        ("title", "title", ["  "]),
        ("authors", "author", []),
        ("authors", "author", [None]),
        ("authors_N", "author", [" "]),
        ("first_author", "author", [None]),
        ("first_author_full", "author", [" "]),
        ("journal", "bibstem", []),
        ("volume", "volume", None),
        ("page", "page", [None]),
    ],
)
def test_each_missing_template_field_is_reported(tmp_path, field, key, value):
    source = tmp_path / "paper.pdf"
    write_pdf(source)
    metadata = {
        "status": "resolved",
        "bibcode": "2024ApJ...900..100D",
        "author": ["Doe, Jane"],
        "title": ["Dust Tail"],
        "year": "2024",
        "sha256": sha256_file(source),
        key: value,
    }
    metadata = confirm_metadata(source, metadata)
    state = Sidecar(tmp_path)
    state.upsert_file(source.name, metadata)
    state.save()
    digest, records, catalog_bytes = (
        sha256_file(source),
        state.data,
        state.path.read_bytes(),
    )

    with pytest.raises(MissingTemplateFieldsError) as exc:
        PdfRenamer().rename_tracked_file(
            state, source, metadata, template="{" + field + "!r}", dry_run=False
        )

    assert exc.value.missing_fields == (field,)
    _assert_unchanged(source, state, digest, records, catalog_bytes)


def test_multiple_missing_fields_are_named_once(crossref_library):
    source, metadata, state = crossref_library
    with pytest.raises(MissingTemplateFieldsError) as exc:
        PdfRenamer().rename_tracked_file(
            state,
            source,
            metadata,
            template="{page}_{bibcode}_{volume}_{bibcode}",
            dry_run=False,
        )
    assert exc.value.missing_fields == ("bibcode", "page", "volume")
    assert all(name in str(exc.value) for name in exc.value.missing_fields)


def test_batch_continues_after_a_missing_field(crossref_library):
    source, _, state = crossref_library
    missing_record = state.get_file(source.name)
    missing_digest = sha256_file(source)
    valid = source.parent / "valid.pdf"
    write_pdf(valid)
    metadata = confirm_metadata(
        valid,
        {
            "status": "resolved",
            "bibcode": "2024ApJ...900..100D",
            "author": ["Doe, Jane"],
            "title": ["Dust Tail"],
            "year": "2024",
            "sha256": sha256_file(valid),
        },
    )
    state.upsert_file(valid.name, metadata)
    state.save()
    valid_record = state.get_file(valid.name)
    valid_digest = sha256_file(valid)
    renamer = PdfRenamer()

    plans = renamer.batch_rename(state, dry_run=False)

    target = source.parent / "DoeJ_2024ApJ...900..100D-Dust_Tail.pdf"
    assert plans == [(valid.name, target.name)]
    assert "bibcode" in renamer.skipped[source.name]
    assert sha256_file(source) == missing_digest
    assert sha256_file(target) == valid_digest
    assert state.get_file(source.name) == missing_record
    assert state.get_file(valid.name) == {
        **valid_record,
        "current_filename": target.name,
    }
    assert Sidecar(source.parent).get_file(source.name) == missing_record


@pytest.mark.parametrize("single", [False, True])
def test_verified_local_metadata_can_rename_without_bibcode(tmp_path, single):
    source = tmp_path / "local.pdf"
    write_pdf(source)
    digest = sha256_file(source)
    metadata = confirm_metadata(
        source,
        {
            "status": "unresolved",
            "author": ["Doe, Jane"],
            "title": ["Dust Tail"],
            "year": "2024",
            "sha256": digest,
            "_adsfolio_locked_fields": ["author", "title", "year"],
        },
    )
    state = Sidecar(tmp_path)
    state.upsert_file(source.name, metadata)
    state.save()
    before = state.get_file(source.name)
    template = "{authors}_{year}-{title}"
    target = tmp_path / "DoeJ_2024-Dust_Tail.pdf"

    if single:
        assert (
            PdfRenamer().rename_tracked_file(
                state, source, metadata, template=template, dry_run=False
            )
            == target
        )
    else:
        assert PdfRenamer().batch_rename(state, template=template, dry_run=False) == [
            (source.name, target.name)
        ]

    assert sha256_file(target) == digest
    assert state.get_file(source.name) == {**before, "current_filename": target.name}


def test_journal_uses_existing_publication_fallback(crossref_library):
    source, metadata, state = crossref_library
    metadata["bibstem"] = []
    metadata["pub"] = "Example Journal"
    state.upsert_file(source.name, metadata)
    state.save()
    target = PdfRenamer().rename_tracked_file(
        state, source, metadata, template="{journal}_{title}", dry_run=False
    )
    assert target.name == "Example_Journal_Dust_Tail.pdf"
    assert sha256_file(target) == metadata["sha256"]


def test_resolved_record_can_omit_unreferenced_metadata(tmp_path):
    source = tmp_path / "paper.pdf"
    write_pdf(source)
    metadata = confirm_metadata(
        source,
        {"status": "resolved", "bibcode": "2024ApJ...900..100D"},
    )
    state = Sidecar(tmp_path)
    state.upsert_file(source.name, metadata)
    state.save()
    digest = sha256_file(source)
    target = PdfRenamer().rename_tracked_file(
        state, source, metadata, template="{bibcode}", dry_run=False
    )
    assert target.name == "2024ApJ...900..100D.pdf"
    assert sha256_file(target) == digest


@pytest.mark.parametrize("single", [False, True])
def test_unknown_template_field_retains_existing_error(crossref_library, single):
    source, metadata, state = crossref_library
    digest, records, catalog_bytes = (
        sha256_file(source),
        state.data,
        state.path.read_bytes(),
    )
    renamer = PdfRenamer()
    with pytest.raises(ValueError, match="Unknown template key") as exc:
        if single:
            renamer.rename_tracked_file(state, source, metadata, template="{unknown}")
        else:
            renamer.batch_rename(state, template="{unknown}")
    assert not isinstance(exc.value, MissingTemplateFieldsError)
    _assert_unchanged(source, state, digest, records, catalog_bytes)


def test_render_retains_preview_semantics_for_missing_fields(crossref_library):
    _, metadata, _ = crossref_library
    assert PdfRenamer().render(DEFAULT_SCHEME, metadata) == "DoeJ_-Dust_Tail.pdf"
    assert PdfRenamer().render("{bibcode!r}", metadata) == "''.pdf"


def test_custom_author_export_runs_only_once(crossref_library):
    source, metadata, state = crossref_library
    calls = []
    export = SimpleNamespace(
        custom=lambda bibcodes, format_string: calls.append(bibcodes) or "DoeJ"
    )
    target = PdfRenamer(client=SimpleNamespace(export=export)).rename_tracked_file(
        state, source, metadata, template="{authors}", author_format="ads_custom:test"
    )
    assert target.name == "DoeJ.pdf"
    assert calls == [[""]]


def test_missing_bibcode_is_reported_before_custom_author_export(crossref_library):
    source, metadata, state = crossref_library
    with pytest.raises(MissingTemplateFieldsError) as exc:
        PdfRenamer().rename_tracked_file(
            state, source, metadata, author_format="ads_custom:test"
        )
    assert exc.value.missing_fields == ("bibcode",)


@pytest.mark.parametrize("single", [False, True])
def test_guard_preserves_central_catalog(crossref_library, single):
    source, metadata, _ = crossref_library
    with closing(open_registry_state(source.parent)) as state:
        state.upsert_file(source.name, metadata)
        state.save()
        before = state.files
        digest = sha256_file(source)
        if single:
            with pytest.raises(MissingTemplateFieldsError):
                PdfRenamer().rename_tracked_file(state, source, metadata, dry_run=False)
        else:
            assert PdfRenamer().batch_rename(state, dry_run=False) == []
        assert state.files == before
        assert state.registry.count_files() == 1
        assert sha256_file(source) == digest
        assert list(source.parent.glob("*.pdf")) == [source]


class _CrossrefIdentifier:
    def __init__(self, metadata):
        self.metadata = metadata

    def pdf_is_readable(self, path):
        return True

    def compute_sha256(self, path):
        return sha256_file(path)

    def resolve_many(self, paths, **kwargs):
        return {Path(paths[0]): self.metadata}


def test_watcher_reports_failure_and_preserves_verified_metadata(tmp_path):
    source = tmp_path / "download.pdf"
    metadata = _crossref_record(source)
    digest = sha256_file(source)
    stat = source.stat()
    event = WatchEvent(
        root=tmp_path,
        session_id="test",
        identity=ArrivalIdentity(stat.st_dev, stat.st_ino, None, 0),
        path=source,
        state=WatchState.DETECTED,
    )
    with closing(open_registry_state(tmp_path)) as state:
        watcher = PdfWatcher(_CrossrefIdentifier(metadata), PdfRenamer())
        outcome = watcher.process_event(
            event, state, RenameSettings(), IdentifySettings()
        )
        stored = state.find_by_current_filename(source.name)
        assert outcome.event.state == WatchState.FAILED
        assert "bibcode" in outcome.message
        assert stored is not None
        assert stored[1]["sha256"] == digest
        assert stored[1]["title"] == metadata["title"]
        assert not stored[1].get("bibcode")
        assert state.registry.count_files() == 1
    assert sha256_file(source) == digest
    assert list(tmp_path.glob("*.pdf")) == [source]
