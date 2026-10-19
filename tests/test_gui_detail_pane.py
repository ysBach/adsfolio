"""Tests for the metadata detail pane layout."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QFont,
    QFontMetrics,
    QStandardItemModel,
    QTextCharFormat,
    QTextDocument,
)
from PySide6.QtWidgets import QApplication, QHeaderView, QInputDialog, QToolBar

from adsfolio.citations import CitationStyle
from adsfolio.config import Config
from adsfolio.gui.config import GuiConfig
from adsfolio.gui.detail_pane import DetailPane
from adsfolio.gui.library import COLUMNS, FileQuery
from adsfolio.gui.main_window import MainWindow
from adsfolio.gui.models import FileTableModel, FirstAuthorDelegate, LinkDelegate
from adsfolio.pdf.registry import PdfRegistry


@pytest.fixture(scope="module")
def application() -> QApplication:
    """Provide one Qt application for detail-pane layout checks."""
    return QApplication.instance() or QApplication([])


def test_detail_values_use_available_width_and_wrap_safely(
    application: QApplication,
) -> None:
    """Long metadata uses the value column rather than overflowing the pane."""
    pane = DetailPane()
    pane.resize(800, 900)
    digest = "dc253a216d584717d8ec96f8fe341ecc29b871b0777777777777777777777777"
    pane.set_record(
        {
            "current_path": (
                "2006MNRAS.373..799C_Collier_CameronA+PollaccoD_"
                "A_fast_hybrid_approach_to_exoplanet_discovery.pdf"
            ),
            "size_bytes": 1_100_000,
            "mtime_ns": 0,
            "sha256": digest,
            "metadata": {
                "title": "A fast hybrid approach to exoplanet discovery",
                "pub": "Monthly Notices of the Royal Astronomical Society",
                "doi": "10.1111/j.1365-2966.2006.10902.x",
            },
        },
        Path("/Users/ysbach/Dropbox/Studocs/PublOCR"),
    )
    pane.show()
    application.processEvents()

    for field in ("title", "pub", "doi", "filename", "full_path", "sha256"):
        value = pane._labels[field]
        assert value.width() > pane._row_widgets[field].name.width()
        assert value.mapTo(pane, value.rect().topRight()).x() <= pane.width() - 1
    assert "hybrid" in pane._labels["title"].text()
    assert "\u200b" not in pane._labels["title"].text()
    assert pane._labels["sha256"].text().replace("\u200b", "") == digest

    pane.close()


def test_detail_pane_renders_and_copies_each_doi_separately(
    application: QApplication,
) -> None:
    """Every DOI is shown on its own clickable line with its own copy value."""
    pane = DetailPane()
    pane.set_record(
        {
            "current_path": "paper.pdf",
            "metadata": {"doi": ["10.1000/first", "10.1000/second"]},
        },
        Path("/papers"),
    )

    row = pane._row_widgets["doi"]

    assert row.value.text().replace("\u200b", "") == "10.1000/first"
    assert [label.text().replace("\u200b", "") for label in row.doi_labels] == [
        "10.1000/second"
    ]
    assert row.doi_labels[0]._copy_text == "10.1000/second"
    pane.close()


def test_detail_rows_default_to_modified_first_and_persist_drag_order(
    application: QApplication,
) -> None:
    """Detail rows expose a stable default order and a user-selected order."""
    pane = DetailPane()
    pane.set_row_order(["title", "modified"])
    pane.set_record(
        {
            "current_path": "paper.pdf",
            "size_bytes": 1,
            "mtime_ns": 0,
            "sha256": "a" * 64,
            "metadata": {"title": ["Title"], "bibcode": "B1"},
        },
        Path("/papers"),
    )
    pane.show()
    application.processEvents()

    assert pane.row_keys()[:2] == ["title", "modified"]
    assert pane.row_label("full_path") == "Full path"

    pane.close()


def test_detail_row_spacing_updates_existing_rows(
    application: QApplication,
) -> None:
    """Changing detail-row separation updates the container immediately."""
    pane = DetailPane()
    pane.set_record(
        {
            "current_path": "paper.pdf",
            "metadata": {
                "title": "Title",
                "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzz": "Value",
            },
        },
        Path("/papers"),
    )

    pane.set_row_spacing(12)
    pane.show()
    application.processEvents()

    assert pane._rows.rows_layout.spacing() == 12

    pane.set_row_spacing(None)
    assert pane._rows.rows_layout.spacing() == 0

    pane.set_row_spacing(12)
    pane.set_record(
        {"current_path": "other.pdf", "metadata": {"title": "Other title"}},
        Path("/papers"),
    )
    application.processEvents()
    assert pane._rows.rows_layout.spacing() == 12
    pane.close()


def test_detail_action_button_visibility_updates_existing_and_new_rows(
    application: QApplication,
) -> None:
    """Global edit and lock visibility applies across record refreshes."""
    pane = DetailPane()
    record = {
        "current_path": "paper.pdf",
        "metadata": {"title": "Title"},
    }
    pane.set_record(record, Path("/papers"))
    row = pane._row_widgets["title"]

    assert not row.edit_button.isHidden()
    assert not row.lock_button.isHidden()

    pane.set_action_buttons_visible(show_edit=False, show_lock=False)

    assert row.edit_button.isHidden()
    assert row.lock_button.isHidden()

    pane.set_record(record, Path("/papers"))
    refreshed_row = pane._row_widgets["title"]
    assert refreshed_row.edit_button.isHidden()
    assert refreshed_row.lock_button.isHidden()

    pane.set_action_buttons_visible(show_edit=True, show_lock=True)

    assert not refreshed_row.edit_button.isHidden()
    assert not refreshed_row.lock_button.isHidden()
    pane.close()


def test_expanded_abstract_grows_naturally(
    application: QApplication,
) -> None:
    """Expanding an abstract changes its natural height without row constraints."""
    pane = DetailPane()
    pane.resize(420, 700)
    pane.set_record(
        {"current_path": "paper.pdf", "metadata": {"abstract": "word " * 200}},
        Path("/papers"),
    )
    pane.show()
    application.processEvents()
    row = pane._row_widgets["abstract"]
    collapsed_height = row.height()

    row.abstract_toggle.click()
    application.processEvents()
    assert row.height() > collapsed_height

    row.abstract_toggle.click()
    application.processEvents()
    assert row.height() == collapsed_height
    pane.close()


def test_default_expanded_abstract_is_top_aligned_after_layout(
    application: QApplication,
) -> None:
    """Default-expanded abstracts do not retain a pre-layout oversized height."""
    pane = DetailPane()
    pane.resize(420, 700)
    pane.set_expand_abstracts(True)
    pane.set_record(
        {"current_path": "paper.pdf", "metadata": {"abstract": "word " * 200}},
        Path("/papers"),
    )
    pane.show()
    application.processEvents()

    row = pane._row_widgets["abstract"]
    assert row.value.alignment() & Qt.AlignTop
    assert row.value.minimumHeight() == 0
    assert row.value.geometry().top() <= row.layout().contentsMargins().top() + 2
    pane.close()


def test_abstract_superscript_markup_does_not_leak_into_following_text(
    application: QApplication,
) -> None:
    """ADS ``<SUP>`` markup renders as a superscript without corrupting the tag."""
    pane = DetailPane()
    pane.set_record(
        {
            "current_path": "paper.pdf",
            "metadata": {
                "abstract": "quoted<SUP>1</SUP> were obtained by Barnard",
            },
        },
        Path("/papers"),
    )
    application.processEvents()

    document = QTextDocument()
    document.setHtml(pane._labels["abstract"].text())
    block = document.firstBlock()
    fragments = []
    iterator = block.begin()
    while not iterator.atEnd():
        fragment = iterator.fragment()
        fragments.append(
            (
                fragment.text().replace("\u200b", ""),
                fragment.charFormat().verticalAlignment(),
            )
        )
        iterator += 1

    assert fragments == [
        ("quoted", QTextCharFormat.AlignNormal),
        ("1", QTextCharFormat.AlignSuperScript),
        (" were obtained by Barnard", QTextCharFormat.AlignNormal),
    ]
    pane.close()


def test_copying_an_abstract_yields_plain_text(
    application: QApplication,
) -> None:
    """A copy click places unmarked text on the clipboard, not ADS markup."""
    pane = DetailPane()
    pane.set_record(
        {
            "current_path": "paper.pdf",
            "metadata": {"abstract": "quoted<SUP>1</SUP> by Barnard &amp; Kuiper"},
        },
        Path("/papers"),
    )
    application.processEvents()

    label = pane._labels["abstract"]
    assert "<SUP>" in label.text()
    assert label._copy_text == "quoted1 by Barnard & Kuiper"
    pane.close()


def test_detail_label_boldness_is_configurable(
    application: QApplication,
) -> None:
    """The detail-pane field-label font follows the configured boldness."""
    pane = DetailPane()
    pane.set_record(
        {
            "current_path": "paper.pdf",
            "metadata": {
                "title": "Title",
                "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzz": "Value",
            },
        },
        Path("/papers"),
    )
    assert pane._row_widgets["title"].name.font().bold()

    long_label = pane._row_widgets["zzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"].name
    assert (
        long_label.fontMetrics().horizontalAdvance(long_label.text())
        > long_label.width()
    )
    assert long_label.text() in long_label.toolTip()

    pane.set_label_bold(False)
    assert not pane._row_widgets["title"].name.font().bold()
    pane.set_record(
        {"current_path": "other.pdf", "metadata": {"title": "Other title"}},
        Path("/papers"),
    )
    assert not pane._row_widgets["title"].name.font().bold()
    pane.close()


def test_detail_rows_move_without_losing_widgets_or_order(
    application: QApplication,
) -> None:
    """Moving a row retains all row widgets for the next selected record."""
    pane = DetailPane()
    record = {
        "current_path": "paper.pdf",
        "size_bytes": 1,
        "mtime_ns": 0,
        "sha256": "a" * 64,
        "metadata": {"title": ["Title"], "year": "2020", "bibcode": "B1"},
    }
    pane.set_record(record, Path("/papers"))

    pane.move_row("year", 0)
    pane.set_record(record, Path("/papers"))

    assert pane.row_keys()[0] == "year"
    assert set(pane.row_keys()) == set(pane._row_widgets)
    assert not pane._row_widgets["links"].link_label.openExternalLinks()
    assert not pane._row_widgets["title"].lock_button.icon().isNull()
    assert pane._row_widgets["title"].lock_button.accessibleName() == "Lock Title"
    pane.close()


def test_detail_links_use_configured_browser(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detail-pane ADS links use the configured browser command."""
    pane = DetailPane()
    pane.set_application_commands(
        pdf_viewer_command=None,
        web_browser_command="web-browser",
    )
    pane.set_record(
        {
            "current_path": "paper.pdf",
            "metadata": {"bibcode": "2024ApJ...900..100S"},
        },
        Path("/papers"),
    )
    opened: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        "adsfolio.gui.detail_pane.open_url",
        lambda url, application=None: opened.append((url, application)) or True,
    )

    pane._row_widgets["links"].link_label.linkActivated.emit(
        "https://adsabs.harvard.edu/abs/2024ApJ...900..100S"
    )

    assert opened == [
        (
            "https://adsabs.harvard.edu/abs/2024ApJ...900..100S",
            "web-browser",
        )
    ]
    pane.close()


def test_detail_rows_can_be_hidden_without_losing_their_visibility_option(
    application: QApplication,
) -> None:
    """A hidden row remains available for the user to re-enable."""
    pane = DetailPane()
    pane.set_hidden_rows(["bibcode"])
    pane.set_record(
        {
            "current_path": "paper.pdf",
            "size_bytes": 1,
            "mtime_ns": 0,
            "sha256": "a" * 64,
            "metadata": {"bibcode": "B1"},
        },
        Path("/papers"),
    )

    assert "bibcode" not in pane.row_keys()
    assert ("bibcode", "Bibcode") in pane.available_rows()
    pane.close()


def test_detail_pane_does_not_render_source(
    application: QApplication,
) -> None:
    """Internal source metadata does not take space in the detail pane."""
    pane = DetailPane()
    pane.set_record(
        {
            "current_path": "paper.pdf",
            "metadata": {
                "bibcode": "B1",
                "current_filename": "paper.pdf",
                "source": "arxiv",
            },
        },
        Path("/papers"),
    )

    assert "source" not in pane.row_keys()
    assert "current_filename" not in pane.row_keys()
    assert "source" not in dict(pane.available_rows())
    pane.close()


def test_long_abstract_expands_and_collapses_in_place(
    application: QApplication,
) -> None:
    """A long abstract remains readable without permanently dominating the pane."""
    pane = DetailPane()
    pane.resize(600, 700)
    pane.set_record(
        {
            "current_path": "paper.pdf",
            "metadata": {"abstract": " ".join(["abstract"] * 160)},
        },
        Path("/papers"),
    )
    pane.show()
    application.processEvents()
    row = pane._row_widgets["abstract"]

    assert row.abstract_toggle.isVisible()
    assert not row.author_toggle.isVisible()
    assert not pane._row_widgets["author"].abstract_toggle.isVisible()
    assert row.abstract_toggle.text() == "Show more"
    collapsed_height = row.value.maximumHeight()
    collapsed_row_height = row.height()
    pane.move_row("abstract", 0)
    row.abstract_toggle.click()
    application.processEvents()

    assert row.abstract_toggle.text() == "Show less"
    assert row.value.maximumHeight() > collapsed_height
    assert row.height() > collapsed_row_height
    row.abstract_toggle.click()
    assert row.abstract_toggle.text() == "Show more"
    assert row.value.maximumHeight() == collapsed_height
    pane.close()


def test_first_author_delegate_separates_surname() -> None:
    """Only the surname is drawn in bold for a conventional ADS author name."""
    assert FirstAuthorDelegate.split_author("Bach, Yoonsoo P.") == (
        "Bach",
        ", Yoonsoo P.",
    )


def test_table_model_reports_a_locked_registry_without_raising() -> None:
    """A transient SQLite lock is reported to the GUI instead of crashing it."""

    class LockedRegistry:
        def query_files(self, **_kwargs):
            raise sqlite3.OperationalError("database is locked")

    model = FileTableModel(None, FileQuery())
    messages: list[str] = []
    model.query_failed.connect(messages.append)

    model.set_registry(LockedRegistry())

    assert messages == ["Library is busy; try again in a moment."]


def test_main_window_reports_locked_sidebar_count(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Sidebar/status count reads do not escape a transient SQLite lock."""
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))

    class LockedRegistry:
        def count_files_matching(self, **_kwargs):
            raise sqlite3.OperationalError("database is locked")

    window = MainWindow()
    assert window._safe_count(LockedRegistry()) is None
    assert window.status.currentMessage() == "Library is busy; try again in a moment."
    window.close()


def test_main_window_propagates_non_busy_sidebar_sqlite_errors(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Non-transient SQLite errors remain programming/data failures."""
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))

    class BrokenRegistry:
        def count_files_matching(self, **_kwargs):
            raise sqlite3.OperationalError("malformed query")

    window = MainWindow()
    with pytest.raises(sqlite3.OperationalError, match="malformed query"):
        window._safe_count(BrokenRegistry())
    window.close()


def test_main_window_does_not_bind_space_to_open(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Space remains available for native table navigation, not PDF opening."""
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    window = MainWindow()

    assert {
        shortcut.toString() for shortcut in window.open_selected_action.shortcuts()
    } == {
        "Return",
        "Enter",
    }
    window.close()


def test_table_header_context_menu_controls_column_visibility(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The header menu exposes checkable visibility for every table column."""
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    window = MainWindow()

    assert window.table.header().contextMenuPolicy() == Qt.CustomContextMenu
    menu = window._create_header_context_menu()
    actions = {
        action.text(): action for action in menu.actions() if not action.isSeparator()
    }

    assert {column.header or "Resolution marker" for column in COLUMNS}.issubset(
        actions
    )
    assert actions["Bibcode"].isCheckable()
    assert actions["Bibcode"].isChecked()

    actions["Bibcode"].trigger()

    bibcode_index = next(
        index for index, column in enumerate(COLUMNS) if column.key == "bibcode"
    )
    assert window.table.isColumnHidden(bibcode_index)
    assert "bibcode" in window.config.hidden_table_columns
    window.close()


def test_table_header_context_menu_restores_order_and_resizes(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Header utilities restore default order and request content sizing."""
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    window = MainWindow()
    header = window.table.header()
    header.moveSection(header.visualIndex(0), header.count() - 1)
    resized = []
    monkeypatch.setattr(
        header,
        "resizeSections",
        lambda mode: resized.append(mode),
    )

    actions = {
        action.text(): action
        for action in window._create_header_context_menu().actions()
        if not action.isSeparator()
    }
    actions["Restore default column order"].trigger()
    actions["Resize columns to contents"].trigger()

    assert [
        window.columns[header.logicalIndex(visual)].key
        for visual in range(header.count())
    ] == [column.key for column in window.columns]
    assert resized == [QHeaderView.ResizeToContents]
    window.close()


def test_main_table_links_open_ads_and_arxiv(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Clicking bibliographic link cells opens their public web endpoints."""
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    window = MainWindow()
    window.current_root = tmp_path
    window.config.web_browser_command = "web-browser"
    window.config.link_activation = "single"
    record = {
        "current_path": "paper.pdf",
        "metadata": {
            "bibcode": "2024ApJ...900..100S",
            "doi": "10.48550/arXiv.2606.12345",
        },
    }
    window.model.record_at = lambda _row: record
    window.table.setModel(QStandardItemModel(1, len(COLUMNS)))
    opened: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        "adsfolio.gui.main_window.open_url",
        lambda url, application=None: opened.append((url, application)) or True,
    )

    for key in ("bibcode", "arxiv"):
        column = next(index for index, item in enumerate(COLUMNS) if item.key == key)
        window._table_clicked(window.table.model().index(0, column))

    assert opened == [
        (
            "https://adsabs.harvard.edu/abs/2024ApJ...900..100S",
            "web-browser",
        ),
        ("https://arxiv.org/abs/2606.12345", "web-browser"),
    ]
    window.close()


def test_link_delegate_switches_to_icon_when_text_does_not_fit(
    application: QApplication,
) -> None:
    """Link cells use compact branding when their text cannot fit."""
    delegate = LinkDelegate("ADS")
    font = QFont()
    text_width = QFontMetrics(font).horizontalAdvance("2024A&A...1A...1B")

    assert not delegate.requires_icon("2024A&A...1A...1B", text_width + 20, font)
    assert delegate.requires_icon("2024A&A...1A...1B", 20, font)


def test_main_table_links_default_to_double_click(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Single clicks only select by default; double-clicks open the link once."""
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    window = MainWindow()
    window.current_root = tmp_path
    record = {
        "current_path": "paper.pdf",
        "metadata": {"bibcode": "2024ApJ...900..100S"},
    }
    window.model.record_at = lambda _row: record
    window.table.setModel(QStandardItemModel(1, len(COLUMNS)))
    opened: list[str] = []
    monkeypatch.setattr(
        "adsfolio.gui.main_window.open_url",
        lambda url, application=None: opened.append(url) or True,
    )
    column = next(i for i, item in enumerate(COLUMNS) if item.key == "bibcode")
    index = window.table.model().index(0, column)

    window._table_clicked(index)
    assert opened == []
    window._open_index(index)

    assert opened == ["https://adsabs.harvard.edu/abs/2024ApJ...900..100S"]
    window.close()


def test_single_click_then_double_click_opens_link_once(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """In single-click mode, the click pair of a double-click opens once."""
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    window = MainWindow()
    window.current_root = tmp_path
    window.config.link_activation = "single"
    record = {
        "current_path": "paper.pdf",
        "metadata": {"bibcode": "2024ApJ...900..100S"},
    }
    window.model.record_at = lambda _row: record
    window.table.setModel(QStandardItemModel(1, len(COLUMNS)))
    opened: list[str] = []
    monkeypatch.setattr(
        "adsfolio.gui.main_window.open_url",
        lambda url, application=None: opened.append(url) or True,
    )
    column = next(i for i, item in enumerate(COLUMNS) if item.key == "bibcode")
    index = window.table.model().index(0, column)

    window._table_clicked(index)
    window._open_index(index)

    assert len(opened) == 1
    window.close()


def test_link_suppression_is_keyed_by_target_and_reset_on_model_reset(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A different target or a reset table is never suppressed."""
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    window = MainWindow()
    window.current_root = tmp_path
    window.config.link_activation = "single"
    record = {"current_path": "a.pdf", "metadata": {"bibcode": "2024ApJ...900..100S"}}
    window.model.record_at = lambda _row: record
    window.table.setModel(QStandardItemModel(1, len(COLUMNS)))
    opened: list[str] = []
    monkeypatch.setattr(
        "adsfolio.gui.main_window.open_url",
        lambda url, application=None: opened.append(url) or True,
    )
    column = next(i for i, item in enumerate(COLUMNS) if item.key == "bibcode")
    index = window.table.model().index(0, column)

    window._table_clicked(index)
    record["metadata"] = {"bibcode": "2025ApJ...901..200B"}
    window._table_clicked(index)
    window._reset_link_activation()
    window._table_clicked(index)

    assert opened == [
        "https://adsabs.harvard.edu/abs/2024ApJ...900..100S",
        "https://adsabs.harvard.edu/abs/2025ApJ...901..200B",
        "https://adsabs.harvard.edu/abs/2025ApJ...901..200B",
    ]
    window.close()


def test_filename_click_uses_configured_reveal_action(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Filename clicks can reveal the file instead of opening it."""
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    window = MainWindow()
    window.current_root = tmp_path
    window.config.filename_click_action = "reveal"
    window.config.link_activation = "single"
    record = {"current_path": "paper.pdf", "metadata": {}}
    window.model.record_at = lambda _row: record
    window.table.setModel(QStandardItemModel(1, len(COLUMNS)))
    revealed: list[Path] = []
    monkeypatch.setattr(
        "adsfolio.gui.main_window.reveal_in_file_manager",
        lambda path: revealed.append(path) or True,
    )

    filename_column = next(
        index for index, item in enumerate(COLUMNS) if item.key == "filename"
    )
    window._table_clicked(window.table.model().index(0, filename_column))

    assert revealed == [tmp_path / "paper.pdf"]
    window.close()


def test_generated_table_cell_copies_display_text_and_shows_feedback(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A non-empty generated citation cell copies its displayed text."""
    style = CitationStyle(id="compact", name="Compact", template="{bibcode}")
    monkeypatch.setattr(
        "adsfolio.gui.main_window.load_config",
        lambda: GuiConfig(citation_styles=[style]),
    )
    monkeypatch.setattr("adsfolio.gui.main_window.save_config", lambda config: None)
    window = MainWindow()
    window.current_root = tmp_path
    window.model._rows = [
        {
            "current_path": "paper.pdf",
            "metadata": {"bibcode": "2024ApJ...900..100S"},
        }
    ]
    column = next(
        index
        for index, item in enumerate(window.columns)
        if item.key == "citation:compact"
    )
    QApplication.clipboard().setText("old clipboard")

    window._table_clicked(window.model.index(0, column))

    assert QApplication.clipboard().text() == "2024ApJ...900..100S"
    assert window.status.currentMessage() == "Copied"
    window.close()


def test_empty_generated_table_cell_keeps_clipboard_content(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An empty generated citation cell does not replace the clipboard."""
    style = CitationStyle(id="year", name="Year", template="{year}")
    monkeypatch.setattr(
        "adsfolio.gui.main_window.load_config",
        lambda: GuiConfig(citation_styles=[style]),
    )
    monkeypatch.setattr("adsfolio.gui.main_window.save_config", lambda config: None)
    window = MainWindow()
    window.current_root = tmp_path
    window.model._rows = [{"current_path": "paper.pdf", "metadata": {}}]
    column = next(
        index
        for index, item in enumerate(window.columns)
        if item.key == "citation:year"
    )
    QApplication.clipboard().setText("keep clipboard")

    window._table_clicked(window.model.index(0, column))

    assert QApplication.clipboard().text() == "keep clipboard"
    window.close()


def test_main_window_retries_selected_ambiguous_ads_candidate(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Choosing an ambiguous ADS candidate retries the selected file by bibcode."""
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    window = MainWindow()
    captured: list[tuple[tuple[str, ...], int | None]] = []
    monkeypatch.setattr(
        window,
        "_start_ads_refresh",
        lambda *args, selection_id=None, **_kwargs: captured.append(
            (args, selection_id)
        ),
    )
    monkeypatch.setattr(
        QInputDialog,
        "getItem",
        lambda *args, **kwargs: (args[3][0], True),
    )

    window._choose_ambiguous_ads_match(
        [
            {
                "bibcode": "1965Natur.207.1381B",
                "first_author": "Bastin, J. A.",
                "title": "Lunar Hot Spots",
            }
        ],
        "paper.pdf",
        17,
    )

    assert captured == [
        (
            ("--bibcode", "1965Natur.207.1381B", "--relative-path", "paper.pdf"),
            17,
        )
    ]
    window.close()


def test_main_window_reconciles_file_facts(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The file-facts menu action queues the same scan as the toolbar."""
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    window = MainWindow()

    window._register_root(tmp_path)
    window._select_root(tmp_path)
    queued: list[Path] = []
    monkeypatch.setattr(window, "_refresh_local_files", lambda: queued.append(tmp_path))

    window.reconcile_file_facts_action.trigger()

    assert queued == [tmp_path]
    window.close()


def test_detail_lock_is_persisted_in_emitted_metadata(
    application: QApplication,
) -> None:
    """Locking an ADS field emits a per-file lock-set update."""
    pane = DetailPane()
    changes: list[dict] = []
    pane.metadata_changed.connect(changes.append)
    pane.set_record(
        {
            "current_path": "paper.pdf",
            "size_bytes": 1,
            "mtime_ns": 0,
            "sha256": "a" * 64,
            "metadata": {"title": ["Title"]},
        },
        Path("/papers"),
    )

    pane._set_lock("title", True)

    assert changes[-1]["_adsfolio_locked_fields"] == ["title"]
    pane.close()


def test_main_window_persists_detail_metadata(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Detail edits are saved to the selected record by its stable path."""
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    root = tmp_path / "library"
    root.mkdir()
    (root / "paper.pdf").write_bytes(b"%PDF")
    registry = PdfRegistry(root)
    registry.upsert_file(
        root=root,
        relative_path="paper.pdf",
        current_path="paper.pdf",
        sha256="a" * 64,
        size_bytes=1,
        mtime_ns=2,
        status="resolved",
        metadata={"title": ["Old"]},
    )
    registry.close()
    window = MainWindow()
    window._register_root(root, "", False)
    window._select_root(root)
    window.show()
    application.processEvents()
    window.table.setCurrentIndex(window.model.index(0, 0))

    window._save_detail_metadata({"title": ["Corrected"]})

    stored = window.registries[str(root)].find_file_by_relative_path(root, "paper.pdf")
    assert stored is not None
    assert stored["metadata"]["title"] == ["Corrected"]
    window._refresh_selection_id = int(stored["id"])
    window._job_finished(0)
    selected = window.model.record_at(window.table.currentIndex().row())
    assert selected is not None
    assert selected["relative_path"] == "paper.pdf"
    window.close()


def test_citation_rows_generated_immediately_after_bibcode_and_not_editable(
    application: QApplication,
) -> None:
    """Citation rows follow Bibcode by default, are not editable/lockable, and copy text."""
    pane = DetailPane()
    pane.set_citation_styles(
        [
            CitationStyle(id="a", name="Short", template="{bibcode}"),
            CitationStyle(id="b", name="Readable", template="{year}: {title}"),
        ]
    )
    record = {
        "current_path": "paper.pdf",
        "size_bytes": 1,
        "mtime_ns": 0,
        "sha256": "a" * 64,
        "metadata": {
            "bibcode": "2024TEST",
            "year": "2024",
            "title": "A Test Title",
        },
    }
    pane.set_record(record, Path("/papers"))
    keys = pane.row_keys()
    bibcode_idx = keys.index("bibcode")
    assert keys[bibcode_idx + 1] == "citation:a"
    assert keys[bibcode_idx + 2] == "citation:b"

    row_a = pane._row_widgets["citation:a"]
    assert row_a.name.text() == "Short"
    assert row_a.value._copy_text == "2024TEST"
    assert not row_a.edit_button.isVisible()
    assert not row_a.lock_button.isVisible()

    row_b = pane._row_widgets["citation:b"]
    assert row_b.name.text() == "Readable"
    assert row_b.value._copy_text == "2024: A Test Title"
    assert not row_b.edit_button.isVisible()
    assert not row_b.lock_button.isVisible()
    pane.close()


def test_citation_rows_recomputed_on_record_and_style_change(
    application: QApplication,
) -> None:
    """Citation rows recompute when record or styles change, and support empty value."""
    pane = DetailPane()
    pane.set_citation_styles(
        [CitationStyle(id="c1", name="Cite", template="{bibcode}")]
    )
    pane.set_record(
        {"current_path": "first.pdf", "metadata": {"bibcode": "2020AAA"}},
        Path("/papers"),
    )
    assert pane._row_widgets["citation:c1"].value._copy_text == "2020AAA"

    # Record change
    pane.set_record(
        {"current_path": "second.pdf", "metadata": {"bibcode": "2021BBB"}},
        Path("/papers"),
    )
    assert pane._row_widgets["citation:c1"].value._copy_text == "2021BBB"

    # Style change rerenders current record
    pane.set_citation_styles(
        [CitationStyle(id="c2", name="NewCite", template="{year}")]
    )
    assert "citation:c1" not in pane._row_widgets
    assert "citation:c2" in pane._row_widgets
    # year is missing, so empty string rendered
    assert pane._row_widgets["citation:c2"].value.text() == ""
    assert pane._row_widgets["citation:c2"].value._copy_text == ""

    pane.close()


def test_citation_rows_available_rows_and_hidden_and_custom_order(
    application: QApplication,
) -> None:
    """Citation rows appear in available_rows(), respect hidden rows and custom ordering."""
    pane = DetailPane()
    pane.set_citation_styles(
        [
            CitationStyle(id="s1", name="Style One", template="{bibcode}"),
            CitationStyle(id="s2", name="Style Two", template="{year}"),
        ]
    )
    avail = dict(pane.available_rows())
    assert avail.get("citation:s1") == "Style One"
    assert avail.get("citation:s2") == "Style Two"

    # Test custom row ordering and hidden rows
    pane.set_row_order(["citation:s2", "title", "citation:s1"])
    pane.set_hidden_rows(["citation:s1"])
    pane.set_record(
        {
            "current_path": "paper.pdf",
            "metadata": {"title": "Sample", "bibcode": "B1", "year": "2024"},
        },
        Path("/papers"),
    )

    keys = pane.row_keys()
    assert keys[0] == "citation:s2"
    assert keys[1] == "title"
    assert "citation:s1" not in keys
    assert ("citation:s1", "Style One") in pane.available_rows()
    pane.close()


def test_citation_rows_typography_and_no_copy_on_empty(
    application: QApplication,
) -> None:
    """Citation rows support per-row typography and empty copy label does not copy."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QColor, QMouseEvent

    pane = DetailPane()
    pane.set_citation_styles(
        [CitationStyle(id="style1", name="Style 1", template="{volume}")]
    )
    custom_font = QFont("Courier", 14)
    custom_color = QColor("#ff0000")
    pane.set_row_styles(
        {"citation:style1": custom_font}, {"citation:style1": custom_color}
    )

    pane.set_record(
        {
            "current_path": "paper.pdf",
            "metadata": {"title": "No volume"},
        },
        Path("/papers"),
    )

    row = pane._row_widgets["citation:style1"]
    assert row.value.font().family() == custom_font.family()
    assert custom_color.name() in row.value.styleSheet()

    # Empty value: text is empty, clicking shouldn't emit copied
    copied_signals = []
    row.copied.connect(copied_signals.append)
    event = QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease,
        QPointF(5, 5),
        QPointF(5, 5),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    row.value.mouseReleaseEvent(event)
    assert len(copied_signals) == 0
    pane.close()


def test_citation_rows_use_cached_document_and_survive_style_change(
    application: QApplication,
) -> None:
    """Cached metadata and metrics feed citation rendering and survive set_citation_styles."""
    pane = DetailPane()
    pane.set_citation_styles(
        [CitationStyle(id="c1", name="Cite", template="{year}: {title}")]
    )
    record = {
        "current_path": "paper.pdf",
        "metadata": {},  # Empty local metadata
    }
    cached_doc = {
        "year": "2023",
        "title": "Cached Title",
        "metrics": {"all_citations": 42},
    }
    pane.set_record(record, Path("/papers"), cached_document=cached_doc)

    # 1. Enriched metadata is used in citation rendering
    assert pane._row_widgets["citation:c1"].value._copy_text == "2023: Cached Title"
    assert "metric_all_citations" in pane.row_keys()
    assert pane._row_widgets["metric_all_citations"].value.text() == "42"

    # 2. Update citation styles: cached_document should survive
    pane.set_citation_styles(
        [CitationStyle(id="c2", name="NewCite", template="{title} ({year})")]
    )
    assert pane._row_widgets["citation:c2"].value._copy_text == "Cached Title (2023)"
    assert "metric_all_citations" in pane.row_keys()
    assert pane._row_widgets["metric_all_citations"].value.text() == "42"
    pane.close()


def test_detail_pane_narrow_width_wraps_values_and_keeps_buttons_in_bounds(
    application: QApplication,
) -> None:
    """Narrow widths wrap multi-line metadata without clipping values or lock buttons."""
    pane = DetailPane()
    pane.resize(280, 620)
    pane.show()
    pane.set_record(
        {
            "current_path": "paper.pdf",
            "size_bytes": 1024,
            "mtime_ns": 1_729_160_000_000_000_000,
            "sha256": "a" * 64,
            "status": "resolved",
            "metadata": {
                "bibcode": "2024A&A...680A...1B",
                "title": [
                    (
                        "High-precision polarimetry of small Solar System bodies "
                        "with SPHEREx and ground-based facilities"
                    )
                ],
                "author": [
                    "Bach, Yoonsoo P.",
                    "Ishiguro, Masateru",
                    "Kwon, Yuna G.",
                ],
                "doi": ["10.1051/0004-6361/202400001"],
                "abstract": "word " * 120,
            },
        },
        Path("/Users/ysbach/Dropbox/Studocs/PublOCR"),
    )
    application.processEvents()

    for key in ("title", "author", "doi", "full_path"):
        row = pane._row_widgets[key]
        assert row.value.height() >= row.value.heightForWidth(row.value.width())
        if row.lock_button.isVisible():
            assert row.lock_button.geometry().right() <= pane._rows.width()
    pane.close()


def test_main_window_applies_default_splitter_sizes_only_when_unset(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """First launch uses DEFAULT_SPLITTER_SIZES while saved splitter_sizes win."""
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr("adsfolio.gui.main_window.load_config", lambda: GuiConfig())
    monkeypatch.setattr("adsfolio.gui.main_window.save_config", lambda config: None)
    first_window = MainWindow()
    first_window.show()
    application.processEvents()
    first_sizes = first_window.splitter.sizes()
    assert first_sizes[1] > first_sizes[2] > first_sizes[0]
    first_window.close()

    custom_sizes = [310, 520, 450]
    monkeypatch.setattr(
        "adsfolio.gui.main_window.load_config",
        lambda: GuiConfig(splitter_sizes=list(custom_sizes)),
    )
    second_window = MainWindow()
    second_window.show()
    application.processEvents()
    second_sizes = second_window.splitter.sizes()
    assert second_sizes[0] > first_sizes[0]
    assert second_sizes[2] > first_sizes[2]
    assert second_sizes[1] < first_sizes[1]
    second_window.close()


def test_toolbar_exposes_identify_and_rename_actions(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The first-run workflow actions are in the toolbar and are the menu actions."""
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    window = MainWindow()
    toolbar = window.findChild(QToolBar, "search_toolbar")

    assert window.identify_action in toolbar.actions()
    assert window.rename_action in toolbar.actions()
    window.close()


@pytest.mark.parametrize(
    "raw_status,display_status",
    [("resolved", "ADS record linked"), ("unresolved", "No ADS link")],
)
def test_detail_status_display_preserves_metadata_and_edit_semantics(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    raw_status: str,
    display_status: str,
) -> None:
    """Show ADS verification labels without changing stored status or field editing."""
    pane = DetailPane()
    record = {
        "current_path": "paper.pdf",
        "status": raw_status,
        "metadata": {"title": ["Existing title"], "status": raw_status},
    }
    changes = []
    pane.metadata_changed.connect(changes.append)
    pane.set_record(record, Path("/papers"))
    row = pane._row_widgets["status"]
    assert row.value.text() == display_status
    assert row.value._copy_text == raw_status
    assert row.edit_button.isHidden()
    assert row.lock_button.isHidden()
    assert row.value.accessibleDescription()
    if raw_status == "unresolved":
        assert "Metadata is available" in row.value.toolTip()
        assert "Existing metadata is retained" in row.value.toolTip()
    monkeypatch.setattr(
        QInputDialog, "getMultiLineText", lambda *args: ("Edited title", True)
    )
    pane._edit_metadata("title")
    assert changes[-1]["title"] == ["Edited title"]
    assert changes[-1]["status"] == raw_status
    assert record["status"] == raw_status
    assert record["metadata"]["status"] == raw_status
    pane.close()


@pytest.mark.parametrize("status", ["pending", "error", None])
def test_detail_other_status_values_keep_existing_display(
    application: QApplication, status: str | None
) -> None:
    """Keep status values outside resolved/unresolved unchanged."""
    pane = DetailPane()
    pane.set_record({"current_path": "paper.pdf", "status": status}, Path("/papers"))
    assert pane._row_widgets["status"].value.text() == (status or "")
    pane.close()
