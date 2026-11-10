"""Form rows with an explanation get a "?" button that shows it on a click."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QWidget,
)

from adsfolio.gui.form_help import HELP_ROW_NAME, FieldHelpButton, add_form_help


def _form(qtbot):
    widget = QWidget()
    qtbot.addWidget(widget)
    form = QFormLayout(widget)
    edit = QLineEdit(widget)
    edit.setToolTip("Type a template")
    form.addRow("Template:", edit)
    box = QCheckBox("Watch new PDFs", widget)
    box.setToolTip("Watch the folder for new PDFs")
    form.addRow(box)
    plain = QLineEdit(widget)
    form.addRow("Name:", plain)
    note = QLabel("A note without a control", widget)
    note.setToolTip("Only text")
    form.addRow(note)
    return widget, form, edit, box


def _buttons(widget) -> list[FieldHelpButton]:
    return widget.findChildren(FieldHelpButton)


def test_rows_with_explained_controls_get_one_button_each(qtbot):
    widget, form, edit, box = _form(qtbot)

    add_form_help(widget)
    add_form_help(widget)

    buttons = _buttons(widget)
    assert sorted(button.accessibleName() for button in buttons) == [
        "Help for Template",
        "Help for Watch new PDFs",
    ]
    label_cell = form.itemAt(0, QFormLayout.ItemRole.LabelRole).widget()
    assert label_cell.objectName() == HELP_ROW_NAME
    assert form.labelForField(edit) is label_cell
    span_cell = form.itemAt(1, QFormLayout.ItemRole.SpanningRole).layout()
    assert span_cell.indexOf(box) == 0
    assert box.parent() is widget


def test_click_shows_the_current_explanation(qtbot, monkeypatch):
    widget, _form_layout, edit, _box = _form(qtbot)
    add_form_help(widget)
    shown = []
    monkeypatch.setattr(
        "adsfolio.gui.form_help.QWhatsThis.showText",
        lambda _point, text, _widget: shown.append(text),
    )
    button = next(
        b for b in _buttons(widget) if b.accessibleName().endswith("Template")
    )

    edit.setToolTip("Type a template, for example {year}")
    button.click()

    assert shown == ["Type a template, for example {year}"]
    assert button.toolTip() == "Type a template, for example {year}"


def test_hidden_row_stays_hidden_until_the_form_shows_it(qtbot):
    widget, form, edit, _box = _form(qtbot)
    form.setRowVisible(edit, False)

    add_form_help(widget)
    label_cell = form.itemAt(0, QFormLayout.ItemRole.LabelRole).widget()
    assert label_cell.isHidden()

    form.setRowVisible(edit, True)

    assert not label_cell.isHidden()
    button = label_cell.findChild(FieldHelpButton)
    assert not button.isHidden()


def test_row_with_an_empty_label_gets_the_button_in_the_label_column(qtbot):
    widget = QWidget()
    qtbot.addWidget(widget)
    form = QFormLayout(widget)
    box = QCheckBox("Match by ORCID iD", widget)
    box.setToolTip("Find papers by ORCID iD")
    form.addRow("", box)

    add_form_help(widget)
    add_form_help(widget)

    cell = form.itemAt(0, QFormLayout.ItemRole.LabelRole).widget()
    assert cell.objectName() == HELP_ROW_NAME
    assert [b.accessibleName() for b in _buttons(widget)] == [
        "Help for Match by ORCID iD"
    ]
    assert form.itemAt(0, QFormLayout.ItemRole.FieldRole).widget() is box
