"""Test the embeddable citation preferences with isolated inputs."""

from __future__ import annotations

import os
import socket
from copy import deepcopy
from unittest.mock import Mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from adsfolio.citations import CitationStyle
from adsfolio.citekeys import KeyPolicy, KeyStyle
from adsfolio.config import Config
from adsfolio.gui import config as gui_config
from adsfolio.gui.preferences_citations import CitationPreferences


@pytest.fixture(autouse=True)
def block_external_access(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail if the page tries to use configuration, files, or a network."""

    def forbidden(*args, **kwargs):
        pytest.fail("The preferences page attempted an external action.")

    monkeypatch.setattr(Config, "load_key_policy", forbidden)
    monkeypatch.setattr(Config, "save_key_policy", forbidden)
    monkeypatch.setattr(gui_config, "load_config", forbidden)
    monkeypatch.setattr(gui_config, "save_config", forbidden)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", forbidden)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", forbidden)
    monkeypatch.setattr(QDesktopServices, "openUrl", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


@pytest.fixture
def examples() -> tuple[KeyPolicy, list[CitationStyle], dict]:
    """Provide fictional citation inputs."""
    return (
        KeyPolicy(),
        [
            CitationStyle(id="fictional-a", name="First", template="{year}"),
            CitationStyle(id="fictional-b", name="Second", template="{title}"),
        ],
        {
            "metadata": {
                "year": "2031",
                "title": ["Fictional Comet"],
                "author": ["Example, Alex"],
            }
        },
    )


@pytest.fixture
def page(qtbot, examples):
    """Create the page inside a parent settings widget."""
    policy, styles, record = examples
    host = QWidget()
    layout = QVBoxLayout(host)
    save_policy = Mock()
    save_styles = Mock()
    widget = CitationPreferences(
        policy,
        styles,
        save_policy=save_policy,
        save_styles=save_styles,
        preview_record=record,
        parent=host,
    )
    layout.addWidget(widget)
    qtbot.addWidget(host)
    host.resize(720, 560)
    host.show()
    return widget, host, save_policy, save_styles


def _save_button(buttons: QDialogButtonBox) -> QPushButton:
    return buttons.button(QDialogButtonBox.StandardButton.Save)


def test_open_and_close_do_not_change_inputs(qtbot, examples):
    policy, styles, record = examples
    original = deepcopy(examples)
    save_policy = Mock()
    save_styles = Mock()
    widget = CitationPreferences(
        policy,
        styles,
        save_policy=save_policy,
        save_styles=save_styles,
        preview_record=record,
    )
    qtbot.addWidget(widget)
    widget.show()
    assert examples == original
    widget.key_dialog.other_style.setCurrentIndex(
        widget.key_dialog.other_style.findData(KeyStyle.SCHOLAR)
    )
    widget.tabs.setCurrentIndex(1)
    widget.styles_dialog.delete_button.click()
    widget.close()

    assert examples == original
    save_policy.assert_not_called()
    save_styles.assert_not_called()


def test_embeds_tabs_without_implicit_saves_or_external_actions(page, examples, qtbot):
    widget, host, save_policy, save_styles = page
    policy, styles, record = examples
    original = deepcopy(examples)

    assert [widget.tabs.tabText(i) for i in range(widget.tabs.count())] == [
        "Keys",
        "Styles",
    ]
    assert not widget.key_dialog.isWindow()
    assert not widget.styles_dialog.isWindow()
    assert widget.key_dialog.isVisible()
    assert not widget.key_dialog.buttons.button(
        QDialogButtonBox.StandardButton.Cancel
    ).isVisible()
    assert "2020ApJ...900....1K" in widget.key_dialog.preview.text()

    widget.key_dialog.other_style.setCurrentIndex(
        widget.key_dialog.other_style.findData(KeyStyle.SCHOLAR)
    )
    qtbot.keyClick(widget.key_dialog.other_style, Qt.Key.Key_Escape)
    assert widget.isVisible()
    assert widget.key_dialog.isVisible()

    widget.tabs.setCurrentIndex(1)
    assert widget.styles_dialog.isVisible()
    assert not widget.styles_dialog.button_box.button(
        QDialogButtonBox.StandardButton.Cancel
    ).isVisible()
    widget.styles_dialog.template_edit.setText("{year}: {title}")
    widget.styles_dialog.preview_button.click()
    assert widget.styles_dialog.preview_label.text() == "2031: Fictional Comet"
    qtbot.keyClick(widget.styles_dialog.template_edit, Qt.Key.Key_Escape)
    assert widget.styles_dialog.isVisible()

    labels = [button.text().lower() for button in widget.findChildren(QPushButton)]
    labels += [action.text().lower() for action in widget.findChildren(QAction)]
    assert not any("export" in label or "remote" in label for label in labels)
    assert not widget.styles_dialog.help_view.openExternalLinks()
    host.close()

    assert (policy, styles, record) == original
    save_policy.assert_not_called()
    save_styles.assert_not_called()


def test_key_save_reuses_policy_validation_and_keeps_page_open(page):
    widget, host, save_policy, save_styles = page
    dialog = widget.key_dialog
    dialog.ads_style.setCurrentIndex(dialog.ads_style.findData(KeyStyle.TEMPLATE))
    dialog.other_style.setCurrentIndex(dialog.other_style.findData(KeyStyle.MANUAL))
    save = _save_button(dialog.buttons)

    dialog.template.setText("{unknown_field}")
    assert not save.isEnabled()
    save.click()
    save_policy.assert_not_called()

    dialog.template.setText("{year}{first_author_initials}")
    assert save.isEnabled()
    save.click()
    save_policy.assert_called_once_with(
        KeyPolicy(
            ads=KeyStyle.TEMPLATE,
            other=KeyStyle.MANUAL,
            template="{year}{first_author_initials}",
        )
    )
    save_styles.assert_not_called()
    assert host.isVisible() and widget.isVisible() and dialog.isVisible()
    host.close()
    save_policy.assert_called_once()


def test_return_saves_only_the_active_tab_in_a_settings_dialog(qtbot, examples):
    policy, styles, record = examples
    host = QDialog()
    layout = QVBoxLayout(host)
    save_policy = Mock()
    save_styles = Mock()
    widget = CitationPreferences(
        policy,
        styles,
        save_policy=save_policy,
        save_styles=save_styles,
        preview_record=record,
        parent=host,
    )
    layout.addWidget(widget)
    close_buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, host)
    close_buttons.rejected.connect(host.reject)
    layout.addWidget(close_buttons)
    qtbot.addWidget(host)
    host.show()

    key_dialog = widget.key_dialog
    key_dialog.ads_style.setCurrentIndex(
        key_dialog.ads_style.findData(KeyStyle.TEMPLATE)
    )
    key_dialog.template.setText("{year}{first_author_initials}")
    key_dialog.template.setFocus()
    qtbot.keyClick(key_dialog.template, Qt.Key.Key_Return)
    save_policy.assert_called_once()
    save_styles.assert_not_called()
    assert host.isVisible() and widget.isVisible()

    widget.tabs.setCurrentIndex(1)
    style_dialog = widget.styles_dialog
    style_dialog.name_edit.setText("Changed")
    style_dialog.name_edit.setFocus()
    qtbot.keyClick(style_dialog.name_edit, Qt.Key.Key_Return)
    save_policy.assert_called_once()
    save_styles.assert_called_once()
    assert save_styles.call_args.args[0][0].name == "Changed"
    assert host.isVisible() and widget.isVisible() and style_dialog.isVisible()


@pytest.mark.parametrize("invalid", ["template", "duplicate_name"])
def test_style_validation_blocks_save(page, monkeypatch, invalid):
    widget, host, save_policy, save_styles = page
    widget.tabs.setCurrentIndex(1)
    dialog = widget.styles_dialog
    warning = Mock()
    monkeypatch.setattr(QMessageBox, "critical", warning)
    if invalid == "template":
        dialog.template_edit.setText("{unknown_field}")
    else:
        dialog.name_edit.setText(" SECOND ")

    _save_button(dialog.button_box).click()

    warning.assert_called_once()
    assert widget.isVisible() and host.isVisible() and dialog.isVisible()
    assert widget.tabs.currentIndex() == 1
    save_policy.assert_not_called()
    save_styles.assert_not_called()


def test_styles_save_preserves_order_preview_and_initial_values(page, examples):
    widget, host, save_policy, save_styles = page
    _, styles, record = examples
    original_styles = tuple(styles)
    original_record = deepcopy(record)
    widget.tabs.setCurrentIndex(1)
    dialog = widget.styles_dialog
    dialog.name_edit.setText("Revised")
    dialog.template_edit.setText("{year}: {title}")
    dialog.preview_button.click()
    assert dialog.preview_label.text() == "2031: Fictional Comet"
    dialog.duplicate_button.click()
    dialog.move_down_button.click()
    _save_button(dialog.button_box).click()

    save_styles.assert_called_once()
    (saved_styles,) = save_styles.call_args.args
    assert isinstance(saved_styles, tuple)
    assert [style.name for style in saved_styles] == [
        "Revised",
        "Second",
        "Copy of Revised",
    ]
    assert saved_styles[0].template == "{year}: {title}"
    assert len({style.id for style in saved_styles}) == 3
    assert tuple(styles) == original_styles and record == original_record
    assert widget.isVisible() and dialog.isVisible() and host.isVisible()
    save_policy.assert_not_called()


def test_empty_styles_save_and_builtin_preview(qtbot, examples):
    policy, styles, _ = examples
    save_styles = Mock()
    widget = CitationPreferences(
        policy, styles[:1], save_policy=Mock(), save_styles=save_styles
    )
    qtbot.addWidget(widget)
    widget.show()
    widget.tabs.setCurrentIndex(1)
    dialog = widget.styles_dialog
    assert "example" in dialog.preview_note_label.text().lower()
    dialog.delete_button.click()
    _save_button(dialog.button_box).click()
    save_styles.assert_called_once_with(())
    assert widget.isVisible() and dialog.isVisible()


@pytest.mark.parametrize("failure", ["exception", "false"])
@pytest.mark.parametrize("failed_tab", ["keys", "styles"])
def test_save_failure_preserves_drafts_and_other_preferences(
    qtbot, examples, monkeypatch, failure, failed_tab
):
    policy, styles, record = examples
    store = {
        "policy": policy,
        "styles": tuple(styles),
        "other": {"font": "Example", "columns": ["title", "year"]},
    }
    original = deepcopy(store)
    attempts = []

    def save_value(name, value):
        attempts.append(name)
        if name == failed_tab and attempts.count(name) == 1:
            if failure == "exception":
                raise OSError("Fictional save failure")
            return False
        store["policy" if name == "keys" else name] = value
        return None

    save_policy = Mock(side_effect=lambda value: save_value("keys", value))
    save_styles = Mock(side_effect=lambda value: save_value("styles", value))
    warning = Mock()
    monkeypatch.setattr(QMessageBox, "warning", warning)
    widget = CitationPreferences(
        policy,
        styles,
        save_policy=save_policy,
        save_styles=save_styles,
        preview_record=record,
    )
    qtbot.addWidget(widget)
    widget.show()
    widget.key_dialog.other_style.setCurrentIndex(
        widget.key_dialog.other_style.findData(KeyStyle.SCHOLAR)
    )
    widget.tabs.setCurrentIndex(1)
    widget.styles_dialog.name_edit.setText("Changed")

    failed_index = 0 if failed_tab == "keys" else 1
    failed_dialog = widget.key_dialog if failed_index == 0 else widget.styles_dialog
    failed_buttons = (
        widget.key_dialog.buttons
        if failed_index == 0
        else widget.styles_dialog.button_box
    )
    widget.tabs.setCurrentIndex(failed_index)
    _save_button(failed_buttons).click()

    warning.assert_called_once()
    assert "not saved" in warning.call_args.args[1].lower()
    if failure == "exception":
        assert "Fictional save failure" in warning.call_args.args[2]
    assert store == original
    assert widget.isVisible() and failed_dialog.isVisible()
    assert widget.key_dialog.other_style.currentData() == KeyStyle.SCHOLAR
    assert widget.styles_dialog.name_edit.text() == "Changed"

    other_index = 1 - failed_index
    widget.tabs.setCurrentIndex(other_index)
    other_buttons = (
        widget.key_dialog.buttons
        if other_index == 0
        else widget.styles_dialog.button_box
    )
    _save_button(other_buttons).click()
    assert store["other"] == original["other"]
    widget.tabs.setCurrentIndex(failed_index)
    _save_button(failed_buttons).click()

    assert store["policy"].other is KeyStyle.SCHOLAR
    assert store["styles"][0].name == "Changed"
    assert store["other"] == original["other"]
    assert widget.isVisible() and failed_dialog.isVisible()
    assert save_policy.call_count == (2 if failed_tab == "keys" else 1)
    assert save_styles.call_count == (2 if failed_tab == "styles" else 1)
    widget.close()
