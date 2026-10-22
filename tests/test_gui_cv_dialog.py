"""CV Generator window: defaults, live preview, settings, and ADS runs."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

from adsfolio.config import Config
from adsfolio.gui.cv_dialog import CVDialog
from adsfolio.user_profile import UserProfile


class FakeJob(QObject):
    output = Signal(str)
    failed = Signal(str)
    finished = Signal(int)
    created: list[FakeJob] = []

    def __init__(self, argv, parent=None, **kwargs) -> None:
        super().__init__(parent)
        self.argv = argv
        self.started = False
        self.cancelled = False
        FakeJob.created.append(self)

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True


@pytest.fixture(autouse=True)
def no_token(monkeypatch):
    monkeypatch.delenv(Config.ENV_VAR, raising=False)
    monkeypatch.setattr(Config, "get_token_paths", classmethod(lambda cls: []))
    FakeJob.created = []


def make_dialog(qtbot, tmp_path: Path, **kwargs) -> CVDialog:
    dialog = CVDialog(settings_path=tmp_path / "cv.yaml", **kwargs)
    qtbot.addWidget(dialog)
    return dialog


def test_defaults_come_from_the_publication_profile(qtbot, tmp_path):
    Config.save_user_profile(UserProfile(author_names=("Doe, Jane", "Doe, J.")))
    dialog = make_dialog(qtbot, tmp_path)
    assert dialog.query.text() == '(author:"Doe, Jane" OR author:"Doe, J.")'
    assert not dialog.use_orcid.isChecked()
    assert dialog.me_target.text() == "Doe, J."
    assert dialog.me_display.text() == "Jane Doe"
    assert dialog.first_author_prefix.text() == "doe, j"


def test_orcid_alone_selects_publications_like_the_cli(qtbot, tmp_path):
    Config.save_user_profile(
        UserProfile(orcid="0000-0002-1825-0097", author_names=("Doe, Jane",))
    )
    dialog = make_dialog(qtbot, tmp_path)
    assert dialog.query.text() == "*:*"
    assert dialog.use_orcid.isChecked()


def test_sample_preview_follows_template_edits_offline(qtbot, tmp_path):
    Config.save_user_profile(UserProfile(author_names=("Doe, Jane",)))
    dialog = make_dialog(qtbot, tmp_path)
    dialog.format_choice.setCurrentIndex(dialog.format_choice.count() - 1)
    dialog.preset.setCurrentIndex(dialog.preset.findData("html"))
    dialog._apply_preset()
    dialog._render_sample()
    assert "<b>Doe, J.</b>" in dialog.preview.toPlainText()
    dialog.parts["entry"].setPlainText("ENTRY {year} {title}")
    qtbot.waitUntil(lambda: "ENTRY 2025 Dust" in dialog.preview.toPlainText())
    assert dialog.escape.currentData() == "html"
    assert dialog.output_path.text().endswith("publications.html")


def test_builtin_formats_switch_the_default_name_highlight(qtbot, tmp_path):
    dialog = make_dialog(qtbot, tmp_path)
    markdown = next(
        i
        for i in range(dialog.format_choice.count())
        if dialog.format_choice.itemData(i)[0] == "markdown"
    )
    dialog.format_choice.setCurrentIndex(markdown)
    assert dialog.me_fmt.text() == "**{}**"
    assert not dialog.template_box.isEnabled()
    dialog.me_fmt.setText("__{}__")
    dialog.format_choice.setCurrentIndex(0)
    assert dialog.me_fmt.text() == "__{}__"


def test_settings_round_trip_and_keep_sections_the_window_does_not_edit(
    qtbot, tmp_path
):
    path = tmp_path / "cv.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "author": {"query": 'author:"Roe, A."', "me_target": "Roe, A."},
                "format": {"output": "template"},
                "template": {"preset": "text", "entry": "* {title}"},
                "markers": {"best": {"symbol": "+", "bibcodes": ["X"]}},
                "status": {"X": "accepted"},
            }
        ),
        encoding="utf-8",
    )
    dialog = make_dialog(qtbot, tmp_path)
    assert dialog.parts["entry"].toPlainText() == "* {title}"
    dialog.max_authors.setValue(9)
    dialog.close_button.click()
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert saved["markers"] == {"best": {"symbol": "+", "bibcodes": ["X"]}}
    assert saved["status"] == {"X": "accepted"}
    assert saved["format"]["n_max_authors"] == 9
    assert saved["template"]["entry"] == "* {title}"
    reopened = make_dialog(qtbot, tmp_path)
    assert reopened.max_authors.value() == 9


def test_ads_lookup_needs_a_token(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr("adsfolio.gui.cv_dialog.CommandJob", FakeJob)
    dialog = make_dialog(qtbot, tmp_path)
    dialog.query.setText('author:"Doe, J."')
    dialog.save_button.click()
    assert FakeJob.created == []
    assert "token" in dialog.status.text()


def test_save_cv_runs_the_cli_with_the_settings_file(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv(Config.ENV_VAR, "token")
    monkeypatch.setattr("adsfolio.gui.cv_dialog.CommandJob", FakeJob)
    dialog = make_dialog(qtbot, tmp_path)
    dialog.query.setText('author:"Doe, J."')
    target = tmp_path / "out" / "cv.tex"
    dialog.output_path.setText(str(target))
    dialog.save_button.click()
    job = FakeJob.created[-1]
    assert job.started
    assert job.argv[-6:] == [
        "cv",
        "generate",
        "--config",
        str(tmp_path / "cv.yaml"),
        "--output",
        str(target),
    ]
    assert (tmp_path / "cv.yaml").is_file()
    assert not dialog.save_button.isEnabled()
    job.finished.emit(0)
    assert dialog.save_button.isEnabled()
    assert str(target) in dialog.status.text()


def test_ads_preview_reads_the_generated_file(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv(Config.ENV_VAR, "token")
    monkeypatch.setattr("adsfolio.gui.cv_dialog.CommandJob", FakeJob)
    dialog = make_dialog(qtbot, tmp_path)
    dialog.query.setText('author:"Doe, J."')
    dialog.refresh_ads.click()
    job = FakeJob.created[-1]
    Path(job.argv[-1]).write_text("REAL ADS OUTPUT\n", encoding="utf-8")
    job.output.emit("WARNING: a log line that must not reach the preview")
    job.finished.emit(0)
    assert dialog.ads_mode.isChecked()
    assert dialog.preview.toPlainText() == "REAL ADS OUTPUT\n"


def test_failed_generation_reports_the_last_output_line(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv(Config.ENV_VAR, "token")
    monkeypatch.setattr("adsfolio.gui.cv_dialog.CommandJob", FakeJob)
    dialog = make_dialog(qtbot, tmp_path)
    dialog.query.setText('author:"Doe, J."')
    dialog.save_button.click()
    job = FakeJob.created[-1]
    job.output.emit("Error: ADS rejected the token")
    job.finished.emit(1)
    assert "ADS rejected the token" in dialog.status.text()


def test_tools_menu_opens_the_cv_generator(qtbot, monkeypatch):
    from adsfolio.gui.main_window import MainWindow

    opened = []
    monkeypatch.setattr(
        "adsfolio.gui.main_window.CVDialog",
        lambda parent: type("D", (), {"exec": lambda self: opened.append(True)})(),
    )
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.cv_action.text() == "CV Generator…"
    window.cv_action.trigger()
    assert opened == [True]


def _interactive_widgets(dialog: CVDialog):
    from PySide6.QtWidgets import (
        QAbstractSpinBox,
        QCheckBox,
        QComboBox,
        QLineEdit,
        QPlainTextEdit,
        QPushButton,
        QRadioButton,
    )

    kinds = (
        QLineEdit,
        QAbstractSpinBox,
        QComboBox,
        QCheckBox,
        QPushButton,
        QRadioButton,
        QPlainTextEdit,
    )
    for widget in dialog.findChildren(QWidget):
        if not isinstance(widget, kinds) or widget is dialog.preview:
            continue
        parent = widget.parentWidget()
        if isinstance(parent, (QAbstractSpinBox, QComboBox)):
            continue  # the editor inside a spin box or combo box
        yield widget


@pytest.mark.parametrize("local", [False, True])
def test_every_control_and_its_label_explains_itself(qtbot, tmp_path, local):
    from PySide6.QtWidgets import QFormLayout

    records = [{"metadata": {"title": ["T"], "author": ["Doe, J."]}}]
    dialog = make_dialog(qtbot, tmp_path, records=records if local else None)
    missing = [
        f"{type(widget).__name__}:{getattr(widget, 'text', lambda: '')()}"
        for widget in _interactive_widgets(dialog)
        if not widget.toolTip().strip()
    ]
    assert missing == []
    for form in dialog.findChildren(QFormLayout):
        for row in range(form.rowCount()):
            label = form.itemAt(row, QFormLayout.LabelRole)
            if label is not None and label.widget() is not None:
                text = label.widget().text()
                if text:
                    assert label.widget().toolTip().strip(), text
    for index in range(dialog.tabs.count()):
        assert dialog.tabs.tabToolTip(index)


def test_preview_renders_html_and_markdown_and_keeps_source(qtbot, tmp_path):
    Config.save_user_profile(UserProfile(author_names=("Doe, Jane",)))
    dialog = make_dialog(qtbot, tmp_path)
    dialog.format_choice.setCurrentIndex(dialog.format_choice.count() - 1)
    dialog.preset.setCurrentIndex(dialog.preset.findData("html"))
    dialog._apply_preset()
    dialog._render_sample()
    assert dialog.render_mode.currentData() == "html"
    assert dialog.preview_stack.currentWidget() is dialog.rendered
    assert "<b>Doe, J.</b>" in dialog.preview.toPlainText()
    rendered = dialog.rendered.toPlainText()
    assert "Doe, J." in rendered and "<b>" not in rendered
    dialog.render_mode.setCurrentIndex(dialog.render_mode.findData("source"))
    assert dialog.preview_stack.currentWidget() is dialog.preview
    dialog.preset.setCurrentIndex(dialog.preset.findData("markdown"))
    dialog._apply_preset()
    dialog._render_sample()
    assert dialog.render_mode.currentData() == "markdown"
    assert "**" not in dialog.rendered.toPlainText()


def test_selected_papers_export_offline_in_their_order(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr("adsfolio.gui.cv_dialog.CommandJob", FakeJob)
    (tmp_path / "cv.yaml").write_text(
        yaml.safe_dump(
            {
                "author": {"query": "q", "me_target": "Doe, J."},
                "format": {"output": "template", "me_fmt": "[{}]"},
                "template": {"entry": "{index}. {authors}: {title} ({venue})"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    records = [
        {
            "relative_path": "b.pdf",
            "metadata": {
                "title": ["Second"],
                "author": ["Roe, A.", "Doe, Jane"],
                "pub": "Local Journal",
                "volume": "2",
                "page": ["9"],
            },
        },
        {
            "relative_path": "a.pdf",
            "bibcode": "2020DEMO....1....1D",
            "metadata": {
                "title": ["First"],
                "author": ["Doe, Jane"],
                "bibstem": ["ApJ"],
            },
        },
    ]
    dialog = CVDialog(records=records)
    qtbot.addWidget(dialog)
    assert dialog.windowTitle() == "Export Selected Papers"
    assert not dialog.format_choice.isEnabled()
    assert dialog.ads_mode.isHidden() and dialog.refresh_ads.isHidden()
    assert not dialog.group.isChecked()
    dialog._render_sample()
    assert dialog.preview.toPlainText().splitlines()[-2:] == [
        "1. Roe, A. and [Doe, J.]: Second (Local Journal 2, 9)",
        "2. [Doe, J.]: First (The Astrophysical Journal)",
    ]
    target = tmp_path / "out" / "list.txt"
    dialog.output_path.setText(str(target))
    dialog.save_button.click()
    assert FakeJob.created == []
    assert "2. [Doe, J.]: First" in target.read_text(encoding="utf-8")
    assert (tmp_path / "cv-selection.yaml").is_file()
    cv_yaml = yaml.safe_load((tmp_path / "cv.yaml").read_text(encoding="utf-8"))
    assert "group" not in cv_yaml["template"]


def test_first_selection_export_without_cv_settings_uses_markdown_without_header(
    qtbot, tmp_path, monkeypatch
):
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    records = [{"metadata": {"title": ["Only"], "author": ["Roe, A."], "year": "2020"}}]
    dialog = CVDialog(records=records)
    qtbot.addWidget(dialog)
    dialog._render_sample()
    assert dialog.parts["header"].toPlainText() == ""
    assert dialog.parts["entry"].toPlainText()
    assert dialog.escape.currentData() == "none"
    assert dialog.render_mode.currentData() == "markdown"
    text = dialog.preview.toPlainText()
    assert "Only" in text and "NASA ADS" not in text
    assert dialog.output_path.text().endswith("selected-papers.md")


def test_selection_export_from_a_builtin_cv_style_starts_from_markdown(
    qtbot, tmp_path, monkeypatch
):
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    (tmp_path / "cv.yaml").write_text(
        yaml.safe_dump(
            {
                "author": {"query": "q"},
                "format": {"output": "latex", "style": "detailed"},
            }
        ),
        encoding="utf-8",
    )
    dialog = CVDialog(records=[{"metadata": {"title": ["X"], "author": ["A, B."]}}])
    qtbot.addWidget(dialog)
    assert dialog.me_fmt.text() == "**{}**"
    assert dialog.parts["header"].toPlainText() == ""
    assert "{title}" in dialog.parts["entry"].toPlainText()
