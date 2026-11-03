"""Exercise the unified Settings window with isolated library data."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QApplication, QMessageBox

from adsfolio.config import Config
from adsfolio.gui.config import GuiConfig, RootEntry, load_config, save_config
from adsfolio.gui.main_window import MainWindow
from adsfolio.gui.preferences import PreferencesDialog
from adsfolio.pdf.registry import CentralPdfRegistryState


@pytest.fixture
def preferences_window(qtbot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    config_dir = tmp_path / "config"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: config_dir))
    root = tmp_path / "library"
    root.mkdir()
    pdf = root / "example.pdf"
    pdf.write_bytes(b"%PDF-1.4\nfictional test content\n%%EOF\n")
    save_config(
        GuiConfig(
            roots=[RootEntry(str(root))],
            check_updates_automatically=False,
            citation_styles=[],
        )
    )
    window = MainWindow()
    qtbot.addWidget(window)
    dialog = PreferencesDialog(window)
    qtbot.addWidget(dialog)
    yield window, dialog, root, pdf
    dialog.close()
    window.close()


def _open_page(dialog: PreferencesDialog, label: str) -> None:
    items = dialog.categories.findItems(label, Qt.MatchExactly)
    assert len(items) == 1
    dialog.categories.setCurrentRow(dialog.categories.row(items[0]))


def test_general_changes_require_save_and_persist(preferences_window):
    window, dialog, _root, _pdf = preferences_window
    dialog.sound.set_sound("chime")
    dialog.automatic_updates.setChecked(True)

    assert window.config.watch_sound == "silent"
    assert load_config().watch_sound == "silent"
    dialog._save_general()

    assert window.config.watch_sound == "chime"
    assert load_config().watch_sound == "chime"
    assert load_config().check_updates_automatically
    assert window.automatic_updates_action.isChecked()


def test_appearance_alignment_applies_only_after_save(preferences_window):
    window, dialog, _root, _pdf = preferences_window
    _open_page(dialog, "Appearance")
    combo = dialog.appearance._alignment_controls["title"]
    combo.setCurrentIndex(combo.findData("center"))
    assert "title" not in window.config.column_alignments

    dialog._save_appearance()

    assert window.config.column_alignments["title"] == "center"
    assert load_config().column_alignments["title"] == "center"
    title_index = next(
        i for i, column in enumerate(window.columns) if column.key == "title"
    )
    assert window.model.index(0, title_index).data(Qt.TextAlignmentRole) == int(
        Qt.AlignHCenter | Qt.AlignVCenter
    )


def test_sort_settings_use_the_same_persisted_table_sort(preferences_window):
    window, dialog, _root, _pdf = preferences_window
    _open_page(dialog, "Sorting")
    dialog.sort_primary.setCurrentIndex(dialog.sort_primary.findData("year"))
    dialog.sort_secondary.setCurrentIndex(dialog.sort_secondary.findData("title"))
    dialog.sort_direction.setCurrentIndex(dialog.sort_direction.findData(False))

    dialog._save_sorting()

    assert window.model.query.order_by == "year"
    assert window.model.query.secondary_order_by == "title"
    assert window.model.query.descending is False
    saved = load_config()
    assert saved.sort_column == "year"
    assert saved.secondary_sort_column == "title"
    assert saved.sort_descending is False


def test_sort_settings_reject_duplicate_columns_without_saving(preferences_window):
    window, dialog, _root, _pdf = preferences_window
    _open_page(dialog, "Sorting")
    original = window.current_query
    dialog.sort_secondary.setCurrentIndex(
        dialog.sort_secondary.findData(dialog.sort_primary.currentData())
    )

    dialog._save_sorting()

    assert window.current_query == original
    assert "different column" in dialog.sort_feedback.text()


def test_library_and_global_identify_defaults_use_existing_scopes(preferences_window):
    window, dialog, root, pdf = preferences_window
    before = hashlib.sha256(pdf.read_bytes()).hexdigest()
    _open_page(dialog, "Identify and Rename")
    identify = dialog.identify
    identify.scope.setCurrentIndex(identify.scope.findData("library"))
    identify.allow_ads.setChecked(not identify.allow_ads.isChecked())
    expected = identify.selected_settings()
    identify.save_defaults_button.click()

    state = CentralPdfRegistryState(window.registries[str(root)])
    assert state.identify_settings == expected
    assert window.config.identify_scopes[str(root)] == "library"
    assert hashlib.sha256(pdf.read_bytes()).hexdigest() == before


def test_watch_settings_change_only_selected_library_preferences(preferences_window):
    window, dialog, root, pdf = preferences_window
    before = hashlib.sha256(pdf.read_bytes()).hexdigest()
    _open_page(dialog, "Libraries and Watching")
    dialog.watch_on_start.setChecked(True)

    assert window.root_entries[str(root)].watch_on_start
    assert load_config().roots[0].watch_on_start
    assert not window.watch_controller.is_watching(root)
    assert hashlib.sha256(pdf.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("focus_name", ["search", "table"])
def test_standard_preferences_shortcut_opens_the_settings_window(
    preferences_window, qtbot, focus_name
):
    window, _dialog, _root, _pdf = preferences_window
    assert window.preferences_action.menuRole() == QAction.PreferencesRole
    assert QKeySequence("Ctrl+,") in window.preferences_action.shortcuts()
    window.show()
    window.activateWindow()
    getattr(window, focus_name).setFocus()
    qtbot.waitUntil(lambda: getattr(window, focus_name).hasFocus(), timeout=3000)
    observed = []

    def close_settings():
        active = QApplication.activeModalWidget()
        observed.append(active)
        if active is not None:
            active.reject()

    QTimer.singleShot(0, close_settings)
    qtbot.keyClick(getattr(window, focus_name), Qt.Key_Comma, Qt.ControlModifier)

    assert len(observed) == 1
    assert isinstance(observed[0], PreferencesDialog)


def test_failed_general_save_leaves_existing_preferences_unchanged(
    preferences_window, monkeypatch
):
    window, dialog, _root, _pdf = preferences_window
    original = load_config()
    dialog.sound.set_sound("bell")

    def fail_save(_config):
        raise OSError("fictional disk failure")

    monkeypatch.setattr("adsfolio.gui.preferences.save_config", fail_save)
    dialog._save_general()

    assert window.config.watch_sound == original.watch_sound
    assert load_config() == original
    assert "fictional disk failure" in dialog.general_feedback.text()


def test_library_defaults_wait_for_the_watcher(preferences_window, monkeypatch):
    window, dialog, root, pdf = preferences_window
    before = hashlib.sha256(pdf.read_bytes()).hexdigest()
    _open_page(dialog, "Identify and Rename")
    identify = dialog.identify
    identify.scope.setCurrentIndex(identify.scope.findData("library"))
    identify.allow_ads.setChecked(not identify.allow_ads.isChecked())
    monkeypatch.setattr(window.watch_controller, "is_watching", lambda target: True)
    errors = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: errors.append(args[2]))

    identify.save_defaults_button.click()

    state = CentralPdfRegistryState(window.registries[str(root)])
    assert state.identify_settings is None
    assert any("Stop watching" in message for message in errors)
    assert hashlib.sha256(pdf.read_bytes()).hexdigest() == before


def test_changing_library_retains_unsaved_defaults_until_confirmed(
    preferences_window, tmp_path, monkeypatch
):
    window, dialog, root, _pdf = preferences_window
    second = tmp_path / "second-library"
    second.mkdir()
    window._register_root(second)
    _open_page(dialog, "Identify and Rename")
    identify = dialog.identify
    identify.scope.setCurrentIndex(identify.scope.findData("library"))
    identify.allow_ads.setChecked(not identify.allow_ads.isChecked())
    draft = identify.allow_ads.isChecked()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.No)

    dialog.defaults_target.setCurrentIndex(dialog.defaults_target.findData(str(second)))

    assert dialog.defaults_target.currentData() == str(root)
    assert dialog.identify.allow_ads.isChecked() == draft


@pytest.mark.parametrize("name", ["identify", "rename"])
def test_changing_scope_preserves_unsaved_defaults_until_confirmed(
    preferences_window, monkeypatch, name
):
    _window, dialog, _root, _pdf = preferences_window
    _open_page(dialog, "Identify and Rename")
    editor = getattr(dialog, name)
    if name == "identify":
        editor.allow_ads.setChecked(not editor.allow_ads.isChecked())
    else:
        editor.template.setText(editor.template.text() + "-draft")
    draft = editor.selected_settings()
    original_scope = editor.scope.currentIndex()
    other_scope = editor.scope.findData("library")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.No)

    editor.scope.setCurrentIndex(other_scope)

    assert editor.scope.currentIndex() == original_scope
    assert editor.selected_settings() == draft
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    editor.scope.setCurrentIndex(other_scope)
    assert editor.scope.currentIndex() == other_scope
    assert editor.selected_settings() != draft


def test_failed_library_settings_load_keeps_original_target(
    preferences_window, tmp_path, monkeypatch
):
    window, dialog, root, _pdf = preferences_window
    second = tmp_path / "second-library"
    second.mkdir()
    window._register_root(second)
    _open_page(dialog, "Identify and Rename")
    original_editor = dialog.identify

    def state_or_error(registry):
        if registry.directory == second:
            raise OSError("fictional catalog failure")
        return CentralPdfRegistryState(registry)

    monkeypatch.setattr(
        "adsfolio.gui.preferences.CentralPdfRegistryState", state_or_error
    )
    dialog.defaults_target.setCurrentIndex(dialog.defaults_target.findData(str(second)))

    assert dialog.defaults_target.currentData() == str(root)
    assert dialog.identify is original_editor
    assert "fictional catalog failure" in dialog.defaults_feedback.text()


def test_global_defaults_work_without_any_library(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    save_config(GuiConfig(check_updates_automatically=False, citation_styles=[]))
    window = MainWindow()
    qtbot.addWidget(window)
    dialog = PreferencesDialog(window)
    qtbot.addWidget(dialog)
    _open_page(dialog, "Identify and Rename")
    assert dialog.defaults_target.currentData() is None
    assert not dialog.identify.scope.model().item(1).isEnabled()
    dialog.identify.allow_ads.setChecked(not dialog.identify.allow_ads.isChecked())
    expected = dialog.identify.selected_settings()

    dialog.identify.save_defaults_button.click()

    assert Config.load_identify_settings() == expected
    assert window.current_root is None
    dialog.close()
    window.close()


def test_global_identify_defaults_reject_skipping_the_future_root(
    qtbot, monkeypatch, tmp_path
):
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    save_config(GuiConfig(check_updates_automatically=False, citation_styles=[]))
    window = MainWindow()
    qtbot.addWidget(window)
    dialog = PreferencesDialog(window)
    qtbot.addWidget(dialog)
    _open_page(dialog, "Identify and Rename")
    original = Config.load_identify_settings()
    dialog.identify.output.setText(".")
    dialog.identify.skip_unresolved.setChecked(True)
    errors = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: errors.append(args[2]))

    dialog.identify.save_defaults_button.click()

    assert Config.load_identify_settings() == original
    assert any("contains the watched root" in error for error in errors)
    dialog.close()
    window.close()


def test_combined_view_requires_explicit_library_for_overrides(preferences_window):
    window, _old_dialog, root, _pdf = preferences_window
    window.combined.set_enabled(True)
    dialog = PreferencesDialog(window)
    _open_page(dialog, "Identify and Rename")
    assert dialog.defaults_target.currentData() is None
    assert not dialog.identify.scope.model().item(1).isEnabled()
    dialog.defaults_target.setCurrentIndex(dialog.defaults_target.findData(str(root)))
    assert dialog.identify.scope.model().item(1).isEnabled()
    dialog.close()


def test_opening_all_settings_pages_does_not_save_or_change_pdf(
    preferences_window, qtbot, monkeypatch
):
    window, dialog, _root, pdf = preferences_window
    config_dir = Config.get_config_dir()
    paths = [
        config_dir / "gui.json",
        config_dir / "setup.json",
        config_dir / "token",
        config_dir / "cv.yaml",
        config_dir / "cv-selection.yaml",
    ]
    before = {path: path.read_bytes() if path.exists() else None for path in paths}
    pdf_hash = hashlib.sha256(pdf.read_bytes()).hexdigest()

    def reject_network(*_args, **_kwargs):
        pytest.fail("Opening Settings attempted a network connection")

    monkeypatch.setattr("socket.socket.connect", reject_network)

    for row in range(dialog.categories.count()):
        dialog.categories.setCurrentRow(row)
    qtbot.wait(400)
    dialog.close()

    assert {
        path: path.read_bytes() if path.exists() else None for path in paths
    } == before
    assert hashlib.sha256(pdf.read_bytes()).hexdigest() == pdf_hash
    assert window.job is None


def test_failed_appearance_save_restores_live_settings(preferences_window, monkeypatch):
    window, dialog, _root, _pdf = preferences_window
    _open_page(dialog, "Appearance")
    original = load_config()
    combo = dialog.appearance._alignment_controls["title"]
    combo.setCurrentIndex(combo.findData("right"))

    def fail_save(_config):
        raise OSError("fictional disk failure")

    monkeypatch.setattr("adsfolio.gui.preferences.save_config", fail_save)
    dialog._save_appearance()

    assert window.config.column_alignments == original.column_alignments
    assert load_config() == original
    assert "fictional disk failure" in dialog.appearance_feedback.text()


def test_cv_settings_save_does_not_export_a_document(preferences_window, tmp_path):
    _window, dialog, _root, pdf = preferences_window
    before = hashlib.sha256(pdf.read_bytes()).hexdigest()
    _open_page(dialog, "CV and Export")
    cv = dialog.cv_settings
    cv.query.setText("author:Example")
    output = tmp_path / "publication-list.tex"
    cv.output_path.setText(str(output))

    dialog._save_cv(cv)

    assert cv.settings_path.is_file()
    assert cv.status.text() == "Settings saved."
    assert not output.exists()
    assert hashlib.sha256(pdf.read_bytes()).hexdigest() == before


def test_cv_settings_do_not_overwrite_intervening_edits(preferences_window):
    _window, dialog, _root, _pdf = preferences_window
    _open_page(dialog, "CV and Export")
    cv = dialog.cv_settings
    cv.query.setText("author:Example")
    external = b"author:\n  query: changed outside settings\n"
    cv.settings_path.parent.mkdir(parents=True, exist_ok=True)
    cv.settings_path.write_bytes(external)

    dialog._save_cv(cv)

    assert cv.settings_path.read_bytes() == external
    assert "changed on disk" in cv.status.text()


def test_escape_from_cv_settings_does_not_save(preferences_window, qtbot):
    _window, dialog, _root, _pdf = preferences_window
    settings_path = Config.get_config_dir() / "cv.yaml"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_bytes(b"author:\n  query: initial\n")
    _open_page(dialog, "CV and Export")
    cv = dialog.cv_settings
    cv.query.setText("unsaved draft")
    external = b"author:\n  query: external\n"
    settings_path.write_bytes(external)
    dialog.show()
    cv.query.setFocus()
    qtbot.waitUntil(cv.query.hasFocus, timeout=3000)

    qtbot.keyClick(cv.query, Qt.Key_Escape)

    assert not dialog.isVisible()
    assert settings_path.read_bytes() == external


def test_failed_cv_settings_read_cannot_overwrite_existing_file(
    preferences_window, monkeypatch
):
    _window, dialog, _root, _pdf = preferences_window
    path = Config.get_config_dir() / "cv.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    original = b"author:\n  query: existing\n"
    path.write_bytes(original)
    read_text = Path.read_text
    failed = False

    def fail_once(target, *args, **kwargs):
        nonlocal failed
        if target == path and not failed:
            failed = True
            raise OSError("fictional read failure")
        return read_text(target, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_once)
    _open_page(dialog, "CV and Export")
    cv = dialog.cv_settings
    cv.query.setText("unsaved draft")

    dialog._save_cv(cv)

    assert failed
    assert path.read_bytes() == original
    assert "Reopen Settings" in cv.status.text()


@pytest.mark.parametrize("original", [b"author: [unterminated", b"- invalid root\n"])
def test_invalid_cv_settings_cannot_be_overwritten(preferences_window, original):
    _window, dialog, _root, _pdf = preferences_window
    path = Config.get_config_dir() / "cv.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(original)

    _open_page(dialog, "CV and Export")
    cv = dialog.cv_settings
    dialog._save_cv(cv)

    assert path.read_bytes() == original
    assert "Reopen Settings" in cv.status.text()


def test_cv_file_changed_during_load_cannot_be_overwritten(
    preferences_window, monkeypatch
):
    _window, dialog, _root, _pdf = preferences_window
    path = Config.get_config_dir() / "cv.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"author:\n  query: initial\n")
    external = b"author:\n  query: external\n"
    read_text = Path.read_text
    changed = False

    def change_once(target, *args, **kwargs):
        nonlocal changed
        if target == path and not changed:
            changed = True
            target.write_bytes(external)
        return read_text(target, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", change_once)
    _open_page(dialog, "CV and Export")
    cv = dialog.cv_settings
    cv.query.setText("unsaved draft")

    dialog._save_cv(cv)

    assert changed
    assert path.read_bytes() == external
    assert "Reopen Settings" in cv.status.text()
