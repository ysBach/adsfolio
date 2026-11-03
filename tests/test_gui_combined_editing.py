"""Test editing selected PDFs in combined libraries."""

from __future__ import annotations

import hashlib
import socket
from copy import deepcopy
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from pypdf import PdfWriter
from PySide6.QtCore import QItemSelectionModel, QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtWidgets import QInputDialog, QMenu, QMessageBox

from adsfolio.config import Config
from adsfolio.gui.config import GuiConfig, RootEntry, save_config
from adsfolio.gui.main_window import MainWindow
from adsfolio.pdf.catalog import CITE_KEY_METADATA_KEY
from adsfolio.pdf.cli import pdf
from adsfolio.pdf.registry import PdfRegistry
from adsfolio.pdf.rename_settings import RenameSettings


@pytest.fixture
def combined_editing(qtbot, monkeypatch, tmp_path):
    config_dir = tmp_path / "config"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: config_dir))
    monkeypatch.setattr(MainWindow, "_start_configured_watchers", lambda self: None)
    attempts = []

    def deny_network(*args, **kwargs):
        attempts.append((args, kwargs))
        raise AssertionError("The test must not use the network")

    monkeypatch.setattr(socket, "create_connection", deny_network)
    monkeypatch.setattr(socket.socket, "connect", deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_network)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: QMessageBox.Ok)
    monkeypatch.setattr(
        QMessageBox, "information", lambda *args, **kwargs: QMessageBox.Ok
    )
    roots = [tmp_path / "first", tmp_path / "second"]
    for root_index, root in enumerate(roots):
        root.mkdir()
        registry = PdfRegistry(root)
        try:
            for name in ("same.pdf", "other.pdf"):
                path = root / name
                writer = PdfWriter()
                writer.add_blank_page(width=32, height=32)
                writer.add_metadata({"/Title": f"Fictional {root_index} {name}"})
                writer.write(path)
                content = path.read_bytes()
                stat = path.stat()
                registry.upsert_file(
                    root=root,
                    relative_path=name,
                    current_path=name,
                    sha256=hashlib.sha256(content).hexdigest(),
                    size_bytes=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                    status="unresolved",
                    metadata={
                        "title": [f"Original {root_index} {name}"],
                        "notes": f"Keep {root_index} {name}",
                    },
                )
        finally:
            registry.close()
    save_config(
        GuiConfig(
            roots=[
                RootEntry(str(root), label=f"Library {i}")
                for i, root in enumerate(roots)
            ],
            check_updates_automatically=False,
            citation_styles=[],
        )
    )
    window = MainWindow()
    qtbot.addWidget(window)
    window.combined.set_enabled(True)
    assert window.model.total == 4
    try:
        yield window, roots
    finally:
        window._ads_recheck_active_root = None
        window._manual_metadata_target = None
        window.close()
        assert attempts == []


def _row(window, root, name="same.pdf"):
    return next(
        row
        for row in range(window.model.rowCount())
        if window.model.record_at(row)["root"] == str(root)
        and window.model.record_at(row)["current_path"] == name
    )


def _select(window, root, name="same.pdf"):
    row = _row(window, root, name)
    index = window._view_table_index(window.model.index(row, 0))
    assert index.isValid()
    window.table.expandAll()
    window.table.selectionModel().setCurrentIndex(
        index, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows
    )
    return deepcopy(window.model.record_at(row))


def _catalog_state(window, roots):
    state = {}
    for root in roots:
        for name in ("same.pdf", "other.pdf"):
            record = window.registries[str(root)].find_file_by_current_path(root, name)
            assert record is not None
            state[(root, name)] = {
                "location_id": record["location_id"],
                "attachment_id": record["id"],
                "metadata": deepcopy(record["metadata"]),
                "sha256": record["sha256"],
                "current_path": record["current_path"],
            }
    return state


def _pdf_hashes(roots):
    return {
        root / name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for root in roots
        for name in ("same.pdf", "other.pdf")
    }


def _block_dialogs_and_launches(window, monkeypatch):
    calls = []
    monkeypatch.setattr(
        QInputDialog,
        "getText",
        lambda *args, **kwargs: (
            calls.append("identifier input") or ("10.1234/test", True)
        ),
    )
    monkeypatch.setattr(
        "adsfolio.gui.main_window.extract_pdf_own_identity",
        lambda path: (
            calls.append(("identity", path)) or SimpleNamespace(has_identifier=True)
        ),
    )
    monkeypatch.setattr(
        window,
        "_start_ads_refresh",
        lambda *args, **kwargs: calls.append(("ADS refresh", args, kwargs)),
    )
    monkeypatch.setattr(
        window,
        "_request_foreground_dialog",
        lambda *args, **kwargs: calls.append(("foreground", args, kwargs)),
    )
    return calls


@pytest.mark.parametrize("selected_index", [0, 1])
def test_inline_edit_and_lock_only_change_selected_attachment(
    combined_editing, monkeypatch, selected_index
):
    window, roots = combined_editing
    root = roots[selected_index]
    window.current_root = roots[1 - selected_index]
    selected = _select(window, root)
    before = _catalog_state(window, roots)
    hashes = _pdf_hashes(roots)
    monkeypatch.setattr(
        QInputDialog,
        "getMultiLineText",
        lambda *args, **kwargs: ("Revised title", True),
    )

    row = window.detail._row_widgets["title"]
    assert window.detail._root == root
    assert row._editable and row._lockable
    row._request_edit()
    assert window._selected_record()["location_id"] == selected["location_id"]
    window.detail._row_widgets["title"].lock_button.click()

    after = _catalog_state(window, roots)
    changed = after.pop((root, "same.pdf"))
    previous = before.pop((root, "same.pdf"))
    assert after == before
    assert changed["location_id"] == previous["location_id"]
    assert changed["attachment_id"] == previous["attachment_id"]
    assert changed["current_path"] == previous["current_path"]
    assert changed["sha256"] == previous["sha256"]
    assert changed["metadata"]["title"] == ["Revised title"]
    assert changed["metadata"]["_adsfolio_locked_fields"] == ["title"]
    assert changed["metadata"]["notes"] == previous["metadata"]["notes"]
    assert window.combined.enabled
    assert window._selected_record()["location_id"] == selected["location_id"]
    assert _pdf_hashes(roots) == hashes


def test_switching_same_named_pdfs_keeps_edits_with_each_attachment(
    combined_editing, monkeypatch
):
    window, roots = combined_editing
    hashes = _pdf_hashes(roots)
    answers = iter(("First revised title", "Second revised title"))
    monkeypatch.setattr(
        QInputDialog,
        "getMultiLineText",
        lambda *args, **kwargs: (next(answers), True),
    )

    for index, root in enumerate(roots):
        window.current_root = roots[1 - index]
        selected = _select(window, root)
        window.detail._row_widgets["title"]._request_edit()
        window.detail._row_widgets["title"].lock_button.click()
        assert window._selected_record()["location_id"] == selected["location_id"]

    state = _catalog_state(window, roots)
    for root, title in zip(roots, ("First revised title", "Second revised title")):
        assert state[(root, "same.pdf")]["metadata"]["title"] == [title]
        assert state[(root, "same.pdf")]["metadata"]["_adsfolio_locked_fields"] == [
            "title"
        ]
    assert _pdf_hashes(roots) == hashes


def test_selection_change_during_inline_edit_does_not_write_either_attachment(
    combined_editing, monkeypatch
):
    window, roots = combined_editing
    window.current_root = roots[1]
    _select(window, roots[0])
    before = _catalog_state(window, roots)
    hashes = _pdf_hashes(roots)

    def switch_selection(*args, **kwargs):
        _select(window, roots[1])
        return "Stale edit", True

    monkeypatch.setattr(QInputDialog, "getMultiLineText", switch_selection)
    window.detail._row_widgets["title"]._request_edit()

    assert _catalog_state(window, roots) == before
    assert window._selected_record()["root"] == str(roots[1])
    assert _pdf_hashes(roots) == hashes


@pytest.mark.parametrize("selected_index", [0, 1])
def test_stale_selected_metadata_does_not_overwrite_another_connection(
    combined_editing, selected_index
):
    window, roots = combined_editing
    root = roots[selected_index]
    window.current_root = roots[1 - selected_index]
    selected = _select(window, root)
    hashes = _pdf_hashes(roots)
    newer = deepcopy(selected["metadata"])
    newer["title"] = ["Newer catalog title"]
    newer["notes"] = "Newer catalog notes"
    newer["_adsfolio_locked_fields"] = ["title"]

    other_connection = PdfRegistry(root)
    try:
        assert (
            other_connection._connection is not window.registries[str(root)]._connection
        )
        assert other_connection.replace_file_metadata(root, "same.pdf", newer)
    finally:
        other_connection.close()
    after_external_change = _catalog_state(window, roots)
    assert after_external_change[(root, "same.pdf")]["metadata"] == newer
    assert window._selected_record()["metadata"] == selected["metadata"]
    stale_edit = deepcopy(selected["metadata"])
    stale_edit["title"] = ["Stale attempted title"]

    window._save_detail_metadata(stale_edit)

    assert _catalog_state(window, roots) == after_external_change
    assert window._selected_record()["location_id"] == selected["location_id"]
    assert _pdf_hashes(roots) == hashes


@pytest.mark.parametrize("selected_index", [0, 1])
def test_update_by_id_routes_to_selected_root_and_attachment(
    combined_editing, monkeypatch, selected_index
):
    window, roots = combined_editing
    root = roots[selected_index]
    window.current_root = roots[1 - selected_index]
    selected = _select(window, root)
    before = _catalog_state(window, roots)
    hashes = _pdf_hashes(roots)
    calls = _block_dialogs_and_launches(window, monkeypatch)

    assert window.detail.update_button.isEnabled()
    window.detail.update_button.click()

    assert calls[:2] == ["identifier input", ("identity", root / "same.pdf")]
    assert len(calls) == 3
    kind, args, kwargs = calls[2]
    assert kind == "ADS refresh"
    assert kwargs["root"] == root
    assert kwargs["selection_id"] == selected["location_id"]
    assert args[args.index("--relative-path") + 1] == "same.pdf"
    assert args[args.index("--identifier") + 1] == "10.1234/test"
    assert _catalog_state(window, roots) == before
    assert _pdf_hashes(roots) == hashes


@pytest.mark.parametrize("selected_index", [0, 1])
def test_enter_details_routes_to_selected_root_and_attachment(
    combined_editing, monkeypatch, selected_index
):
    window, roots = combined_editing
    root = roots[selected_index]
    window.current_root = roots[1 - selected_index]
    _select(window, root)
    before = _catalog_state(window, roots)
    hashes = _pdf_hashes(roots)
    calls = _block_dialogs_and_launches(window, monkeypatch)

    assert window.detail.manual_metadata_button.isEnabled()
    window.detail.manual_metadata_button.click()

    assert calls == [("foreground", ("manual_metadata", root), {})]
    assert window._manual_metadata_target == (root, root / "same.pdf", None)
    assert _catalog_state(window, roots) == before
    assert _pdf_hashes(roots) == hashes


@pytest.mark.parametrize("selected_index", [0, 1])
def test_enter_details_saves_and_locks_only_selected_attachment(
    combined_editing, monkeypatch, selected_index
):
    window, roots = combined_editing
    root = roots[selected_index]
    window.current_root = roots[1 - selected_index]
    selected = _select(window, root)
    before = _catalog_state(window, roots)
    hashes = _pdf_hashes(roots)
    calls = _block_dialogs_and_launches(window, monkeypatch)
    dialog_results = []

    def save_details(dialog):
        dialog.fields["title"].setText("Manual title")
        dialog._save()
        dialog_results.append((dialog.result(), dialog.error.text()))
        return dialog.result()

    monkeypatch.setattr(
        "adsfolio.gui.main_window.ManualMetadataDialog.exec", save_details
    )
    assert window.detail.manual_metadata_button.isEnabled()
    window.detail.manual_metadata_button.click()
    assert calls == [("foreground", ("manual_metadata", root), {})]
    window._open_manual_metadata(root)

    assert dialog_results == [(1, "")]
    after = _catalog_state(window, roots)
    changed = after.pop((root, "same.pdf"))
    previous = before.pop((root, "same.pdf"))
    assert after == before
    assert changed["location_id"] == previous["location_id"]
    assert changed["attachment_id"] == previous["attachment_id"]
    assert changed["metadata"]["title"] == ["Manual title"]
    assert changed["metadata"]["_adsfolio_locked_fields"] == ["title"]
    assert changed["metadata"]["notes"] == previous["metadata"]["notes"]
    assert window._selected_record()["location_id"] == selected["location_id"]
    assert _pdf_hashes(roots) == hashes


@pytest.mark.parametrize("same_field", [False, True])
def test_enter_details_preserves_a_concurrent_metadata_edit(
    combined_editing, monkeypatch, same_field
):
    window, roots = combined_editing
    root = roots[1]
    _select(window, root)
    hashes = _pdf_hashes(roots)
    outcomes = []

    def edit_during_dialog(dialog):
        external = PdfRegistry(root)
        try:
            record = external.find_file_by_current_path(root, "same.pdf")
            metadata = deepcopy(record["metadata"])
            metadata["title" if same_field else "notes"] = (
                ["Concurrent title"] if same_field else "Concurrent notes"
            )
            assert external.replace_file_metadata(root, "same.pdf", metadata)
        finally:
            external.close()
        dialog.fields["title"].setText("Manual title")
        dialog._save()
        outcomes.append((dialog.result(), dialog.error.text()))
        return dialog.result()

    monkeypatch.setattr(
        "adsfolio.gui.main_window.ManualMetadataDialog.exec", edit_during_dialog
    )
    window.detail.manual_metadata_button.click()

    saved = window.registries[str(root)].find_file_by_current_path(root, "same.pdf")
    if same_field:
        assert saved["metadata"]["title"] == ["Concurrent title"]
        assert "field changed" in outcomes[0][1].lower()
    else:
        assert saved["metadata"]["title"] == ["Manual title"]
        assert saved["metadata"]["notes"] == "Concurrent notes"
        assert outcomes == [(1, "")]
    assert _pdf_hashes(roots) == hashes


@pytest.mark.parametrize("selection", ["none", "different_roots", "same_root"])
def test_missing_or_multiple_selections_block_all_edit_paths(
    combined_editing, monkeypatch, selection
):
    window, roots = combined_editing
    window.current_root = roots[1]
    _select(window, roots[0])
    selection_model = window.table.selectionModel()
    if selection == "none":
        selection_model.clearSelection()
    else:
        root, name = (
            (roots[1], "same.pdf")
            if selection == "different_roots"
            else (roots[0], "other.pdf")
        )
        index = window._view_table_index(
            window.model.index(_row(window, root, name), 0)
        )
        assert index.isValid()
        selection_model.select(
            index, QItemSelectionModel.Select | QItemSelectionModel.Rows
        )
    before = _catalog_state(window, roots)
    hashes = _pdf_hashes(roots)
    calls = _block_dialogs_and_launches(window, monkeypatch)
    buttons_enabled = (
        window.detail.update_button.isEnabled(),
        window.detail.manual_metadata_button.isEnabled(),
    )

    window._save_detail_metadata({"title": ["Must not be saved"]})
    window._update_from_identifier("")
    window._enter_manual_metadata()

    assert buttons_enabled == (False, False)
    assert calls == []
    assert _catalog_state(window, roots) == before
    assert _pdf_hashes(roots) == hashes


@pytest.mark.parametrize("bad_owner", ["unknown_root", "escaping_path"])
def test_ambiguous_record_ownership_blocks_all_edit_paths(
    combined_editing, monkeypatch, bad_owner
):
    window, roots = combined_editing
    window.current_root = roots[1]
    _select(window, roots[0])
    record = window.model.record_at(_row(window, roots[0]))
    if bad_owner == "unknown_root":
        record["root"] = str(roots[0].parent / "unknown")
    else:
        record["current_path"] = "../second/same.pdf"
    window._sync_detail_and_selection_actions()
    before = _catalog_state(window, roots)
    hashes = _pdf_hashes(roots)
    calls = _block_dialogs_and_launches(window, monkeypatch)
    buttons_enabled = (
        window.detail.update_button.isEnabled(),
        window.detail.manual_metadata_button.isEnabled(),
    )

    window._save_detail_metadata({"title": ["Must not be saved"]})
    window._update_from_identifier("")
    window._enter_manual_metadata()

    assert buttons_enabled == (False, False)
    assert calls == []
    assert _catalog_state(window, roots) == before
    assert _pdf_hashes(roots) == hashes


@pytest.mark.parametrize("active_index", [0, 1])
def test_active_ads_recheck_blocks_edits_and_explains_why(
    combined_editing, monkeypatch, active_index
):
    window, roots = combined_editing
    window.current_root = roots[1]
    _select(window, roots[0])
    before = _catalog_state(window, roots)
    hashes = _pdf_hashes(roots)
    calls = _block_dialogs_and_launches(window, monkeypatch)
    window._ads_recheck_active_root = roots[active_index]
    window._sync_refresh_actions()
    buttons = (window.detail.update_button, window.detail.manual_metadata_button)
    enabled = tuple(button.isEnabled() for button in buttons)
    tips = tuple(button.toolTip().lower() for button in buttons)
    row = window.detail._row_widgets["title"]
    editable = (row._editable, row._lockable)

    window._save_detail_metadata({"title": ["Must not be saved"]})
    window._update_from_identifier("")
    window._enter_manual_metadata()

    assert enabled == (False, False)
    assert all("ads" in tip and "recheck" in tip and "wait" in tip for tip in tips)
    assert editable == (False, False)
    assert calls == []
    assert _catalog_state(window, roots) == before
    assert _pdf_hashes(roots) == hashes

    window._ads_recheck_active_root = None
    window._sync_refresh_actions()
    assert all(button.isEnabled() for button in buttons)


@pytest.mark.parametrize("active_index", [0, 1])
def test_identifier_update_preempts_recheck_for_original_selection(
    combined_editing, monkeypatch, qtbot, active_index
):
    window, roots = combined_editing
    selected = _select(window, roots[0])
    hashes = _pdf_hashes(roots)
    before = _catalog_state(window, roots)
    calls = _block_dialogs_and_launches(window, monkeypatch)

    class Recheck:
        running = True
        cancelled = False

        def cancel(self):
            self.cancelled = True

        def deleteLater(self):
            pass

    recheck = Recheck()
    window._ads_recheck_active_root = roots[active_index]
    window._ads_recheck_job = window.job = recheck
    window._sync_refresh_actions()
    assert window.detail.update_button.isEnabled()
    assert window.update_selected_ads_action.isEnabled()
    assert window.detail.manual_metadata_button.isEnabled()
    assert not window.detail._row_widgets["title"]._editable

    window.detail.update_button.click()
    assert recheck.cancelled
    assert calls == ["identifier input"]
    assert not window.detail.update_button.isEnabled()
    _select(window, roots[1])
    window._ads_recheck_finished(roots[active_index], recheck, None, None)
    qtbot.waitUntil(lambda: window._pending_priority_edit is None)

    refresh = next(
        call for call in calls if isinstance(call, tuple) and call[0] == "ADS refresh"
    )
    assert refresh[2]["root"] == roots[0]
    assert refresh[2]["selection_id"] == selected["location_id"]
    assert calls.count("identifier input") == 1
    assert _catalog_state(window, roots) == before
    assert _pdf_hashes(roots) == hashes


def test_cancel_identifier_prompt_keeps_recheck_running(combined_editing, monkeypatch):
    window, roots = combined_editing
    _select(window, roots[0])
    recheck = SimpleNamespace(running=True, cancel=lambda: pytest.fail("cancelled"))
    window._ads_recheck_active_root = roots[1]
    window._ads_recheck_job = window.job = recheck
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("", False))
    window._sync_refresh_actions()

    window.detail.update_button.click()

    assert window._pending_priority_edit is None
    assert window.job is recheck
    window._ads_recheck_job = window.job = None


def test_recheck_can_finish_while_identifier_prompt_is_open(
    combined_editing, monkeypatch, qtbot
):
    window, roots = combined_editing
    selected = _select(window, roots[0])
    calls = _block_dialogs_and_launches(window, monkeypatch)

    class Recheck:
        running = True

        def cancel(self):
            pytest.fail("The recheck already finished")

        def deleteLater(self):
            pass

    recheck = Recheck()
    window._ads_recheck_active_root = roots[1]
    window._ads_recheck_job = window.job = recheck
    window._sync_refresh_actions()

    def finish_during_input(*args, **kwargs):
        window._ads_recheck_finished(roots[1], recheck, None, None)
        return "10.1234/test", True

    monkeypatch.setattr(QInputDialog, "getText", finish_during_input)
    window.detail.update_button.click()
    qtbot.waitUntil(lambda: window._pending_priority_edit is None)
    refresh = next(
        call for call in calls if isinstance(call, tuple) and call[0] == "ADS refresh"
    )
    assert refresh[2]["root"] == roots[0]
    assert refresh[2]["selection_id"] == selected["location_id"]


def test_manual_details_wait_for_recheck_and_keep_original_selection(
    combined_editing, monkeypatch, qtbot
):
    window, roots = combined_editing
    _select(window, roots[0])
    hashes = _pdf_hashes(roots)
    before = _catalog_state(window, roots)
    calls = _block_dialogs_and_launches(window, monkeypatch)

    class Recheck:
        running = True
        cancelled = False

        def cancel(self):
            self.cancelled = True

        def deleteLater(self):
            pass

    recheck = Recheck()
    window._ads_recheck_active_root = roots[1]
    window._ads_recheck_job = window.job = recheck
    window._sync_refresh_actions()
    assert window.detail.manual_metadata_button.isEnabled()
    window.detail.manual_metadata_button.click()
    assert recheck.cancelled
    assert calls == []
    _select(window, roots[1])
    window._ads_recheck_finished(roots[1], recheck, None, None)
    qtbot.waitUntil(lambda: window._pending_priority_edit is None)

    assert calls == [("foreground", ("manual_metadata", roots[0]), {})]
    assert window._manual_metadata_target[:2] == (roots[0], roots[0] / "same.pdf")
    assert _catalog_state(window, roots) == before
    assert _pdf_hashes(roots) == hashes


def test_priority_edit_does_not_get_stuck_if_another_job_starts(
    combined_editing, monkeypatch, qtbot
):
    window, roots = combined_editing
    _select(window, roots[0])
    calls = _block_dialogs_and_launches(window, monkeypatch)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))

    class Recheck:
        running = True

        def cancel(self):
            pass

        def deleteLater(self):
            pass

    recheck = Recheck()
    window._ads_recheck_active_root = roots[1]
    window._ads_recheck_job = window.job = recheck
    window._sync_refresh_actions()
    window.detail.update_button.click()
    window._ads_recheck_finished(roots[1], recheck, None, None)
    window.job = SimpleNamespace(running=False)
    qtbot.waitUntil(lambda: window._pending_priority_edit is None)

    assert warnings and "Another operation started" in warnings[0][2]
    assert not any(
        isinstance(call, tuple) and call[0] == "ADS refresh" for call in calls
    )
    window.job = None


@pytest.mark.parametrize("change", ["content", "catalog"])
def test_identifier_update_rejects_changed_pdf_after_recheck(
    combined_editing, monkeypatch, qtbot, change
):
    window, roots = combined_editing
    _select(window, roots[0])
    calls = _block_dialogs_and_launches(window, monkeypatch)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))

    class Recheck:
        running = True

        def cancel(self):
            pass

        def deleteLater(self):
            pass

    recheck = Recheck()
    window._ads_recheck_active_root = roots[1]
    window._ads_recheck_job = window.job = recheck
    window._sync_refresh_actions()
    window.detail.update_button.click()
    if change == "content":
        (roots[0] / "same.pdf").write_bytes(b"changed")
    else:
        registry = window.registries[str(roots[0])]
        record = registry.find_file_by_current_path(roots[0], "same.pdf")
        registry.upsert_file(
            root=roots[0],
            relative_path="same.pdf",
            current_path="same.pdf",
            sha256="0" * 64,
            size_bytes=record["size_bytes"],
            mtime_ns=record["mtime_ns"],
            status="unresolved",
        )
    window._ads_recheck_finished(roots[1], recheck, None, None)
    qtbot.waitUntil(lambda: window._pending_priority_edit is None)

    assert warnings and "changed" in warnings[0][2]
    assert not any(
        isinstance(call, tuple) and call[0] == "ADS refresh" for call in calls
    )


def test_identifier_update_waits_for_watch_pause(combined_editing, monkeypatch, qtbot):
    window, roots = combined_editing
    _select(window, roots[0])
    calls = _block_dialogs_and_launches(window, monkeypatch)
    resumed = []
    monkeypatch.setattr(window.watch_controller, "session_id", lambda root: "watch")
    monkeypatch.setattr(window.watch_controller, "resume_writes", resumed.append)
    monkeypatch.setattr(
        window, "_launch_ads_recheck", lambda root: pytest.fail("recheck launched")
    )
    window._ads_recheck_active_root = roots[1]
    window._pending_watch_dialog = ("ads_recheck", roots[1])
    window._sync_refresh_actions()

    window.detail.update_button.click()
    assert calls == ["identifier input"]
    assert window._pending_priority_edit is not None
    window._handle_watch_paused("watch")
    qtbot.waitUntil(lambda: window._pending_priority_edit is None)

    assert resumed == [roots[1]]
    assert any(isinstance(call, tuple) and call[0] == "ADS refresh" for call in calls)


@pytest.mark.parametrize("label", ["Update by ID…", "Enter Details…"])
def test_context_menu_can_preempt_recheck(combined_editing, monkeypatch, label):
    window, roots = combined_editing
    selected = _select(window, roots[0])
    _block_dialogs_and_launches(window, monkeypatch)
    cancellations = []
    recheck = SimpleNamespace(running=True, cancel=lambda: cancellations.append(True))
    window._ads_recheck_active_root = roots[1]
    window._ads_recheck_job = window.job = recheck
    window._sync_refresh_actions()

    class EditMenu(QMenu):
        def exec(self, _position):
            action = next(item for item in self.actions() if item.text() == label)
            assert action.isEnabled()
            action.trigger()

    monkeypatch.setattr("adsfolio.gui.combined_view.QMenu", EditMenu)
    window.combined.table_menu((selected,), QPoint())

    assert cancellations == [True]
    assert window._pending_priority_edit.root == roots[0]
    window._pending_priority_edit = None
    window._ads_recheck_job = window.job = None


@pytest.mark.parametrize("selected_index", [0, 1])
@pytest.mark.parametrize("refresh_scope", ["owner", "other", "combined"])
def test_refresh_preserves_selected_root_and_attachment(
    combined_editing, selected_index, refresh_scope
):
    window, roots = combined_editing
    root = roots[selected_index]
    other = roots[1 - selected_index]
    window.current_root = other
    selected = _select(window, root)
    hashes = _pdf_hashes(roots)

    if refresh_scope == "combined":
        window.combined.refresh()
    else:
        window.refresh(root if refresh_scope == "owner" else other)

    records = window._selected_records()
    assert len(records) == 1
    assert records[0]["root"] == str(root)
    assert records[0]["location_id"] == selected["location_id"]
    assert window.detail._root == root
    assert window.combined.enabled
    assert _pdf_hashes(roots) == hashes


@pytest.mark.parametrize("rename", [False, True])
def test_identify_combined_selection_uses_one_owning_root(
    combined_editing, monkeypatch, rename
):
    window, roots = combined_editing
    window.current_root = roots[0]
    _select(window, roots[1])
    other = window._view_table_index(
        window.model.index(_row(window, roots[1], "other.pdf"), 0)
    )
    window.table.selectionModel().select(
        other, QItemSelectionModel.Select | QItemSelectionModel.Rows
    )
    records = window._selected_records()
    calls = []
    monkeypatch.setattr(
        window,
        "_identify_and_rename" if rename else "_identify",
        lambda paths, *, root: calls.append((set(paths), root)),
    )
    before = _pdf_hashes(roots)

    window._identify_combined_selection(records, rename=rename)

    assert calls == [({roots[1] / "same.pdf", roots[1] / "other.pdf"}, roots[1])]
    assert _pdf_hashes(roots) == before


def test_identify_combined_selection_refuses_mixed_or_changed_roots(
    combined_editing, monkeypatch
):
    window, roots = combined_editing
    _select(window, roots[0])
    first = window._selected_records()
    other = window._view_table_index(window.model.index(_row(window, roots[1]), 0))
    window.table.selectionModel().select(
        other, QItemSelectionModel.Select | QItemSelectionModel.Rows
    )
    mixed = window._selected_records()
    calls = []
    monkeypatch.setattr(window, "_identify", lambda *args, **kwargs: calls.append(args))

    window._identify_combined_selection(first)
    window._identify_combined_selection(mixed)

    assert calls == []
    assert "one available library" in window.status.currentMessage()


def test_combined_tree_folder_rows_never_target_a_pdf(combined_editing, monkeypatch):
    window, roots = combined_editing
    _select(window, roots[0])
    tree = window.table.model()
    root_index = tree.index_for_key(("root", str(roots[1])))
    assert root_index.isValid()
    assert tree.is_folder_index(root_index)
    before = _catalog_state(window, roots)
    hashes = _pdf_hashes(roots)
    calls = _block_dialogs_and_launches(window, monkeypatch)

    window.table.selectionModel().setCurrentIndex(
        root_index, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows
    )
    window._save_detail_metadata({"title": ["Must not be saved"]})
    window._update_from_identifier("")
    window._enter_manual_metadata()

    assert window._selected_record() is None
    assert not window.detail.update_button.isEnabled()
    assert not window.detail.manual_metadata_button.isEnabled()
    assert calls == []
    assert _catalog_state(window, roots) == before
    assert _pdf_hashes(roots) == hashes


def test_context_action_rejects_a_changed_selection(combined_editing):
    window, roots = combined_editing
    first = _select(window, roots[0])
    identity = (str(roots[0]), first["location_id"])
    _select(window, roots[1])
    calls = []

    window.combined._run_single_selection(identity, lambda: calls.append(True))

    assert calls == []
    assert "selection changed" in window.status.currentMessage().lower()


def test_inline_edit_preserves_a_concurrent_shared_document_change(
    combined_editing, monkeypatch
):
    window, roots = combined_editing
    root = roots[0]
    registry = window.registries[str(root)]
    current = registry.find_file_by_current_path(root, "same.pdf")
    metadata = deepcopy(current["metadata"])
    metadata["bibcode"] = "2024ApJ...123..456F"
    assert registry.replace_file_metadata(root, "same.pdf", metadata)
    window.refresh(root)
    selected = _select(window, root)
    document_id = selected["document_id"]
    assert document_id is not None
    hashes = _pdf_hashes(roots)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    external = PdfRegistry(root)
    try:
        external.catalog.set_document_cite_key(document_id, "changed-elsewhere")
    finally:
        external.close()
    changed = deepcopy(selected["metadata"])
    changed["notes"] = "Attempted stale edit"

    window._save_detail_metadata(changed)

    current = window.registries[str(root)].find_file_by_current_path(root, "same.pdf")
    document = window.registries[str(root)].catalog.get_document(document_id)
    assert current["metadata"] == selected["metadata"]
    assert document["metadata"][CITE_KEY_METADATA_KEY] == "changed-elsewhere"
    assert any("changed" in warning.lower() for warning in warnings)
    assert _pdf_hashes(roots) == hashes


def test_inline_edit_does_not_restore_stale_fields_in_a_shared_document(
    combined_editing, monkeypatch
):
    window, roots = combined_editing
    bibcode = "2024ApJ...123..456F"
    for index, root in enumerate(roots):
        registry = window.registries[str(root)]
        record = registry.find_file_by_current_path(root, "same.pdf")
        metadata = deepcopy(record["metadata"])
        metadata.update({"bibcode": bibcode, "title": [f"Title {index}"]})
        assert registry.replace_file_metadata(root, "same.pdf", metadata)
    window.refresh(roots[0])
    window.refresh(roots[1])
    selected = _select(window, roots[0])
    assert selected["document_id"] is not None
    document = window.registries[str(roots[0])].catalog.get_document(
        selected["document_id"]
    )
    assert document["metadata"]["title"] == ["Title 1"]
    hashes = _pdf_hashes(roots)
    monkeypatch.setattr(
        QInputDialog, "getMultiLineText", lambda *args, **kwargs: ("Title 0", True)
    )
    window.detail._edit_metadata("title")
    document = window.registries[str(roots[0])].catalog.get_document(
        selected["document_id"]
    )
    assert document["metadata"]["title"] == ["Title 1"]
    monkeypatch.setattr(
        QInputDialog, "getMultiLineText", lambda *args, **kwargs: ("Edited notes", True)
    )

    window.detail._edit_metadata("notes")

    registry = window.registries[str(roots[0])]
    updated = registry.find_file_by_current_path(roots[0], "same.pdf")
    document = registry.catalog.get_document(selected["document_id"])
    assert updated["metadata"]["notes"] == "Edited notes"
    assert updated["metadata"]["title"] == ["Title 0"]
    assert document["metadata"]["title"] == ["Title 1"]
    assert _pdf_hashes(roots) == hashes


def test_inline_edit_preserves_unresolved_status_with_a_stored_bibcode(
    combined_editing, monkeypatch
):
    window, roots = combined_editing
    root = roots[1]
    registry = window.registries[str(root)]
    record = registry.find_file_by_current_path(root, "same.pdf")
    metadata = deepcopy(record["metadata"])
    metadata["bibcode"] = "2024ApJ...123..456F"
    assert registry.replace_file_metadata(root, "same.pdf", metadata)
    assert registry.update_file_status(root, "same.pdf", "unresolved")
    window.refresh(root)
    selected = _select(window, root)
    assert selected["status"] == "unresolved"
    monkeypatch.setattr(
        QInputDialog, "getMultiLineText", lambda *args, **kwargs: ("Edited notes", True)
    )
    hashes = _pdf_hashes(roots)

    window.detail._edit_metadata("notes")

    saved = window.registries[str(root)].find_file_by_current_path(root, "same.pdf")
    assert saved["metadata"]["notes"] == "Edited notes"
    assert saved["status"] == "unresolved"
    assert _pdf_hashes(roots) == hashes


def test_combined_context_menu_updates_the_selected_pdf(combined_editing, monkeypatch):
    window, roots = combined_editing
    selected = _select(window, roots[1])
    calls = _block_dialogs_and_launches(window, monkeypatch)

    class UpdateMenu(QMenu):
        def exec(self, _position):
            action = next(
                item for item in self.actions() if item.text() == "Update by ID…"
            )
            assert action.isEnabled()
            action.trigger()

    monkeypatch.setattr("adsfolio.gui.combined_view.QMenu", UpdateMenu)
    window.combined.table_menu((selected,), QPoint())

    assert calls[-1][0] == "ADS refresh"
    assert calls[-1][2]["root"] == roots[1]
    assert calls[-1][2]["selection_id"] == selected["location_id"]


def test_combined_context_menu_explains_mixed_root_identify(
    combined_editing, monkeypatch
):
    window, roots = combined_editing
    _select(window, roots[0])
    other = window._view_table_index(window.model.index(_row(window, roots[1]), 0))
    window.table.selectionModel().select(
        other, QItemSelectionModel.Select | QItemSelectionModel.Rows
    )

    class MixedMenu(QMenu):
        def exec(self, _position):
            action = next(
                item
                for item in self.actions()
                if item.text() == "Identify Selected PDFs…"
            )
            assert not action.isEnabled()
            assert "one available library" in action.toolTip()

    monkeypatch.setattr("adsfolio.gui.combined_view.QMenu", MixedMenu)
    window.combined.table_menu(window._selected_records(), QPoint())


@pytest.mark.parametrize(
    ("label", "kind"),
    [("Identify Library…", "identify"), ("Rename Library…", "rename")],
)
def test_library_folder_actions_use_the_folder_root(
    combined_editing, monkeypatch, label, kind
):
    window, roots = combined_editing
    window.current_root = roots[0]
    tree = window.table.model()
    folder = tree.index_for_key(("root", str(roots[1])))
    calls = []
    monkeypatch.setattr(
        window, "_request_foreground_dialog", lambda *args, **kwargs: calls.append(args)
    )

    class FolderMenu(QMenu):
        def exec(self, _position):
            action = next(item for item in self.actions() if item.text() == label)
            assert action.isEnabled()
            action.trigger()

    monkeypatch.setattr("adsfolio.gui.combined_view.QMenu", FolderMenu)
    window.combined.folder_menu(folder, QPoint())

    assert calls == [(kind, roots[1])]
    assert window.combined.enabled


def test_library_folder_action_rejects_an_unchecked_root(combined_editing):
    window, roots = combined_editing
    calls = []
    window.combined.selected.discard(str(roots[1]))

    window.combined._run_library_action(roots[1], lambda root: calls.append(root))

    assert calls == []
    assert "unavailable" in window.status.currentMessage().lower()


def test_watcher_cannot_start_during_a_library_job(combined_editing, monkeypatch):
    window, roots = combined_editing
    calls = []
    monkeypatch.setattr(
        window.watch_controller,
        "start_root",
        lambda root, **_kwargs: calls.append(root),
    )
    monkeypatch.setattr(
        window.watch_controller, "is_watching", lambda _root: bool(calls)
    )
    window.job = SimpleNamespace(running=False)

    assert not window._start_root_watch(roots[1])
    assert calls == []
    assert "wait" in window.status.currentMessage().lower()

    window.job = None
    assert window._start_root_watch(roots[1])
    assert calls == [roots[1]]


def test_table_context_menu_routes_library_folder(combined_editing, monkeypatch):
    window, roots = combined_editing
    folder = window.table.model().index_for_key(("root", str(roots[1])))
    position = QPoint(4, 6)
    calls = []
    monkeypatch.setattr(window.table, "indexAt", lambda _position: folder)
    monkeypatch.setattr(
        window.combined,
        "folder_menu",
        lambda index, point: calls.append((index, point)),
    )

    window._table_menu(position)

    assert calls == [(folder, position)]


@pytest.mark.parametrize(
    ("dialog_name", "open_method"),
    [
        ("IdentifyDialog", "_open_identify_dialog"),
        ("RenameDialog", "_open_rename_dialog"),
    ],
)
def test_library_dialog_job_refreshes_its_own_root(
    combined_editing, monkeypatch, dialog_name, open_method
):
    window, roots = combined_editing
    window.current_root = roots[0]
    refreshes = []

    class CallbackSignal:
        def connect(self, callback):
            self.callback = callback

    class FakeDialog:
        def __init__(self, root, *_args, **_kwargs):
            assert root == roots[1]
            self.job_started = CallbackSignal()

        def exec(self):
            self.job_started.callback(object())

    monkeypatch.setattr(f"adsfolio.gui.main_window.{dialog_name}", FakeDialog)
    monkeypatch.setattr(window, "_watch_job", lambda job: setattr(window, "job", job))
    monkeypatch.setattr(window, "refresh", lambda root=None: refreshes.append(root))

    getattr(window, open_method)(roots[1])
    window._job_finished(0)

    assert refreshes == [roots[1]]
    assert window.combined.enabled


def test_combined_update_by_id_runs_owning_library_workflow(
    combined_editing, monkeypatch, qtbot
):
    window, roots = combined_editing
    window.current_root = roots[0]
    selected = _select(window, roots[1])
    before_hash = hashlib.sha256((roots[1] / "same.pdf").read_bytes()).hexdigest()
    other_before = _catalog_state(window, roots)[(roots[0], "same.pdf")]
    other_hash = hashlib.sha256((roots[0] / "same.pdf").read_bytes()).hexdigest()
    Config.save_rename_settings(RenameSettings(template="{authors}_{year}-{title}"))
    monkeypatch.setattr(
        QInputDialog, "getText", lambda *args, **kwargs: ("10.5555/fictional", True)
    )
    monkeypatch.setattr(
        QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes
    )
    monkeypatch.setattr(
        "adsfolio.pdf.cli._get_client",
        lambda _ctx: SimpleNamespace(
            search=SimpleNamespace(
                query=lambda **_kwargs: SimpleNamespace(num_found=0, docs=[])
            )
        ),
    )
    monkeypatch.setattr(
        "adsfolio.pdf.identifier.fetch_crossref_doi",
        lambda _doi: {
            "doi": ["10.5555/fictional"],
            "title": ["Fictional 1 same.pdf"],
            "author": ["Roe, Alice"],
            "year": "2021",
            "pub": "Journal of Examples",
            "volume": "3",
            "page": ["7"],
        },
    )
    results = []

    class InlineCommandJob(QObject):
        output = Signal(str)
        failed = Signal(str)
        finished = Signal(int)

        def __init__(self, argv, parent=None, **_kwargs):
            super().__init__(parent)
            self.argv = argv
            self.running = True

        def start(self):
            def run():
                result = CliRunner().invoke(pdf, self.argv[4:])
                results.append(result)
                self.running = False
                self.finished.emit(result.exit_code)

            QTimer.singleShot(0, run)

        def cancel(self):
            self.running = False

    monkeypatch.setattr("adsfolio.gui.main_window.CommandJob", InlineCommandJob)
    window.detail.update_button.click()
    qtbot.waitUntil(lambda: bool(results), timeout=5000)

    assert results[0].exit_code == 0, results[0].output
    registry = window.registries[str(roots[1])]
    updated = registry.find_location_by_id(selected["location_id"])
    assert updated is not None
    assert updated["current_path"] != "same.pdf"
    renamed = roots[1] / updated["current_path"]
    assert hashlib.sha256(renamed.read_bytes()).hexdigest() == before_hash
    assert not (roots[1] / "same.pdf").exists()
    other = window.registries[str(roots[0])].find_file_by_current_path(
        roots[0], "same.pdf"
    )
    assert {
        "location_id": other["location_id"],
        "attachment_id": other["attachment_id"],
        "metadata": other["metadata"],
        "sha256": other["sha256"],
        "current_path": other["current_path"],
    } == other_before
    assert (
        hashlib.sha256((roots[0] / "same.pdf").read_bytes()).hexdigest() == other_hash
    )
    assert window.combined.enabled


def test_combined_tree_preserves_expansion_and_selection_on_refresh(combined_editing):
    window, roots = combined_editing
    selected = _select(window, roots[0])
    tree = window.table.model()
    first = tree.index_for_key(("root", str(roots[0])))
    second = tree.index_for_key(("root", str(roots[1])))
    window.table.expand(first)
    window.table.collapse(second)

    window.combined.refresh()

    first = tree.index_for_key(("root", str(roots[0])))
    second = tree.index_for_key(("root", str(roots[1])))
    assert window.table.isExpanded(first)
    assert not window.table.isExpanded(second)
    assert window._selected_record()["location_id"] == selected["location_id"]
    assert window.detail._root == roots[0]


def test_combined_tree_shows_nested_directories_and_library_labels(combined_editing):
    window, roots = combined_editing
    path = roots[1] / "folder" / "subfolder" / "nested.pdf"
    path.parent.mkdir(parents=True)
    writer = PdfWriter()
    writer.add_blank_page(width=24, height=24)
    writer.write(path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    stat = path.stat()
    window.registries[str(roots[1])].upsert_file(
        root=roots[1],
        relative_path="folder/subfolder/nested.pdf",
        current_path="folder/subfolder/nested.pdf",
        sha256=before,
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        status="unresolved",
        metadata={},
    )

    window.refresh(roots[1])

    tree = window.table.model()
    root = tree.index_for_key(("root", str(roots[1])))
    folder = tree.index_for_key(("directory", str(roots[1]), ("folder",)))
    nested = tree.index_for_key(("directory", str(roots[1]), ("folder", "subfolder")))
    assert root.isValid() and folder.isValid() and nested.isValid()
    assert (
        tree.data(root.siblingAtColumn(tree.filename_column), Qt.DisplayRole)
        == "Library 1"
    )
    assert not tree.data(
        root.siblingAtColumn(tree.filename_column), Qt.DecorationRole
    ).isNull()
    assert tree.data(
        folder.siblingAtColumn(tree.filename_column), Qt.ToolTipRole
    ) == str(roots[1] / "folder")
    assert tree.parent(folder) == root
    assert tree.parent(nested) == folder
    record = window.registries[str(roots[1])].find_file_by_current_path(
        roots[1], "folder/subfolder/nested.pdf"
    )
    leaf = tree.index_for_key(("file", str(roots[1]), record["location_id"]))
    assert tree.parent(leaf) == nested
    assert (
        tree.data(leaf.siblingAtColumn(tree.filename_column), Qt.DisplayRole)
        == "nested.pdf"
    )
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_combined_tree_stays_usable_when_filename_column_is_hidden(combined_editing):
    window, roots = combined_editing
    tree = window.table.model()
    root = tree.index_for_key(("root", str(roots[0])))

    window._set_column_visibility("filename", False)

    column = window.table.treePosition()
    assert column != tree.filename_column
    assert not window.table.isColumnHidden(column)
    assert tree.data(root.siblingAtColumn(column), Qt.DisplayRole) == "Library 0"

    window._set_column_visibility("filename", True)
    assert window.table.treePosition() == tree.filename_column


def test_combined_tree_restores_a_later_page_selection_after_sort(
    combined_editing, monkeypatch, qtbot
):
    window, _roots = combined_editing
    monkeypatch.setattr("adsfolio.gui.models.PAGE_SIZE", 2)
    assert window.apply_sort("filename", False)
    tree = window.table.model()
    qtbot.waitUntil(lambda: not tree.is_loading, timeout=5000)
    first = window.model.record_at(0)
    selected = _select(window, first["root"], first["current_path"])

    assert window.apply_sort("filename", True)
    qtbot.waitUntil(lambda: not tree.is_loading, timeout=5000)

    assert window._selected_record()["location_id"] == selected["location_id"]
    assert window._selected_record()["root"] == selected["root"]
