from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QItemSelectionModel, QObject, Qt, Signal
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from adsfolio.citations import CitationStyle
from adsfolio.gui.config import GuiConfig, RootEntry
from adsfolio.gui.dialogs import IdentifyDialog, RenameDialog, UserProfileDialog
from adsfolio.gui.main_window import MainWindow
from adsfolio.pdf.identify_settings import IdentifySettings, IdentifyStrategy
from adsfolio.pdf.registry import CentralPdfRegistryState
from adsfolio.pdf.rename_settings import RenameSettings
from adsfolio.user_profile import UserProfile


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_identify_dialog_defaults_global_and_keeps_profile_fields_out(application):
    dialog = IdentifyDialog(Path("/papers"), IdentifySettings())

    assert dialog.scope.currentData() == "global"
    assert not hasattr(dialog, "debut")
    assert not hasattr(dialog, "end")
    assert not dialog.refresh_cache.isChecked()
    assert not dialog.allow_ads.isChecked()
    assert not dialog.skip_unresolved.isChecked()
    assert not dialog.apply_moves.isChecked()
    dialog.close()


def test_identify_dialog_switches_scope_and_copies_global_for_new_library(
    application,
):
    global_settings = IdentifySettings(
        recursive=True,
        skip_unresolved_directory=True,
    )
    dialog = IdentifyDialog(Path("/papers"), global_settings)

    dialog.scope.setCurrentIndex(dialog.scope.findData("library"))

    assert dialog.recursive.isChecked()
    assert dialog.skip_unresolved.isChecked()
    dialog.close()


def test_identify_dialog_saves_selected_scope_without_accepting(application):
    calls = []
    dialog = IdentifyDialog(
        Path("/papers"),
        IdentifySettings(recursive=True),
        IdentifySettings(recursive=False),
        initial_scope="library",
        save_settings=lambda scope, settings: calls.append((scope, settings)),
    )

    dialog.save_defaults()

    assert calls == [("library", dialog.selected_settings())]
    assert dialog.result() != QDialog.Accepted


def test_identify_dialog_uses_just_saved_global_defaults_as_library_fallback(
    application,
):
    dialog = IdentifyDialog(Path("/papers"), IdentifySettings())
    dialog.recursive.setChecked(True)

    dialog.save_defaults()
    dialog.scope.setCurrentIndex(dialog.scope.findData("library"))

    assert dialog.recursive.isChecked()
    dialog.close()


def test_identify_dialog_cancel_does_not_save(application):
    calls = []
    dialog = IdentifyDialog(
        Path("/papers"),
        IdentifySettings(),
        save_settings=lambda scope, settings: calls.append((scope, settings)),
    )

    dialog.reject()

    assert calls == []


def test_identify_dialog_argv_uses_displayed_settings_and_one_run_actions(application):
    dialog = IdentifyDialog(Path("/papers"), IdentifySettings(recursive=True))
    dialog.refresh_cache.setChecked(True)
    dialog.allow_ads.setChecked(True)
    dialog.apply_moves.setChecked(True)

    argv = dialog.build_argv()

    scope_index = argv.index("--settings-scope")
    assert argv[scope_index : scope_index + 4] == [
        "--settings-scope",
        "global",
        "--recursive",
        "--strategy",
    ]
    assert argv.count("--strategy") == len(IdentifyStrategy)
    assert "--clear-output-unresolved" in argv
    assert "--include-unresolved-directory" in argv
    assert "--quiet" in argv
    assert argv[-3:] == ["--refresh-cache", "--allow-ads", "--apply"]
    dialog.close()


def test_identify_dialog_preserves_enabled_strategy_order(application):
    configured = (
        IdentifyStrategy.FIRST_PAGE_DOI,
        IdentifyStrategy.FILENAME_BIBCODE,
    )
    dialog = IdentifyDialog(Path("/papers"), IdentifySettings(strategies=configured))

    assert dialog.selected_strategies() == configured
    assert dialog.strategies.count() == len(IdentifyStrategy)
    dialog.strategies.setCurrentRow(1)
    dialog._move_strategy(-1)

    assert dialog.selected_strategies() == tuple(reversed(configured))
    dialog.close()


def test_identify_dialog_rejects_empty_strategy_policy(application):
    dialog = IdentifyDialog(Path("/papers"), IdentifySettings())
    for row in range(dialog.strategies.count()):
        dialog.strategies.item(row).setCheckState(Qt.Unchecked)

    with pytest.raises(ValueError, match="At least one"):
        dialog.selected_settings()

    dialog.close()


def test_identify_dialog_all_interactive_items_have_accessible_help(application):
    dialog = IdentifyDialog(Path("/papers"), IdentifySettings())
    controls = [
        dialog.scope,
        dialog.recursive,
        dialog.refresh_cache,
        dialog.allow_ads,
        dialog.strategies,
        dialog.move_strategy_up,
        dialog.move_strategy_down,
        dialog.reset_strategies,
        dialog.output,
        dialog.choose_output,
        dialog.skip_unresolved,
        dialog.apply_moves,
        dialog.verbose,
        dialog.verbose_parser,
        dialog.ai_csv,
        dialog.choose_ai_csv,
    ]

    for control in controls:
        assert control.toolTip()
        assert control.accessibleDescription() == control.toolTip()
    assert all(label.help_button.text() == "?" for label in dialog.help_labels.values())
    dialog.close()


def test_user_profile_dialog_loads_normalizes_and_saves_profile(application):
    calls = []
    dialog = UserProfileDialog(
        UserProfile(
            orcid="https://orcid.org/0000-0002-1694-233x",
            author_names=("Bach, Y. P.",),
            first_publication_year=2010,
            last_publication_year=2030,
            ads_library_ids=("LIB1",),
        ),
        save_profile=calls.append,
    )
    dialog.author_names.setPlainText(" Bach, Y. P. \n\nBach, Yoonsoo\nBach, Y. P.")
    dialog.library_ids.setPlainText("LIB1\n LIB2 \nLIB1")

    dialog.accept()

    assert calls == [
        UserProfile(
            orcid="0000-0002-1694-233X",
            author_names=("Bach, Y. P.", "Bach, Yoonsoo"),
            first_publication_year=2010,
            last_publication_year=2030,
            ads_library_ids=("LIB1", "LIB2"),
        )
    ]
    assert dialog.result() == QDialog.Accepted


def test_user_profile_dialog_rejects_invalid_orcid(application, monkeypatch):
    calls = []
    messages = []
    monkeypatch.setattr(
        "adsfolio.gui.dialogs.QMessageBox.critical",
        lambda parent, title, text: messages.append(text),
    )
    dialog = UserProfileDialog(UserProfile(), save_profile=calls.append)
    dialog.orcid.setText("0000-0002-1825-0098")

    dialog.accept()

    assert calls == []
    assert "check digit" in messages[0]
    assert dialog.result() != QDialog.Accepted


def test_user_profile_dialog_cancel_does_not_save(application):
    calls = []
    dialog = UserProfileDialog(UserProfile(), save_profile=calls.append)

    dialog.reject()

    assert calls == []


def test_rename_dialog_has_visible_help_and_complete_template_explanation(
    application,
):
    dialog = RenameDialog(Path("/papers"))
    controls = [
        dialog.scope,
        dialog.template,
        dialog.author_format,
        dialog.max_authors,
        dialog.authors_when_truncated,
        dialog.author_separator,
        dialog.author_truncation_suffix,
        dialog.char_policy,
        dialog.preview_button,
        dialog.save_defaults_button,
        dialog.apply_button,
    ]

    for control in controls:
        assert control.toolTip()
        assert control.accessibleDescription() == control.toolTip()
    explanation = dialog.template.toolTip()
    for field in (
        "{bibcode}",
        "{year}",
        "{title}",
        "{first_author}",
        "{first_author_full}",
        "{authors}",
        "{authors_N}",
        "{journal}",
        "{volume}",
        "{page}",
    ):
        assert field in explanation
    assert ".pdf is appended automatically" in explanation
    assert all(label.help_button.text() == "?" for label in dialog.help_labels.values())
    dialog.close()


def test_tools_profile_action_opens_with_global_profile(application, monkeypatch):
    profile = UserProfile(orcid="0000")
    captured = {}

    class _ProfileDialog:
        def __init__(self, loaded_profile, *, save_profile, parent):
            captured.update(
                profile=loaded_profile,
                save_profile=save_profile,
                parent=parent,
            )

        def exec(self):
            return QDialog.Rejected

    monkeypatch.setattr("adsfolio.gui.main_window.load_config", GuiConfig)
    monkeypatch.setattr("adsfolio.gui.main_window.save_config", lambda config: None)
    monkeypatch.setattr(
        "adsfolio.gui.main_window.Config.load_user_profile",
        lambda: profile,
    )
    monkeypatch.setattr("adsfolio.gui.main_window.UserProfileDialog", _ProfileDialog)
    window = MainWindow()

    window.profile_action.trigger()

    assert window.profile_action.text() == "My Publication Profile…"
    assert captured["profile"] == profile
    assert captured["parent"] is window
    window.close()


def test_tools_citation_styles_action_opens_with_global_styles_and_selected_record(
    application, monkeypatch
):
    styles = [CitationStyle(id="alpha", name="Compact", template="{bibcode}")]
    record = {
        "current_path": "paper.pdf",
        "metadata": {"bibcode": "2024ApJ...900..100S"},
    }
    captured = {}

    class _CitationStylesDialog(QObject):
        saved = Signal(object)

        def __init__(self, loaded_styles, preview_record=None, parent=None):
            super().__init__(parent)
            captured.update(
                styles=loaded_styles,
                preview_record=preview_record,
                parent=parent,
            )

        def exec(self):
            return QDialog.Rejected

    monkeypatch.setattr(
        "adsfolio.gui.main_window.load_config",
        lambda: GuiConfig(citation_styles=styles),
    )
    monkeypatch.setattr("adsfolio.gui.main_window.save_config", lambda config: None)
    monkeypatch.setattr(
        "adsfolio.gui.main_window.CitationStylesDialog",
        _CitationStylesDialog,
    )
    window = MainWindow()
    window.model._rows = [record]
    window.table.setCurrentIndex(window.model.index(0, 0))

    window.citation_styles_action.trigger()

    assert window.citation_styles_action.text() == "Custom Citation Styles…"
    assert captured == {
        "styles": styles,
        "preview_record": record,
        "parent": window,
    }
    window.close()


def test_accepting_citation_styles_replaces_runtime_columns_and_detail_once(
    application, monkeypatch
):
    old_styles = [
        CitationStyle(id="alpha", name="Old Alpha", template="{bibcode}"),
        CitationStyle(id="deleted", name="Deleted", template="{year}"),
    ]
    new_styles = [
        CitationStyle(id="alpha", name="Renamed Alpha", template="{year}"),
        CitationStyle(id="new", name="New Style", template="{title}"),
    ]
    config = GuiConfig(
        citation_styles=old_styles,
        column_widths={"citation:alpha": 211, "citation:deleted": 212},
        column_fonts={
            "citation:alpha": {"family": "Menlo", "point_size": 11},
            "citation:deleted": {"family": "Menlo", "point_size": 12},
        },
        table_column_order=[
            "title",
            "citation:deleted",
            "bibcode",
            "citation:alpha",
            "filename",
        ],
        hidden_table_columns=["citation:deleted", "citation:alpha"],
        detail_fonts={
            "citation:alpha": {"family": "Menlo", "point_size": 13},
            "citation:deleted": {"family": "Menlo", "point_size": 14},
        },
        detail_row_order=[
            "title",
            "citation:deleted",
            "bibcode",
            "citation:alpha",
            "filename",
        ],
        detail_hidden_rows=["citation:deleted", "citation:alpha"],
    )

    class _CitationStylesDialog(QObject):
        saved = Signal(object)

        def __init__(self, *_args, **_kwargs):
            super().__init__()

        def exec(self):
            self.saved.emit(tuple(new_styles))
            return QDialog.Accepted

    monkeypatch.setattr("adsfolio.gui.main_window.load_config", lambda: config)
    save_calls = []
    monkeypatch.setattr(
        "adsfolio.gui.main_window.save_config",
        lambda saved_config: save_calls.append(saved_config),
    )
    monkeypatch.setattr(
        "adsfolio.gui.main_window.CitationStylesDialog",
        _CitationStylesDialog,
    )
    window = MainWindow()
    model_calls = []
    detail_calls = []
    original_set_columns = window.model.set_columns
    original_set_citation_styles = window.detail.set_citation_styles

    def set_columns(columns):
        model_calls.append(tuple(column.key for column in columns))
        original_set_columns(columns)

    def set_citation_styles(styles):
        detail_calls.append(tuple(styles))
        original_set_citation_styles(styles)

    monkeypatch.setattr(window.model, "set_columns", set_columns)
    monkeypatch.setattr(window.detail, "set_citation_styles", set_citation_styles)

    window.citation_styles_action.trigger()

    assert config.citation_styles == new_styles
    assert save_calls == [config]
    assert save_calls[0].citation_styles == new_styles
    assert model_calls == [tuple(column.key for column in window.columns)]
    assert detail_calls == [tuple(new_styles)]
    header = window.table.header()
    visual_keys = [
        window.columns[header.logicalIndex(visual)].key
        for visual in range(header.count())
    ]
    assert visual_keys[visual_keys.index("bibcode") + 1] == "citation:new"
    assert config.column_widths == {"citation:alpha": 211}
    assert config.column_fonts == {
        "citation:alpha": {"family": "Menlo", "point_size": 11}
    }
    assert config.hidden_table_columns == ["citation:alpha"]
    assert config.detail_fonts == {
        "citation:alpha": {"family": "Menlo", "point_size": 13}
    }
    assert config.detail_hidden_rows == ["citation:alpha"]
    assert config.table_column_order == [
        "title",
        "bibcode",
        "citation:new",
        "citation:alpha",
        "filename",
    ]
    assert config.detail_row_order == [
        "title",
        "bibcode",
        "citation:new",
        "citation:alpha",
        "filename",
    ]
    new_column = next(
        index
        for index, column in enumerate(window.columns)
        if column.key == "citation:new"
    )
    assert not window.table.isColumnHidden(new_column)
    window.close()


def test_reordering_citation_styles_reorders_table_and_detail_keys(
    application, monkeypatch
):
    old_styles = [
        CitationStyle(id="alpha", name="Alpha", template="{bibcode}"),
        CitationStyle(id="beta", name="Beta", template="{year}"),
    ]
    new_styles = [old_styles[1], old_styles[0]]
    config = GuiConfig(
        citation_styles=old_styles,
        table_column_order=[
            "title",
            "bibcode",
            "citation:alpha",
            "citation:beta",
            "filename",
        ],
        detail_row_order=[
            "title",
            "bibcode",
            "citation:alpha",
            "citation:beta",
            "filename",
        ],
    )

    class _CitationStylesDialog(QObject):
        saved = Signal(object)

        def __init__(self, *_args, **_kwargs):
            super().__init__()

        def exec(self):
            self.saved.emit(tuple(new_styles))
            return QDialog.Accepted

    monkeypatch.setattr("adsfolio.gui.main_window.load_config", lambda: config)
    monkeypatch.setattr("adsfolio.gui.main_window.save_config", lambda config: None)
    monkeypatch.setattr(
        "adsfolio.gui.main_window.CitationStylesDialog",
        _CitationStylesDialog,
    )
    window = MainWindow()

    window.citation_styles_action.trigger()

    expected_order = [
        "title",
        "bibcode",
        "citation:beta",
        "citation:alpha",
        "filename",
    ]
    assert config.table_column_order == expected_order
    assert config.detail_row_order == expected_order
    window.close()


def test_accepting_citation_styles_restores_selected_table_record(
    application, monkeypatch
):
    old_style = CitationStyle(id="alpha", name="Alpha", template="{bibcode}")
    new_style = CitationStyle(id="alpha", name="Renamed", template="{year}")
    config = GuiConfig(citation_styles=[old_style])
    records = [
        {"location_id": 1, "current_path": "one.pdf", "metadata": {}},
        {"location_id": 2, "current_path": "two.pdf", "metadata": {}},
    ]

    class _CitationStylesDialog(QObject):
        saved = Signal(object)

        def __init__(self, *_args, **_kwargs):
            super().__init__()

        def exec(self):
            self.saved.emit((new_style,))
            return QDialog.Accepted

    monkeypatch.setattr("adsfolio.gui.main_window.load_config", lambda: config)
    monkeypatch.setattr("adsfolio.gui.main_window.save_config", lambda config: None)
    monkeypatch.setattr(
        "adsfolio.gui.main_window.CitationStylesDialog",
        _CitationStylesDialog,
    )
    window = MainWindow()
    window.model._rows = records
    window.table.setCurrentIndex(window.model.index(1, 0))
    assert window.table.currentIndex().row() == 1

    window.citation_styles_action.trigger()

    assert window.table.currentIndex().isValid()
    assert window.table.currentIndex().row() == 1
    assert window._selected_record() is records[1]
    assert window.detail._record is records[1]
    window.close()


@pytest.mark.parametrize("selection_count", [0, 2])
def test_citation_styles_preview_uses_builtin_for_non_single_selection(
    application, monkeypatch, selection_count
):
    styles = [CitationStyle(id="alpha", name="Alpha", template="{bibcode}")]
    config = GuiConfig(citation_styles=styles)
    captured = {}

    class _CitationStylesDialog(QObject):
        saved = Signal(object)

        def __init__(self, _styles, preview_record=None, parent=None):
            super().__init__(parent)
            captured["preview_record"] = preview_record

        def exec(self):
            return QDialog.Rejected

    monkeypatch.setattr("adsfolio.gui.main_window.load_config", lambda: config)
    monkeypatch.setattr("adsfolio.gui.main_window.save_config", lambda config: None)
    monkeypatch.setattr(
        "adsfolio.gui.main_window.CitationStylesDialog",
        _CitationStylesDialog,
    )
    window = MainWindow()
    window.model._rows = [
        {"current_path": "one.pdf", "metadata": {}},
        {"current_path": "two.pdf", "metadata": {}},
    ]
    if selection_count == 2:
        window.table.setCurrentIndex(window.model.index(0, 0))
        window.table.selectionModel().select(
            window.model.index(1, 0),
            QItemSelectionModel.SelectionFlag.Select
            | QItemSelectionModel.SelectionFlag.Rows,
        )
        assert len(window.table.selectionModel().selectedRows()) == 2
    else:
        window.table.selectionModel().clearSelection()
        window.table.selectionModel().clearCurrentIndex()

    window.citation_styles_action.trigger()

    assert captured["preview_record"] is None
    window.close()


def test_rename_dialog_switches_scope_and_saves_before_apply(application, monkeypatch):
    calls = []
    dialog = RenameDialog(
        Path("/papers"),
        RenameSettings(template="{bibcode}"),
        RenameSettings(template="{title}", max_authors=6),
        initial_scope="library",
        save_settings=lambda scope, settings: calls.append((scope, settings)),
    )
    dialog.accept()
    assert calls == [("library", dialog.selected_settings())]
    assert dialog.result() == QDialog.Accepted


def test_rename_dialog_cancel_does_not_save(application):
    calls = []
    dialog = RenameDialog(
        Path("/papers"),
        RenameSettings(),
        save_settings=lambda scope, settings: calls.append((scope, settings)),
    )
    dialog.reject()
    assert calls == []


def test_rename_dialog_save_defaults_does_not_start_rename_job(application):
    events = []
    dialog = RenameDialog(
        Path("/papers"),
        RenameSettings(
            max_authors=2,
            author_separator="+",
            author_truncation_suffix="+",
        ),
        save_settings=lambda scope, settings: events.append(("save", scope, settings)),
    )
    dialog.job_started.connect(lambda job: events.append(("job", job)))

    dialog.save_defaults_button.click()

    assert events == [("save", "global", dialog.selected_settings())]
    assert dialog.result() == QDialog.Accepted
    dialog.close()


def test_rename_dialog_switches_scope_and_copies_global_for_new_library(
    application,
):
    global_settings = RenameSettings(
        template="{title}",
        author_format="last_comma_first",
        max_authors=5,
        char_policy="ascii",
    )
    dialog = RenameDialog(Path("/papers"), global_settings)

    dialog.scope.setCurrentIndex(dialog.scope.findData("library"))

    assert dialog.template.text() == "{title}"
    assert dialog.author_format.currentText() == "last_comma_first"
    assert dialog.max_authors.value() == 5
    assert dialog.char_policy.currentText() == "ascii"
    dialog.close()


def test_rename_dialog_save_failure_displays_error_and_does_not_close_or_start_job(
    application, monkeypatch
):
    def failing_save(scope, settings):
        raise OSError("Permission denied")

    dialog = RenameDialog(
        Path("/papers"),
        RenameSettings(),
        save_settings=failing_save,
    )
    critical_messages = []
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        lambda parent, title, message: critical_messages.append((title, message)),
    )

    dialog.accept()
    assert len(critical_messages) == 1
    assert "Permission denied" in critical_messages[0][1]
    assert dialog.result() == 0
    dialog.close()


def test_rename_dialog_apply_saves_settings_before_starting_job(
    application, monkeypatch
):
    events = []
    dialog = RenameDialog(
        Path("/papers"),
        RenameSettings(template="{bibcode}"),
        save_settings=lambda scope, settings: events.append(("save", scope, settings)),
    )
    dialog.table.setRowCount(1)
    dialog.job_started.connect(lambda job: events.append(("emit", job)))

    monkeypatch.setattr(
        "adsfolio.gui.dialogs.CommandJob.start",
        lambda self: events.append(("start", self)),
    )

    dialog.apply()

    assert events[0] == ("save", "global", dialog.selected_settings())
    assert [event[0] for event in events] == ["save", "start", "emit"]
    assert dialog.result() == QDialog.Accepted
    dialog.close()


def test_rename_dialog_apply_save_failure_does_not_start_job_or_close(
    application, monkeypatch
):
    events = []

    def failing_save(scope, settings):
        events.append(("save", scope, settings))
        raise ValueError("Invalid defaults")

    dialog = RenameDialog(
        Path("/papers"),
        RenameSettings(),
        save_settings=failing_save,
    )
    dialog.table.setRowCount(1)
    dialog.job_started.connect(lambda job: events.append(("emit", job)))
    monkeypatch.setattr(
        "adsfolio.gui.dialogs.CommandJob.start",
        lambda self: events.append(("start", self)),
    )
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        lambda parent, title, message: events.append(("error", title, message)),
    )

    dialog.apply()

    assert [event[0] for event in events] == ["save", "error"]
    assert "Invalid defaults" in events[1][2]
    assert dialog.result() == 0
    dialog.close()


def test_rename_dialog_preview_never_saves(application, monkeypatch):
    calls = []
    observed = {}
    dialog = RenameDialog(
        Path("/papers"),
        RenameSettings(
            author_separator="~",
            author_truncation_suffix="++",
            authors_when_truncated=2,
        ),
        save_settings=lambda scope, settings: calls.append((scope, settings)),
    )
    monkeypatch.setattr(
        "adsfolio.gui.dialogs.open_registry_state",
        lambda path: type("FakeState", (), {"close": lambda self: None})(),
    )
    monkeypatch.setattr(
        "adsfolio.gui.dialogs.PdfRenamer.batch_rename",
        lambda *args, **kwargs: observed.update(kwargs) or [("old.pdf", "new.pdf")],
    )

    dialog.preview()

    assert calls == []
    assert dialog.table.rowCount() == 1
    assert observed["author_separator"] == "~"
    assert observed["authors_when_truncated"] == 2
    assert observed["author_truncation_suffix"] == "++"
    dialog.close()


def test_rename_dialog_char_policy_order_and_defaults(application):
    dialog = RenameDialog(Path("/papers"))
    assert [
        dialog.char_policy.itemText(i) for i in range(dialog.char_policy.count())
    ] == ["keep", "ascii"]
    assert dialog.char_policy.currentText() == "keep"
    dialog.close()


def test_rename_dialog_authors_when_truncated_maximum_syncs_with_max_authors(
    application,
):
    dialog = RenameDialog(
        Path("/papers"),
        global_settings=RenameSettings(max_authors=1, authors_when_truncated=1),
        library_settings=RenameSettings(max_authors=4, authors_when_truncated=2),
    )
    assert dialog.max_authors.value() == 1
    assert dialog.authors_when_truncated.maximum() == 1

    dialog.scope.setCurrentIndex(dialog.scope.findData("library"))
    assert dialog.max_authors.value() == 4
    assert dialog.authors_when_truncated.maximum() == 4

    dialog.scope.setCurrentIndex(dialog.scope.findData("global"))
    assert dialog.max_authors.value() == 1
    assert dialog.authors_when_truncated.maximum() == 1
    dialog.close()


def test_rename_dialog_saves_and_forwards_author_join_settings(application):
    dialog = RenameDialog(
        Path("/papers"),
        RenameSettings(
            max_authors=2,
            authors_when_truncated=2,
            author_separator="~",
            author_truncation_suffix="++",
        ),
    )

    settings = dialog.selected_settings()
    argv = dialog.build_argv()

    assert settings.author_separator == "~"
    assert settings.authors_when_truncated == 2
    assert settings.author_truncation_suffix == "++"
    assert argv[argv.index("--authors-when-truncated") + 1] == "2"
    assert argv[argv.index("--author-separator") + 1] == "~"
    assert argv[argv.index("--author-truncation-suffix") + 1] == "++"
    dialog.close()


def test_main_window_passes_independent_rename_scope_settings(
    application, monkeypatch, tmp_path
):
    import adsfolio.gui.main_window as main_window_module

    root = tmp_path / "library"
    root.mkdir()
    global_settings = RenameSettings(template="{bibcode}")
    library_settings = RenameSettings(template="{title}")
    captured = {}

    class _RenameDialog(QObject):
        job_started = Signal(object)

        def __init__(self, *args, **kwargs):
            super().__init__()
            captured.update(args=args, kwargs=kwargs)

        def exec(self):
            return QDialog.Rejected

    monkeypatch.setattr(
        main_window_module,
        "load_config",
        lambda: GuiConfig(
            roots=[RootEntry(str(root))],
            rename_scopes={str(root.resolve()): "library"},
        ),
    )
    monkeypatch.setattr(main_window_module, "save_config", lambda config: None)
    monkeypatch.setattr(main_window_module, "RenameDialog", _RenameDialog)
    monkeypatch.setattr(
        main_window_module.Config,
        "load_rename_settings",
        classmethod(lambda cls: global_settings),
    )
    monkeypatch.setattr(
        main_window_module.Config,
        "get_config_dir",
        classmethod(lambda cls: tmp_path / "config"),
    )
    window = MainWindow()
    CentralPdfRegistryState(window.registries[str(root.resolve())]).set_rename_settings(
        library_settings
    )

    window._rename()

    assert captured["args"][:3] == (
        root.resolve(),
        global_settings,
        library_settings,
    )
    assert captured["kwargs"]["initial_scope"] == "library"
    window.close()
