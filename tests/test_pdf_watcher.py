from pathlib import Path

import pytest

from adsfolio.config import AuthenticationError, Config
from adsfolio.pdf.doi_providers import DEFAULT_DOI_PROVIDERS
from adsfolio.pdf.identify_settings import IdentifySettings, IdentifyStrategy
from adsfolio.pdf.registry import open_registry_state
from adsfolio.pdf.rename_settings import RenameSettings
from adsfolio.pdf.renamer import PdfRenamer
from adsfolio.pdf.sidecar import Sidecar
from adsfolio.pdf.tracked_move import move_tracked_file
from adsfolio.pdf.watch_session import ArrivalIdentity, WatchEvent, WatchState
from adsfolio.pdf.watcher import PdfNotReadyError, PdfWatcher, WatchIdentifyPolicy


@pytest.fixture(autouse=True)
def consistent_mock_digest(monkeypatch):
    # Watcher test identifiers use a fixed digest; the rename guard must use
    # the same hashing double. Real digest mismatches have dedicated tests.
    monkeypatch.setattr("adsfolio.pdf.renamer.sha256_file", lambda path: "watch-sha")


class _Identifier:
    def __init__(self, record):
        self.record = record

    def compute_sha256(self, path: Path) -> str:
        return "watch-sha"

    def pdf_is_readable(self, path: Path) -> bool:
        return True

    def resolve_many(self, paths, journal_map=None, **kwargs):
        return {Path(path): self.record for path in paths}


class _CountingIdentifier(_Identifier):
    def __init__(self, record):
        super().__init__(record)
        self.hash_calls = 0

    def compute_sha256(self, path: Path) -> str:
        self.hash_calls += 1
        return super().compute_sha256(path)


class _NoResolveIdentifier(_Identifier):
    """Fail if a cached resolved record unexpectedly reaches ADS lookup."""

    def resolve_many(self, paths, journal_map=None, **kwargs):
        raise AssertionError("cached resolved PDF must not query ADS")


class _ChangingIdentifier(_Identifier):
    def __init__(self, record):
        super().__init__(record)
        self.sha256 = "watch-sha-1"

    def compute_sha256(self, path: Path) -> str:
        return self.sha256


class _StrategyRecordingIdentifier(_Identifier):
    def __init__(self, record):
        super().__init__(None)
        self.inferred_record = record
        self.resolve_many_calls = []
        self.hash_calls = 0

    def compute_sha256(self, path: Path) -> str:
        self.hash_calls += 1
        return super().compute_sha256(path)

    def resolve_many(self, paths, journal_map=None, **kwargs):
        self.resolve_many_calls.append((paths, journal_map, kwargs))
        return {Path(paths[0]): self.inferred_record}


class _Renamer:
    def batch_rename(self, **kwargs):
        raise AssertionError("watching one file must not rename the whole library")

    def rename_tracked_file(self, state, source_path, metadata, **kwargs):
        return source_path


def _state(monkeypatch, tmp_path):
    monkeypatch.setattr(
        Config,
        "get_config_dir",
        classmethod(lambda cls: tmp_path / "config"),
    )
    return open_registry_state(tmp_path)


def test_watcher_persists_unresolved_nested_path(monkeypatch, tmp_path):
    root = tmp_path / "library"
    nested = root / "nested" / "paper.pdf"
    nested.parent.mkdir(parents=True)
    nested.write_bytes(b"%PDF")
    state = _state(monkeypatch, root)
    watcher = PdfWatcher(_Identifier(None), _Renamer())

    assert watcher.process_new_pdf(nested, state) is None

    record = state.registry.find_file_by_relative_path(root, "nested/paper.pdf")
    assert record is not None
    assert record["status"] == "unresolved"
    assert record["sha256"] == "watch-sha"
    state.close()


def test_watcher_registers_resolved_nested_path_without_batch_rename(
    monkeypatch, tmp_path
):
    root = tmp_path / "library"
    nested = root / "nested" / "paper.pdf"
    nested.parent.mkdir(parents=True)
    nested.write_bytes(b"%PDF")
    state = _state(monkeypatch, root)
    watcher = PdfWatcher(
        _Identifier({"bibcode": "B1", "sha256": "watch-sha"}),
        _Renamer(),
    )

    record = watcher.process_new_pdf(nested, state)

    assert record is not None
    stored = state.registry.find_file_by_relative_path(root, "nested/paper.pdf")
    assert stored is not None
    assert stored["bibcode"] == "B1"
    state.close()


def test_watcher_records_new_content_version_for_existing_path(monkeypatch, tmp_path):
    root = tmp_path / "library"
    pdf_path = root / "paper.pdf"
    root.mkdir()
    pdf_path.write_bytes(b"first")
    state = _state(monkeypatch, root)
    identifier = _ChangingIdentifier({"bibcode": "B1"})
    watcher = PdfWatcher(identifier, _Renamer())

    watcher.process_new_pdf(pdf_path, state)
    first = state.registry.find_file_by_relative_path(root, "paper.pdf")
    assert first is not None

    pdf_path.write_bytes(b"second")
    identifier.sha256 = "watch-sha-2"
    watcher.process_new_pdf(pdf_path, state)

    second = state.registry.find_file_by_relative_path(root, "paper.pdf")
    assert second is not None
    assert second["sha256"] == "watch-sha-2"
    assert len(state.registry.catalog.content_versions(first["attachment_id"])) == 2
    state.close()


def test_watcher_retries_unchanged_unresolved_pdf(monkeypatch, tmp_path):
    root = tmp_path / "library"
    pdf_path = root / "paper.pdf"
    root.mkdir()
    pdf_path.write_bytes(b"%PDF")
    state = _state(monkeypatch, root)
    state.upsert_file(
        "paper.pdf",
        {
            "current_filename": "paper.pdf",
            "sha256": "watch-sha",
            "status": "unresolved",
        },
    )
    watcher = PdfWatcher(
        _Identifier(
            {
                "bibcode": "2022A&A...668A.132B",
                "doi": "10.1051/0004-6361/202243983",
            }
        ),
        _Renamer(),
    )

    record = watcher.process_new_pdf(pdf_path, state)

    assert record is not None
    assert record["bibcode"] == "2022A&A...668A.132B"
    stored = state.registry.find_file_by_relative_path(root, "paper.pdf")
    assert stored is not None
    assert stored["status"] == "resolved"
    assert stored["bibcode"] == "2022A&A...668A.132B"
    state.close()


def test_watcher_does_not_suffix_unresolved_pdf_already_in_output_directory(
    monkeypatch, tmp_path
):
    root = tmp_path / "library"
    output = root / "unresolved"
    pdf_path = output / "paper.pdf"
    output.mkdir(parents=True)
    pdf_path.write_bytes(b"%PDF")
    state = _state(monkeypatch, root)
    state.upsert_file(
        "unresolved/paper.pdf",
        {
            "current_filename": "unresolved/paper.pdf",
            "sha256": "watch-sha",
            "status": "unresolved",
        },
    )
    watcher = PdfWatcher(_Identifier(None), _Renamer())

    record = watcher.process_new_pdf(
        pdf_path,
        state,
        output_unresolved=output,
    )

    assert record is None
    assert pdf_path.exists()
    assert not (output / "paper_1.pdf").exists()
    stored = state.registry.find_file_by_relative_path(root, "unresolved/paper.pdf")
    assert stored is not None
    assert stored["status"] == "unresolved"
    state.close()


def test_polling_skips_unchanged_registered_pdf(monkeypatch, tmp_path):
    root = tmp_path / "library"
    pdf_path = root / "paper.pdf"
    root.mkdir()
    pdf_path.write_bytes(b"%PDF")
    state = _state(monkeypatch, root)
    identifier = _CountingIdentifier({"bibcode": "B1"})
    watcher = PdfWatcher(identifier, _Renamer())

    class _StopAfterSecondCheck:
        checks = 0

        def is_set(self):
            self.checks += 1
            return self.checks > 1

    stop_event = _StopAfterSecondCheck()
    watcher.poll_loop(
        root,
        state,
        interval=0,
        callback=lambda path: watcher.process_new_pdf(path, state),
        stop_event=stop_event,
    )

    assert identifier.hash_calls == 1
    state.close()


def test_move_tracked_file_updates_catalog_path(monkeypatch, tmp_path):
    root = tmp_path / "library"
    source = root / "paper.pdf"
    source.parent.mkdir()
    source.write_bytes(b"%PDF")
    state = _state(monkeypatch, root)
    state.upsert_file(
        "paper.pdf",
        {"current_filename": "paper.pdf", "sha256": "watch-sha"},
    )
    destination = root / "unresolved" / "paper.pdf"

    assert move_tracked_file(state, source, destination) == destination

    assert not source.exists()
    assert destination.exists()
    assert (
        state.registry.find_file_by_relative_path(root, "unresolved/paper.pdf")
        is not None
    )
    active_paths = {
        record["current_path"]
        for record in state.registry.catalog.query_files(root=root)
    }
    assert active_paths == {"unresolved/paper.pdf"}
    state.close()


def test_move_tracked_file_rolls_back_when_catalog_update_fails(monkeypatch, tmp_path):
    root = tmp_path / "library"
    source = root / "paper.pdf"
    source.parent.mkdir()
    source.write_bytes(b"%PDF")
    state = _state(monkeypatch, root)
    state.upsert_file(
        "paper.pdf",
        {"current_filename": "paper.pdf", "sha256": "watch-sha"},
    )
    destination = root / "renamed.pdf"
    monkeypatch.setattr(
        state,
        "set_current_filename",
        lambda *args: (_ for _ in ()).throw(RuntimeError("catalog failed")),
    )

    with pytest.raises(RuntimeError, match="catalog failed"):
        move_tracked_file(state, source, destination)

    assert source.exists()
    assert not destination.exists()
    state.close()


def test_watcher_moves_unresolved_and_updates_catalog(monkeypatch, tmp_path):
    root = tmp_path / "library"
    source = root / "paper.pdf"
    source.parent.mkdir()
    source.write_bytes(b"%PDF")
    state = _state(monkeypatch, root)
    watcher = PdfWatcher(_Identifier(None), _Renamer())

    assert (
        watcher.process_new_pdf(
            source,
            state,
            output_unresolved=root / "unresolved",
            apply=True,
        )
        is None
    )

    assert not source.exists()
    assert (root / "unresolved" / "paper.pdf").exists()
    assert (
        state.registry.find_file_by_relative_path(root, "unresolved/paper.pdf")
        is not None
    )
    state.close()


def test_watcher_renames_only_resolved_new_pdf(monkeypatch, tmp_path):
    root = tmp_path / "library"
    source = root / "paper.pdf"
    source.parent.mkdir()
    source.write_bytes(b"%PDF")
    state = _state(monkeypatch, root)
    watcher = PdfWatcher(
        _Identifier(
            {
                "bibcode": "2025RNAAS...9..338Z",
                "title": ["Aperture-averaged Polarimetry"],
                "author": ["Zheltobryukhov, Maxim"],
            }
        ),
        PdfRenamer(),
    )

    record = watcher.process_new_pdf(
        source,
        state,
        scheme="{bibcode}",
        apply=True,
    )

    target = root / "2025RNAAS...9..338Z.pdf"
    assert record is not None
    assert record["current_filename"] == target.name
    assert target.exists()
    assert not source.exists()
    assert state.registry.find_file_by_relative_path(root, target.name) is not None
    state.close()


def test_watcher_defers_unreadable_pdf_without_catalog_mutation(monkeypatch, tmp_path):
    root = tmp_path / "library"
    source = root / "partial.pdf"
    source.parent.mkdir()
    source.write_bytes(b"%PDF")
    state = _state(monkeypatch, root)
    identifier = _Identifier(None)
    identifier.pdf_is_readable = lambda path: False
    watcher = PdfWatcher(identifier, _Renamer())

    with pytest.raises(PdfNotReadyError):
        watcher.process_new_pdf(source, state)

    assert state.registry.find_file_by_relative_path(root, "partial.pdf") is None
    state.close()


def test_watcher_defers_pdf_that_changes_during_hash(monkeypatch, tmp_path):
    root = tmp_path / "library"
    source = root / "changing.pdf"
    source.parent.mkdir()
    source.write_bytes(b"%PDF")
    state = _state(monkeypatch, root)
    watcher = PdfWatcher(_Identifier(None), _Renamer())
    facts = iter([(4, 1, 2, 3), (5, 2, 2, 3)])
    monkeypatch.setattr(watcher, "_file_facts", lambda path: next(facts))

    with pytest.raises(PdfNotReadyError):
        watcher.process_new_pdf(source, state)

    assert state.registry.find_file_by_relative_path(root, "changing.pdf") is None
    state.close()


def test_move_tracked_file_uses_original_legacy_sidecar_key(tmp_path):
    source = tmp_path / "nested" / "paper.pdf"
    source.parent.mkdir()
    source.write_bytes(b"%PDF")
    sidecar = Sidecar(tmp_path)
    sidecar.upsert_file(
        "original.pdf",
        {
            "current_filename": "nested/paper.pdf",
            "sha256": "watch-sha",
            "status": "unresolved",
        },
    )
    destination = tmp_path / "unresolved" / "paper.pdf"

    move_tracked_file(sidecar, source, destination)

    assert sidecar.get_file("original.pdf")["current_filename"] == (
        "unresolved/paper.pdf"
    )
    assert destination.exists()
    reloaded = Sidecar(tmp_path)
    assert reloaded.get_file("original.pdf")["current_filename"] == (
        "unresolved/paper.pdf"
    )


@pytest.mark.parametrize("filename", ["cached.pdf", "B1.pdf"])
def test_watcher_preserves_cached_sidecar_record_during_rename(
    tmp_path, filename: str
) -> None:
    source = tmp_path / filename
    source.write_bytes(b"%PDF cached")
    sidecar = Sidecar(tmp_path)
    sidecar.upsert_file(
        "download.pdf",
        {
            "current_filename": filename,
            "sha256": "watch-sha",
            "status": "resolved",
            "bibcode": "B1",
            "notes": "Keep this note",
        },
    )
    watcher = PdfWatcher(_NoResolveIdentifier(None), PdfRenamer())

    record = watcher.process_new_pdf(source, sidecar, scheme="{bibcode}")

    assert record is not None
    assert record["current_filename"] == "B1.pdf"
    assert (tmp_path / "B1.pdf").read_bytes() == b"%PDF cached"
    reloaded = Sidecar(tmp_path)
    assert set(reloaded.files) == {"download.pdf"}
    assert reloaded.get_file("download.pdf")["current_filename"] == "B1.pdf"
    assert reloaded.get_file("download.pdf")["notes"] == "Keep this note"


def test_watcher_dry_run_does_not_mutate_catalog_or_files(monkeypatch, tmp_path):
    root = tmp_path / "library"
    source = root / "paper.pdf"
    source.parent.mkdir()
    source.write_bytes(b"%PDF")
    state = _state(monkeypatch, root)
    watcher = PdfWatcher(
        _Identifier(
            {
                "bibcode": "2025RNAAS...9..338Z",
                "title": ["Aperture-averaged Polarimetry"],
                "author": ["Zheltobryukhov, Maxim"],
            }
        ),
        PdfRenamer(),
    )

    record = watcher.process_new_pdf(
        source,
        state,
        scheme="{bibcode}",
        output_unresolved=root / "unresolved",
        apply=False,
    )

    assert record is not None
    assert record["planned_filename"] == "2025RNAAS...9..338Z.pdf"
    assert source.exists()
    assert not (root / "2025RNAAS...9..338Z.pdf").exists()
    assert state.registry.catalog.query_files(root=root) == []
    state.close()


def test_duplicate_hash_arrival_is_renamed_without_ads_query(monkeypatch, tmp_path):
    root = tmp_path / "library"
    existing = root / "existing.pdf"
    incoming = root / "download.pdf"
    root.mkdir()
    existing.write_bytes(b"first")
    incoming.write_bytes(b"same content represented by test digest")
    state = _state(monkeypatch, root)
    state.upsert_file(
        "existing.pdf",
        {
            "bibcode": "2024ApJ...900..100S",
            "title": ["Paper Title"],
            "author": ["Smith, John"],
            "current_filename": "existing.pdf",
            "sha256": "watch-sha",
        },
    )
    identifier = _Identifier(None)
    watcher = PdfWatcher(identifier, PdfRenamer())

    record = watcher.process_new_pdf(
        incoming,
        state,
        scheme="{bibcode}",
        apply=True,
    )

    assert record is not None
    assert not incoming.exists()
    assert (root / "2024ApJ...900..100S.pdf").exists()
    state.close()


def test_nonrecursive_poll_does_not_mark_nested_locations_missing(
    monkeypatch, tmp_path
):
    root = tmp_path / "library"
    top = root / "top.pdf"
    nested = root / "unresolved" / "nested.pdf"
    nested.parent.mkdir(parents=True)
    top.write_bytes(b"top")
    nested.write_bytes(b"nested")
    state = _state(monkeypatch, root)
    for path in (top, nested):
        relative = path.relative_to(root).as_posix()
        state.upsert_file(
            relative,
            {"current_filename": relative, "sha256": f"sha-{relative}"},
        )

    class _StopAfterFirstScan:
        def is_set(self):
            return True

    PdfWatcher(_Identifier(None), _Renamer()).poll_loop(
        root,
        state,
        interval=0,
        recursive=False,
        callback=lambda path: None,
        stop_event=_StopAfterFirstScan(),
    )

    active_paths = {
        record["current_path"]
        for record in state.registry.catalog.query_files(root=root)
    }
    assert active_paths == {"top.pdf", "unresolved/nested.pdf"}
    state.close()


def _watch_event(root: Path, path: Path) -> WatchEvent:
    stat = path.stat()
    return WatchEvent(
        root=root,
        session_id="session-1",
        identity=ArrivalIdentity(
            device=stat.st_dev,
            inode=stat.st_ino,
            birth_time_ns=getattr(stat, "st_birthtime_ns", None),
            generation=0,
        ),
        path=path,
        state=WatchState.DETECTED,
    )


@pytest.mark.parametrize("output_unresolved", [".", ".."])
def test_watch_identify_policy_rejects_skip_destination_containing_root(
    tmp_path: Path,
    output_unresolved: str,
) -> None:
    root = tmp_path / "library"
    root.mkdir()

    with pytest.raises(ValueError, match="contains the watched root"):
        WatchIdentifyPolicy.from_settings(
            root,
            IdentifySettings(
                output_unresolved=output_unresolved,
                skip_unresolved_directory=True,
            ),
        )


def test_process_event_reports_renamed_destination(monkeypatch, tmp_path):
    root = tmp_path / "library"
    source = root / "incoming.pdf"
    root.mkdir()
    source.write_bytes(b"%PDF")
    state = _state(monkeypatch, root)
    watcher = PdfWatcher(
        _Identifier(
            {
                "bibcode": "2025RNAAS...9..338Z",
                "title": ["A title"],
                "author": ["Smith, Jane"],
            }
        ),
        PdfRenamer(),
    )

    outcome = watcher.process_event(
        _watch_event(root, source),
        state,
        RenameSettings(template="{bibcode}"),
        IdentifySettings(),
    )

    assert outcome.event.state is WatchState.RENAMED
    assert outcome.destination == root / "2025RNAAS...9..338Z.pdf"
    state.close()


def test_process_event_applies_saved_author_join_settings(monkeypatch, tmp_path):
    root = tmp_path / "library"
    source = root / "incoming.pdf"
    root.mkdir()
    source.write_bytes(b"%PDF")
    state = _state(monkeypatch, root)
    watcher = PdfWatcher(
        _Identifier(
            {
                "bibcode": "2025RNAAS...9..338Z",
                "author": ["Smith, Jane", "Doe, John", "Lee, Alex"],
            }
        ),
        PdfRenamer(),
    )

    outcome = watcher.process_event(
        _watch_event(root, source),
        state,
        RenameSettings(
            template="{authors}",
            max_authors=2,
            authors_when_truncated=2,
            author_separator="~",
            author_truncation_suffix="++",
        ),
        IdentifySettings(),
    )

    assert outcome.event.state is WatchState.RENAMED
    assert outcome.destination == root / "SmithJ~DoeJ++.pdf"
    state.close()


def test_process_event_passes_configured_strategy_order_to_resolver(
    monkeypatch, tmp_path
) -> None:
    root = tmp_path / "library"
    source = root / "2025RNAAS...9..338Z.pdf"
    root.mkdir()
    source.write_bytes(b"%PDF")
    state = _state(monkeypatch, root)
    identifier = _StrategyRecordingIdentifier(
        {"bibcode": "2025RNAAS...9..338Z", "title": ["Inferred"]}
    )
    watcher = PdfWatcher(identifier, _Renamer())

    outcome = watcher.process_event(
        _watch_event(root, source),
        state,
        RenameSettings(),
        IdentifySettings(
            strategies=(
                IdentifyStrategy.FIRST_PAGE_DOI,
                IdentifyStrategy.FILENAME_BIBCODE,
            )
        ),
    )

    assert outcome.event.state is WatchState.RESOLVED
    assert identifier.hash_calls == 1
    assert len(identifier.resolve_many_calls) == 1
    paths, _journal_map, kwargs = identifier.resolve_many_calls[0]
    assert paths == [source]
    assert kwargs == {
        "strategies": (
            IdentifyStrategy.FIRST_PAGE_DOI,
            IdentifyStrategy.FILENAME_BIBCODE,
        ),
        "sha256_by_path": {source: "watch-sha"},
        "providers": DEFAULT_DOI_PROVIDERS,
    }
    state.close()


def test_process_event_skips_unresolved_directory_without_mutation_or_resolution(
    monkeypatch, tmp_path
) -> None:
    root = tmp_path / "library"
    source = root / "unresolved" / "unknown.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"%PDF")
    state = _state(monkeypatch, root)
    identifier = _StrategyRecordingIdentifier(None)
    watcher = PdfWatcher(identifier, _Renamer())

    outcome = watcher.process_event(
        _watch_event(root, source),
        state,
        RenameSettings(),
        IdentifySettings(
            skip_unresolved_directory=True,
        ),
    )

    assert outcome.event.state is WatchState.DISMISSED
    assert identifier.hash_calls == 0
    assert identifier.resolve_many_calls == []
    assert (
        state.registry.find_file_by_relative_path(root, "unresolved/unknown.pdf")
        is None
    )
    assert source.read_bytes() == b"%PDF"
    state.close()


def test_process_event_reports_invalid_persisted_skip_geometry_as_failure(
    monkeypatch,
    tmp_path,
) -> None:
    root = tmp_path / "library"
    source = root / "unknown.pdf"
    root.mkdir()
    source.write_bytes(b"%PDF")
    state = _state(monkeypatch, root)
    identifier = _StrategyRecordingIdentifier(None)
    watcher = PdfWatcher(identifier, _Renamer())

    outcome = watcher.process_event(
        _watch_event(root, source),
        state,
        RenameSettings(),
        IdentifySettings(
            output_unresolved=".",
            skip_unresolved_directory=True,
        ),
    )

    assert outcome.event.state is WatchState.FAILED
    assert "contains the watched root" in outcome.message
    assert identifier.hash_calls == 0
    assert identifier.resolve_many_calls == []
    assert state.registry.find_file_by_relative_path(root, "unknown.pdf") is None
    assert source.read_bytes() == b"%PDF"
    state.close()


def test_process_event_maps_not_ready_to_deferred(monkeypatch, tmp_path):
    root = tmp_path / "library"
    source = root / "partial.pdf"
    root.mkdir()
    source.write_bytes(b"%PDF")
    state = _state(monkeypatch, root)
    watcher = PdfWatcher(_Identifier(None), _Renamer())
    monkeypatch.setattr(
        watcher,
        "process_new_pdf",
        lambda **kwargs: (_ for _ in ()).throw(PdfNotReadyError("not ready")),
    )

    outcome = watcher.process_event(
        _watch_event(root, source),
        state,
        RenameSettings(),
        IdentifySettings(),
    )

    assert outcome.event.state is WatchState.DEFERRED
    assert outcome.destination is None
    state.close()


def test_process_event_applies_relative_unresolved_destination(monkeypatch, tmp_path):
    root = tmp_path / "library"
    source = root / "unknown.pdf"
    root.mkdir()
    source.write_bytes(b"%PDF")
    state = _state(monkeypatch, root)
    watcher = PdfWatcher(_Identifier(None), _Renamer())

    outcome = watcher.process_event(
        _watch_event(root, source),
        state,
        RenameSettings(),
        IdentifySettings(output_unresolved="unresolved"),
    )

    assert outcome.event.state is WatchState.UNRESOLVED
    assert outcome.destination == root / "unresolved" / "unknown.pdf"
    assert outcome.destination.exists()
    assert not source.exists()
    state.close()


@pytest.mark.parametrize("cached", [False, True])
def test_process_event_reports_collision_without_overwrite(
    monkeypatch, tmp_path, cached
) -> None:
    root = tmp_path / "library"
    source = root / "incoming.pdf"
    occupied = root / "2025RNAAS...9..338Z.pdf"
    root.mkdir()
    source.write_bytes(b"%PDF source")
    occupied.write_bytes(b"occupied")
    state = _state(monkeypatch, root)
    if cached:
        state.upsert_file(
            source.name,
            {
                "current_filename": source.name,
                "sha256": "watch-sha",
                "status": "resolved",
                "bibcode": "2025RNAAS...9..338Z",
            },
        )
    watcher = PdfWatcher(
        _NoResolveIdentifier(None)
        if cached
        else _Identifier({"bibcode": "2025RNAAS...9..338Z"}),
        PdfRenamer(),
    )

    outcome = watcher.process_event(
        _watch_event(root, source),
        state,
        RenameSettings(template="{bibcode}"),
        IdentifySettings(),
    )

    assert outcome.event.state is WatchState.FAILED
    assert str(occupied) in (outcome.message or "")
    assert source.read_bytes() == b"%PDF source"
    assert occupied.read_bytes() == b"occupied"
    state.close()


def test_process_event_reports_ads_failure_without_moving_source(monkeypatch, tmp_path):
    root = tmp_path / "library"
    source = root / "incoming.pdf"
    root.mkdir()
    source.write_bytes(b"%PDF")
    state = _state(monkeypatch, root)
    watcher = PdfWatcher(_Identifier(None), _Renamer())
    monkeypatch.setattr(
        watcher,
        "process_new_pdf",
        lambda **kwargs: (_ for _ in ()).throw(
            AuthenticationError("missing ADS token")
        ),
    )

    outcome = watcher.process_event(
        _watch_event(root, source),
        state,
        RenameSettings(),
        IdentifySettings(),
    )

    assert outcome.event.state is WatchState.FAILED
    assert "missing ADS token" in (outcome.message or "")
    assert source.exists()
    state.close()


@pytest.mark.parametrize("apply", [False, True])
@pytest.mark.parametrize("configured", [False, True])
def test_watcher_relocates_fresh_resolved_pdf_from_nested_unresolved(
    monkeypatch, tmp_path, configured, apply
):
    root = tmp_path / "library"
    unresolved = root / "review" / "pending" if configured else root / "unresolved"
    source = unresolved / "nested" / "paper.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"%PDF fresh")
    state = _state(monkeypatch, root)
    watcher = PdfWatcher(
        _Identifier(
            {
                "bibcode": "B1",
                "title": ["Nested paper"],
                "author": ["Smith, Jane"],
            }
        ),
        PdfRenamer(),
    )

    record = watcher.process_new_pdf(
        source,
        state,
        scheme="{bibcode}",
        output_unresolved=unresolved if configured else None,
        apply=apply,
    )

    target = root / "B1.pdf"
    if apply:
        assert record is not None
        assert record["current_filename"] == target.name
        assert target.read_bytes() == b"%PDF fresh"
        assert not source.exists()
        stored = state.registry.find_file_by_relative_path(root, target.name)
        assert stored is not None
        assert stored["bibcode"] == "B1"
        assert stored["status"] == "resolved"
    else:
        assert record is not None
        assert record["planned_filename"] == target.name
        assert source.exists()
        assert not target.exists()
        assert state.registry.catalog.query_files(root=root) == []
    state.close()


@pytest.mark.parametrize("apply", [False, True])
@pytest.mark.parametrize("configured", [False, True])
@pytest.mark.parametrize("from_unresolved", [False, True])
def test_watcher_renames_cached_resolved_pdf(
    monkeypatch, tmp_path, configured, apply, from_unresolved
) -> None:
    root = tmp_path / "library"
    unresolved = root / "review" / "pending" if configured else root / "unresolved"
    source = (
        unresolved / "nested" / "cached.pdf" if from_unresolved else root / "cached.pdf"
    )
    source.parent.mkdir(parents=True)
    source.write_bytes(b"%PDF cached")
    relative = source.relative_to(root).as_posix()
    state = _state(monkeypatch, root)
    state.upsert_file(
        relative,
        {
            "current_filename": relative,
            "sha256": "watch-sha",
            "status": "resolved",
            "bibcode": "B2",
            "title": ["Cached paper"],
            "author": ["Doe, Alex"],
        },
    )
    watcher = PdfWatcher(_NoResolveIdentifier(None), PdfRenamer())

    record = watcher.process_new_pdf(
        source,
        state,
        scheme="{bibcode}",
        output_unresolved=unresolved if configured else None,
        apply=apply,
    )

    target = root / "B2.pdf"
    if apply:
        assert record is not None
        assert record["current_filename"] == target.name
        assert target.read_bytes() == b"%PDF cached"
        assert not source.exists()
        assert state.registry.find_file_by_relative_path(root, relative) is None
        stored = state.registry.find_file_by_relative_path(root, target.name)
        assert stored is not None
        assert stored["bibcode"] == "B2"
        assert stored["title"] == ["Cached paper"]
        assert stored["status"] == "resolved"
        repeated = watcher.process_new_pdf(target, state, scheme="{bibcode}")
        assert repeated is not None
        assert repeated["current_filename"] == target.name
        assert target.read_bytes() == b"%PDF cached"
    else:
        assert record is not None
        assert record["planned_filename"] == target.name
        assert source.exists()
        assert not target.exists()
        stored = state.registry.find_file_by_relative_path(root, relative)
        assert stored is not None
        assert stored["bibcode"] == "B2"
        assert stored["current_path"] == relative
    state.close()


@pytest.mark.parametrize("configured", [False, True])
def test_process_event_reports_destination_after_unresolved_relocation(
    monkeypatch, tmp_path, configured
):
    root = tmp_path / "library"
    unresolved = root / "review" / "pending" if configured else root / "unresolved"
    source = unresolved / "nested" / "event.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"%PDF event")
    state = _state(monkeypatch, root)
    watcher = PdfWatcher(
        _Identifier({"bibcode": "B3", "title": ["Event paper"]}),
        PdfRenamer(),
    )

    outcome = watcher.process_event(
        _watch_event(root, source),
        state,
        RenameSettings(template="{bibcode}"),
        IdentifySettings(output_unresolved="review/pending" if configured else None),
    )

    target = root / "B3.pdf"
    assert outcome.event.state is WatchState.RENAMED
    assert outcome.destination == target
    assert target.read_bytes() == b"%PDF event"
    assert not source.exists()
    stored = state.registry.find_file_by_relative_path(root, target.name)
    assert stored is not None
    assert stored["bibcode"] == "B3"
    state.close()


def test_process_event_relocation_collision_keeps_source_and_metadata(
    monkeypatch, tmp_path
):
    root = tmp_path / "library"
    unresolved = root / "review" / "pending"
    source = unresolved / "nested" / "collision.pdf"
    target = root / "B4.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"%PDF source")
    target.write_bytes(b"%PDF existing")
    state = _state(monkeypatch, root)
    watcher = PdfWatcher(
        _Identifier(
            {
                "bibcode": "B4",
                "title": ["Retain this metadata"],
                "author": ["Smith, Jane"],
            }
        ),
        PdfRenamer(),
    )

    outcome = watcher.process_event(
        _watch_event(root, source),
        state,
        RenameSettings(template="{bibcode}"),
        IdentifySettings(output_unresolved="review/pending"),
    )

    relative = source.relative_to(root).as_posix()
    assert outcome.event.state is WatchState.FAILED
    assert source.read_bytes() == b"%PDF source"
    assert target.read_bytes() == b"%PDF existing"
    stored = state.registry.find_file_by_relative_path(root, relative)
    assert stored is not None
    assert stored["bibcode"] == "B4"
    assert stored["title"] == ["Retain this metadata"]
    assert stored["status"] == "resolved"
    state.close()


def _active_paths(state, root: Path) -> set[str]:
    return {
        record["current_path"]
        for record in state.registry.catalog.query_files(root=root)
    }


class _StopAfterFirstScan:
    def is_set(self):
        return True


def _poll_once(watcher, root, state, handled=None):
    watcher.poll_loop(
        root,
        state,
        interval=0,
        recursive=True,
        callback=(handled.append if handled is not None else (lambda path: None)),
        stop_event=_StopAfterFirstScan(),
    )


def _track(state, root: Path, *relative_paths: str) -> None:
    for relative in relative_paths:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(relative.encode())
        state.upsert_file(
            relative, {"current_filename": relative, "sha256": f"sha-{relative}"}
        )


def test_poll_detects_uppercase_pdf_suffix(monkeypatch, tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    (root / "PAPER.PDF").write_bytes(b"%PDF")
    state = _state(monkeypatch, root)
    handled: list[Path] = []

    _poll_once(PdfWatcher(_Identifier(None), _Renamer()), root, state, handled)

    assert [path.name for path in handled] == ["PAPER.PDF"]
    state.close()


def test_stat_permission_error_keeps_tracked_files_and_loop_alive(
    monkeypatch, tmp_path
):
    root = tmp_path / "library"
    root.mkdir()
    state = _state(monkeypatch, root)
    _track(state, root, "ok.pdf", "locked.pdf")
    watcher = PdfWatcher(_Identifier(None), _Renamer())
    real_facts = PdfWatcher._file_facts

    def facts(path: Path):
        if path.name == "locked.pdf":
            raise PermissionError("denied")
        return real_facts(path)

    monkeypatch.setattr(PdfWatcher, "_file_facts", staticmethod(facts))

    _poll_once(watcher, root, state)

    assert _active_paths(state, root) == {"ok.pdf", "locked.pdf"}
    state.close()


def test_unlistable_directory_keeps_tracked_files(monkeypatch, tmp_path):
    import os

    from adsfolio.pdf import files

    root = tmp_path / "library"
    root.mkdir()
    state = _state(monkeypatch, root)
    _track(state, root, "top.pdf", "sub/nested.pdf")
    real_scandir = os.scandir

    def deny(path):
        if Path(path).name == "sub":
            raise PermissionError("denied")
        return real_scandir(path)

    monkeypatch.setattr(files.os, "scandir", deny)

    _poll_once(PdfWatcher(_Identifier(None), _Renamer()), root, state)

    assert _active_paths(state, root) == {"top.pdf", "sub/nested.pdf"}
    state.close()


def test_complete_scan_still_marks_deleted_file_missing(monkeypatch, tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    state = _state(monkeypatch, root)
    _track(state, root, "keep.pdf", "gone.pdf")
    (root / "gone.pdf").unlink()

    _poll_once(PdfWatcher(_Identifier(None), _Renamer()), root, state)

    assert _active_paths(state, root) == {"keep.pdf"}
    state.close()


def test_symlink_outside_root_does_not_stop_scan_or_hide_known_files(
    monkeypatch, tmp_path
):
    root = tmp_path / "library"
    root.mkdir()
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF")
    state = _state(monkeypatch, root)
    _track(state, root, "known.pdf")
    (root / "link.pdf").symlink_to(outside)
    handled: list[Path] = []

    _poll_once(PdfWatcher(_Identifier(None), _Renamer()), root, state, handled)

    assert handled == []
    assert _active_paths(state, root) == {"known.pdf"}
    state.close()
