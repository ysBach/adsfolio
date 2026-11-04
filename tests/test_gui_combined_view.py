"""The combined library view keeps per-library check states and routes file actions.

The tests cover saved root check states, file actions sent to the owning
library, and selection when a library fails to restore.
"""

from __future__ import annotations

import hashlib

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QItemSelectionModel, QPoint, Qt

from adsfolio.config import Config
from adsfolio.gui.config import GuiConfig, RootEntry, load_config, save_config
from adsfolio.gui.main_window import MainWindow
from adsfolio.gui.preferences import PreferencesDialog


@pytest.fixture
def combined_window(qtbot, monkeypatch, tmp_path):
    config_dir = tmp_path / "config"
    monkeypatch.setattr(MainWindow, "_queue_startup_ads_rechecks", lambda self: None)
    monkeypatch.setattr(MainWindow, "_queue_added_ads_recheck", lambda self, root: None)
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: config_dir))
    monkeypatch.setattr(Config, "token_source", classmethod(lambda cls: None))
    roots = [tmp_path / "first", tmp_path / "second"]
    for index, root in enumerate(roots):
        root.mkdir()
        (root / "same.pdf").write_bytes(f"fictional PDF {index}".encode())
    save_config(
        GuiConfig(
            roots=[
                RootEntry(str(root), label=f"Library {index}")
                for index, root in enumerate(roots)
            ],
            check_updates_automatically=False,
            citation_styles=[],
        )
    )
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    yield window, roots
    window.close()


def _root_item(window, root):
    return next(
        window.libraries_item.child(i)
        for i in range(window.libraries_item.childCount())
        if window.libraries_item.child(i).data(0, Qt.UserRole) == str(root)
    )


def _row_for_root(window, root):
    return next(
        i
        for i in range(window.model.rowCount())
        if window.model.record_at(i)["root"] == str(root)
    )


def _view_index_for_root(window, root, column=0):
    source = window.model.index(_row_for_root(window, root), column)
    index = window._view_table_index(source)
    assert index.isValid()
    return index


def test_checkbox_combines_libraries_and_persists_selection(combined_window, qtbot):
    window, roots = combined_window
    assert window.model.total == 1
    window.combined.toggle_item.setCheckState(0, Qt.Checked)
    assert window.combined.enabled
    assert window.model.total == 2
    assert all(_root_item(window, root).checkState(0) == Qt.Checked for root in roots)
    filename = next(c for c in window.columns if c.key == "filename")
    assert {filename.getter(window.model.record_at(row)) for row in range(2)} == {
        "Library 0 / same.pdf",
        "Library 1 / same.pdf",
    }

    _root_item(window, roots[0]).setCheckState(0, Qt.Unchecked)
    assert window.model.total == 1
    assert window.model.record_at(0)["root"] == str(roots[1])
    window.close()
    config = load_config()
    assert config.combined_view is True
    assert config.combined_roots == [str(roots[1])]

    reopened = MainWindow()
    qtbot.addWidget(reopened)
    assert reopened.combined.enabled
    assert reopened.model.total == 1
    assert reopened.model.record_at(0)["root"] == str(roots[1])
    _root_item(reopened, roots[1]).setCheckState(0, Qt.Unchecked)
    assert reopened.model.total == 0
    reopened.combined.toggle_item.setCheckState(0, Qt.Unchecked)
    assert not reopened.combined.enabled
    assert reopened.model.total == 1
    reopened.close()


def test_combined_open_and_details_use_each_records_own_root(
    combined_window, monkeypatch
):
    window, roots = combined_window
    files = [root / "same.pdf" for root in roots]
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    window.combined.set_enabled(True)
    opened = []
    revealed = []
    monkeypatch.setattr(
        "adsfolio.gui.main_window.open_path",
        lambda path, command: opened.append(path) or True,
    )
    monkeypatch.setattr(
        "adsfolio.gui.detail_pane.open_path",
        lambda path, command: opened.append(path) or True,
    )
    monkeypatch.setattr(
        "adsfolio.gui.detail_pane.reveal_in_file_manager",
        lambda path: revealed.append(path) or True,
    )

    for root in roots:
        row = _row_for_root(window, root)
        index = _view_index_for_root(window, root)
        window.table.setCurrentIndex(index)
        record = window.model.record_at(row)
        assert window.detail._root == root
        assert window.detail.update_button.isEnabled()
        assert window.detail.manual_metadata_button.isEnabled()
        assert window.detail._row_widgets["title"]._editable
        assert window.detail._row_widgets["title"]._lockable
        assert window.update_selected_ads_action.isEnabled()
        assert not window.refresh_selected_ads_action.isEnabled()
        window._open_index(index)
        window._open_filename(record)
        window._open_current()
        window.detail._open()
        window.detail._reveal()
        assert opened[-4:] == [root / "same.pdf"] * 4
        assert revealed[-1] == root / "same.pdf"

    assert not window.identify_action.isEnabled()
    assert not window.rename_action.isEnabled()
    assert window.refresh_action.isEnabled()
    assert not window.reconcile_file_facts_action.isEnabled()
    assert not window.remove_missing_action.isEnabled()
    assert not window.merge_annotations_action.isEnabled()
    # Export uses the shared catalog. ADS library intake needs one owner root.
    assert window.export_all_action.isEnabled()
    assert window.export_selected_action.isEnabled()
    assert window.add_to_ads_library_action.isEnabled()
    assert not window.link_bibcodes_action.isEnabled()
    assert not window.refresh_all_ads_action.isEnabled()
    assert not window.refresh_all_citation_metrics_action.isEnabled()
    assert {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in files
    } == before


def test_show_in_library_restores_owner_for_editing(combined_window):
    window, roots = combined_window
    window.combined.set_enabled(True)
    record = window.model.record_at(_row_for_root(window, roots[1]))
    window.combined.show_in_library(record)

    assert not window.combined.enabled
    assert window.current_root == roots[1]
    assert window._selected_record()["location_id"] == record["location_id"]
    assert window.detail._root == roots[1]
    assert window.detail.update_button.isEnabled()
    assert window.identify_action.isEnabled()


def test_double_click_opens_combined_record_without_an_anchor_library(
    combined_window, qtbot, monkeypatch
):
    window, roots = combined_window
    window.combined.set_enabled(True)
    window.current_root = None
    opened = []
    monkeypatch.setattr(
        "adsfolio.gui.main_window.open_path",
        lambda path, command: opened.append(path) or True,
    )
    title = next(i for i, column in enumerate(window.columns) if column.key == "title")
    index = _view_index_for_root(window, roots[1], title)
    parent = index.parent()
    while parent.isValid():
        window.table.expand(parent)
        parent = parent.parent()
    window.table.scrollTo(index)
    point = window.table.visualRect(index).center()
    qtbot.mouseClick(window.table.viewport(), Qt.LeftButton, pos=point)
    qtbot.mouseDClick(window.table.viewport(), Qt.LeftButton, pos=point)

    assert opened == [roots[1] / "same.pdf"]


def test_combined_sort_search_and_refresh_keep_all_checked_roots(combined_window):
    window, roots = combined_window
    window.combined.set_enabled(True)
    filename_index = next(
        i for i, c in enumerate(window.columns) if c.key == "filename"
    )
    window.table.sortByColumn(filename_index, Qt.AscendingOrder)
    assert window.current_query.order_by == "filename"
    assert [window.model.record_at(i)["root"] for i in range(2)] == list(
        map(str, roots)
    )
    window.search.setText("same.pdf")
    window.search_timer.stop()
    window._search_changed()
    assert window.model.total == 2
    assert window.model.query.order_by == "filename"
    window.refresh(roots[1])
    assert window.model.total == 2
    assert window.model.query.order_by == "filename"


def test_combined_watch_refresh_preserves_selected_owner(combined_window):
    window, roots = combined_window
    window.combined.set_enabled(True)
    window.table.setCurrentIndex(_view_index_for_root(window, roots[1]))
    original = window._selected_record()
    window._schedule_watch_refresh(roots[0])
    window._run_watch_refresh()

    assert window._selected_record()["location_id"] == original["location_id"]
    assert window.detail._root == roots[1]
    assert window.model.total == 2


def test_combined_actions_follow_selection(combined_window):
    window, roots = combined_window
    registry = window.registries[str(roots[1])]
    assert registry.replace_file_metadata(
        roots[1], "same.pdf", {"bibcode": "2024ApJ...123..456F"}
    )
    window.combined.set_enabled(True)

    assert not window.detail.update_button.isEnabled()
    assert not window.detail.manual_metadata_button.isEnabled()
    assert not window.update_selected_ads_action.isEnabled()
    assert not window.refresh_selected_ads_action.isEnabled()

    first = _view_index_for_root(window, roots[0])
    window.table.setCurrentIndex(first)
    assert window.detail.update_button.isEnabled()
    assert window.detail.manual_metadata_button.isEnabled()
    assert window.update_selected_ads_action.isEnabled()
    assert not window.refresh_selected_ads_action.isEnabled()

    second = _view_index_for_root(window, roots[1])
    window.table.setCurrentIndex(second)
    assert window.detail._root == roots[1]
    assert window.update_selected_ads_action.isEnabled()
    assert window.refresh_selected_ads_action.isEnabled()
    assert window.ads_refresh_button.isEnabled()

    window.table.selectionModel().select(
        first, QItemSelectionModel.Select | QItemSelectionModel.Rows
    )
    assert len(window.table.selectionModel().selectedRows()) == 2
    assert not window.detail.update_button.isEnabled()
    assert not window.detail.manual_metadata_button.isEnabled()
    assert not window.update_selected_ads_action.isEnabled()
    assert not window.refresh_selected_ads_action.isEnabled()


def test_combined_root_wide_handlers_do_not_target_anchor_library(
    combined_window, monkeypatch
):
    window, roots = combined_window
    window.combined.set_enabled(True)
    window.table.setCurrentIndex(_view_index_for_root(window, roots[1]))
    assert window.current_root == roots[0]

    def unexpected(*args, **kwargs):
        pytest.fail("Combined-table action targeted the anchor library")

    monkeypatch.setattr(window, "_request_foreground_dialog", unexpected)
    monkeypatch.setattr(window, "_start_ads_refresh", unexpected)
    monkeypatch.setattr("adsfolio.gui.main_window.QMessageBox.question", unexpected)
    monkeypatch.setattr("adsfolio.gui.main_window.QMessageBox.information", unexpected)
    monkeypatch.setattr(
        window.registries[str(roots[0])], "replace_file_metadata", unexpected
    )
    monkeypatch.setattr(
        window.registries[str(roots[0])], "delete_files_by_ids", unexpected
    )
    window._identify()
    window._rename()
    window._refresh_all_ads()
    window._refresh_all_citation_metrics()
    window._link_stored_bibcodes()
    window._remove_missing_records()


def test_removing_a_combined_root_keeps_remaining_view_usable(combined_window):
    window, roots = combined_window
    window.combined.set_enabled(True)
    window._remove_root(roots[0])

    assert window.combined.enabled
    assert window.current_root == roots[1]
    assert window.model.total == 1
    assert window.model.record_at(0)["root"] == str(roots[1])
    assert (roots[0] / "same.pdf").is_file()


def test_missing_root_reports_partial_results_and_path_escape_is_refused(
    combined_window,
):
    window, roots = combined_window
    window.combined.set_enabled(True)
    (roots[1] / "same.pdf").unlink()
    roots[1].rmdir()
    window.combined.refresh()

    assert window.model.total == 1
    assert "unavailable" in window.status.currentMessage()
    assert any(
        notice.key == "combined-unavailable"
        for notice in window.notice_bar.queued_notices()
    )
    record = dict(window.model.record_at(0), current_path="../escape.pdf")
    assert window.combined.path_for(record) is None
    roots[1].mkdir()
    window.refresh_action.trigger()
    assert "unavailable" not in window.status.currentMessage()
    assert not any(
        notice.key == "combined-unavailable"
        for notice in window.notice_bar.queued_notices()
    )


@pytest.fixture
def restore_roots(monkeypatch, tmp_path):
    """Create isolated libraries and preferences for restore-failure tests."""
    config_dir = tmp_path / "config"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: config_dir))
    monkeypatch.setattr(Config, "token_source", classmethod(lambda cls: None))
    roots = [tmp_path / "first", tmp_path / "second"]
    for index, root in enumerate(roots):
        root.mkdir()
        (root / "same.pdf").write_bytes(f"fictional PDF {index}".encode())
    return roots


def _fail_root_registration(monkeypatch, failed_roots):
    original = MainWindow._register_root

    def register(window, path, *args, **kwargs):
        if path in failed_roots:
            raise OSError("fictional temporary restore failure")
        return original(window, path, *args, **kwargs)

    monkeypatch.setattr(MainWindow, "_register_root", register)


def _save_combined_config(roots, selected, *, enabled=True):
    save_config(
        GuiConfig(
            roots=[RootEntry(str(root)) for root in roots],
            combined_view=enabled,
            combined_roots=selected,
            check_updates_automatically=False,
            citation_styles=[],
        )
    )


def test_failed_restore_keeps_selection_through_settings_sort_and_restart(
    restore_roots, qtbot, monkeypatch
):
    roots = restore_roots
    paths = [root / "same.pdf" for root in roots]
    hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
    _save_combined_config(roots, [str(root) for root in roots])
    with monkeypatch.context() as patch:
        _fail_root_registration(patch, {roots[1]})
        window = MainWindow()
    qtbot.addWidget(window)

    assert window.combined.enabled
    assert window.combined.selected == {str(root) for root in roots}
    assert window.model.total == 1
    settings = PreferencesDialog(window)
    qtbot.addWidget(settings)
    settings._save_general()
    assert set(load_config().combined_roots) == {str(root) for root in roots}

    _root_item(window, roots[0]).setCheckState(0, Qt.Unchecked)
    assert window.apply_sort("title", False)
    assert load_config().combined_roots == [str(roots[1])]
    settings.close()
    window.close()
    assert load_config().combined_roots == [str(roots[1])]

    restored = MainWindow()
    qtbot.addWidget(restored)
    assert restored.combined.enabled
    assert _root_item(restored, roots[0]).checkState(0) == Qt.Unchecked
    assert _root_item(restored, roots[1]).checkState(0) == Qt.Checked
    assert restored.model.total == 1
    assert restored.model.record_at(0)["root"] == str(roots[1])
    restored.close()
    assert [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths] == hashes


def test_all_failed_roots_keep_mode_and_selection_through_settings_and_restart(
    restore_roots, qtbot, monkeypatch
):
    roots = restore_roots
    _save_combined_config(roots, [str(roots[1])])
    with monkeypatch.context() as patch:
        _fail_root_registration(patch, set(roots))
        window = MainWindow()
    qtbot.addWidget(window)
    assert not window.combined.enabled

    settings = PreferencesDialog(window)
    qtbot.addWidget(settings)
    settings._save_general()
    saved = load_config()
    assert saved.combined_view
    assert saved.combined_roots == [str(roots[1])]
    assert {entry.path for entry in saved.roots} == {str(root) for root in roots}
    settings.close()
    window.close()
    assert load_config().combined_view
    assert load_config().combined_roots == [str(roots[1])]

    restored = MainWindow()
    qtbot.addWidget(restored)
    assert restored.combined.enabled
    assert _root_item(restored, roots[0]).checkState(0) == Qt.Unchecked
    assert _root_item(restored, roots[1]).checkState(0) == Qt.Checked
    assert restored.model.total == 1
    restored.close()


@pytest.mark.parametrize("enabled", [False, True])
def test_default_selection_includes_a_root_that_failed_to_restore(
    restore_roots, qtbot, monkeypatch, enabled
):
    roots = restore_roots
    _save_combined_config(roots, None, enabled=enabled)
    with monkeypatch.context() as patch:
        _fail_root_registration(patch, {roots[1]})
        window = MainWindow()
    qtbot.addWidget(window)
    if not enabled:
        window.combined.set_enabled(True)
    assert window.combined.selected == {str(root) for root in roots}
    window.close()
    assert set(load_config().combined_roots) == {str(root) for root in roots}

    restored = MainWindow()
    qtbot.addWidget(restored)
    assert restored.combined.enabled
    assert all(_root_item(restored, root).checkState(0) == Qt.Checked for root in roots)
    assert restored.model.total == 2
    restored.close()


def test_explicit_removal_keeps_other_unrestored_selection(
    restore_roots, qtbot, monkeypatch
):
    roots = restore_roots
    _save_combined_config(roots, [str(root) for root in roots])
    with monkeypatch.context() as patch:
        _fail_root_registration(patch, {roots[1]})
        window = MainWindow()
    qtbot.addWidget(window)
    window._remove_root(roots[0])
    assert window.combined.selected == {str(roots[1])}
    assert window.config.combined_roots == [str(roots[1])]
    window.close()
    saved = load_config()
    assert [entry.path for entry in saved.roots] == [str(roots[1])]
    assert saved.combined_roots == [str(roots[1])]

    restored = MainWindow()
    qtbot.addWidget(restored)
    restored.combined.set_enabled(True)
    assert _root_item(restored, roots[1]).checkState(0) == Qt.Checked
    assert restored.model.total == 1
    restored.close()
    assert (roots[0] / "same.pdf").is_file()


def test_explicitly_removed_root_is_selected_when_added_again(combined_window):
    window, roots = combined_window
    window.combined.set_enabled(True)
    window._remove_root(roots[0])
    assert str(roots[0]) not in window.combined.selected

    window._register_root(roots[0])
    window.combined.refresh()
    assert _root_item(window, roots[0]).checkState(0) == Qt.Checked
    assert set(window.config.combined_roots) == {str(root) for root in roots}
    assert window.model.total == 2


@pytest.mark.parametrize("was_selected", [False, True])
def test_recovered_root_retains_its_previous_check_state(
    restore_roots, qtbot, monkeypatch, was_selected
):
    roots = restore_roots
    selected = [str(roots[0])]
    if was_selected:
        selected.append(str(roots[1]))
    _save_combined_config(roots, selected)
    with monkeypatch.context() as patch:
        _fail_root_registration(patch, {roots[1]})
        window = MainWindow()
    qtbot.addWidget(window)

    window._register_root(roots[1])
    expected = Qt.Checked if was_selected else Qt.Unchecked
    assert _root_item(window, roots[1]).checkState(0) == expected
    assert window.config.combined_roots == selected
    window.close()
    assert load_config().combined_roots == selected


def _expanded_tree(window):
    window.combined.set_enabled(True)
    tree = window.combined.tree_model
    while tree.is_loading:
        tree._load_next_page()
    window.table.expandAll()
    return tree


def test_combined_tree_uses_half_indentation_and_restores_it(combined_window):
    window, _roots = combined_window
    default = window.table.indentation()
    _expanded_tree(window)
    assert window.table.indentation() == default // 2
    window.combined.set_enabled(False)
    assert window.table.indentation() == default


def test_combined_tree_shows_icons_and_aligns_files_with_their_folder(
    combined_window,
):
    from PySide6.QtWidgets import QStyleOptionViewItem

    window, _roots = combined_window
    tree = _expanded_tree(window)
    column = window.table.treePosition()
    folder = tree.index(0, column)
    file_index = tree.index(0, column, tree.index(0, 0))
    assert tree.node_kind(folder) == "root"
    assert tree.node_kind(file_index) == "file"
    assert tree.data(folder, Qt.DecorationRole) is not None
    assert tree.data(file_index, Qt.DecorationRole) is not None

    delegate = window.table.itemDelegateForIndex(file_index)
    folder_option = QStyleOptionViewItem()
    folder_option.widget = window.table
    folder_option.rect = window.table.visualRect(folder)
    delegate.initStyleOption(folder_option, folder)
    file_option = QStyleOptionViewItem()
    file_option.widget = window.table
    file_option.rect = window.table.visualRect(file_index)
    delegate.initStyleOption(file_option, file_index)
    # The file content starts where the folder content starts.
    assert file_option.rect.left() == folder_option.rect.left()
    assert folder_option.rect == window.table.visualRect(folder)


def test_click_on_moved_file_icon_selects_the_file(combined_window, qtbot):
    window, _roots = combined_window
    tree = _expanded_tree(window)
    column = window.table.treePosition()
    window.table.scrollTo(tree.index(0, column))
    file_index = tree.index(0, column, tree.index(0, 0))
    rect = window.table.visualRect(file_index)
    point = QPoint(rect.left() - window.table.indentation() // 2, rect.center().y())
    qtbot.mouseClick(window.table.viewport(), Qt.LeftButton, pos=point)
    assert window.table.currentIndex().row() == file_index.row()
    assert tree.node_kind(window.table.currentIndex()) == "file"
