from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from adsapi.gui.config import GuiConfig, RootEntry
from adsapi.gui.dialogs import IdentifyDialog, RenameDialog, UserProfileDialog
from adsapi.gui.main_window import MainWindow
from adsapi.pdf.identify_settings import IdentifySettings, IdentifyStrategy
from adsapi.pdf.registry import CentralPdfRegistryState
from adsapi.pdf.rename_settings import RenameSettings
from adsapi.user_profile import UserProfile


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
            orcid="0000",
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
            orcid="0000",
            author_names=("Bach, Y. P.", "Bach, Yoonsoo"),
            first_publication_year=2010,
            last_publication_year=2030,
            ads_library_ids=("LIB1", "LIB2"),
        )
    ]
    assert dialog.result() == QDialog.Accepted


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

    monkeypatch.setattr("adsapi.gui.main_window.load_config", GuiConfig)
    monkeypatch.setattr("adsapi.gui.main_window.save_config", lambda config: None)
    monkeypatch.setattr(
        "adsapi.gui.main_window.Config.load_user_profile",
        lambda: profile,
    )
    monkeypatch.setattr("adsapi.gui.main_window.UserProfileDialog", _ProfileDialog)
    window = MainWindow()

    window.profile_action.trigger()

    assert window.profile_action.text() == "My Publication Profile…"
    assert captured["profile"] == profile
    assert captured["parent"] is window
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
        "adsapi.gui.dialogs.CommandJob.start",
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
        "adsapi.gui.dialogs.CommandJob.start",
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
        "adsapi.gui.dialogs.open_registry_state",
        lambda path: type("FakeState", (), {"close": lambda self: None})(),
    )
    monkeypatch.setattr(
        "adsapi.gui.dialogs.PdfRenamer.batch_rename",
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
    import adsapi.gui.main_window as main_window_module

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
