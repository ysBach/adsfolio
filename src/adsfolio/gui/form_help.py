"""Form layout fixes and click-to-open help for form rows."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractSlider,
    QAbstractSpinBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QToolButton,
    QWhatsThis,
    QWidget,
)

# Controls that a "?" button can explain. Text labels explain themselves.
_CONTROLS = (
    QAbstractButton,
    QAbstractSlider,
    QAbstractSpinBox,
    QComboBox,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
)
# Object name of the widgets that `add_form_help` puts into a form.
HELP_ROW_NAME = "form_help_row"


def grow_form_fields(widget: QWidget) -> None:
    """Let text fields and wrapped labels in forms use the full row width.

    The macOS style keeps form fields at their size hint. A text field is then
    too narrow, and a wrapped label gets too little height, so Qt clips its
    last lines. Such forms get the rule that grows fields with an expanding
    size policy. Other styles, such as Fusion, already grow fields at least
    that much, so their forms do not change.

    Parameters
    ----------
    widget : `QWidget`
        Widget that contains the forms.
    """
    policy = QFormLayout.FieldGrowthPolicy
    for form in widget.findChildren(QFormLayout):
        if form.fieldGrowthPolicy() == policy.FieldsStayAtSizeHint:
            form.setFieldGrowthPolicy(policy.ExpandingFieldsGrow)


class FieldHelpButton(QToolButton):
    """Show the tooltips of form controls when the user clicks "?".

    The button reads the tooltips at each click, so a tooltip that changes
    later still shows its current text. The button hides and shows with the
    first control, so it never stays alone in a hidden row.

    Parameters
    ----------
    controls : sequence of `QWidget`
        Controls that the button explains, in display order.
    title : `str`
        Row name for the accessible name, or ``""``.
    parent : `QWidget` or `None`, optional
        Parent widget.
    """

    def __init__(
        self,
        controls: list[QWidget],
        title: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controls = controls
        self.setText("?")
        self.setAutoRaise(True)
        self.setAccessibleName(f"Help for {title}" if title else "Help")
        self.clicked.connect(self.show_help)
        self._sync_text()
        for control in controls:
            control.installEventFilter(self)

    def explanation(self) -> str:
        """Return the current tooltips of the controls, one per line."""
        texts = (control.toolTip().strip() for control in self._controls)
        return "\n".join(dict.fromkeys(text for text in texts if text))

    def show_help(self) -> None:
        """Show the explanation next to the button until the next click."""
        QWhatsThis.showText(
            self.mapToGlobal(self.rect().bottomLeft()), self.explanation(), self
        )

    def _sync_text(self) -> None:
        text = self.explanation()
        self.setToolTip(text)
        self.setAccessibleDescription(text)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Follow tooltip and visibility changes of the explained controls."""
        kind = event.type()
        if kind == QEvent.Type.ToolTipChange:
            self._sync_text()
        elif watched is self._controls[0] and kind in (
            QEvent.Type.ShowToParent,
            QEvent.Type.HideToParent,
        ):
            self.setVisible(not self._controls[0].isHidden())
        return False


def _explained_controls(item: object) -> list[QWidget]:
    """Return the controls with tooltips in one form cell."""
    if item is None:
        return []
    widget = item.widget()
    if widget is not None:
        return [widget] if isinstance(widget, _CONTROLS) and widget.toolTip() else []
    layout = item.layout()
    if not isinstance(layout, QLayout):
        return []
    controls = []
    for index in range(layout.count()):
        controls.extend(_explained_controls(layout.itemAt(index)))
    return controls


def _control_title(control: QWidget) -> str:
    """Return the visible text of a button, without mnemonic marks."""
    if isinstance(control, QAbstractButton):
        return control.text().replace("&", "")
    return ""


def _help_row(parent: QWidget, *widgets: QWidget) -> QWidget:
    row = QWidget(parent)
    row.setObjectName(HELP_ROW_NAME)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    for widget in widgets:
        layout.addWidget(widget)
    layout.addStretch(1)
    return row


def add_form_help(widget: QWidget) -> None:
    """Add a "?" button to each form row that explains its control in a tooltip.

    A click on "?" shows the explanation without a hover wait. Hover tooltips
    stay. A row with a label gets the button after its label. A row with an
    empty label gets the button in the label column. A control that spans the
    row gets the button after it. Rows that already have help, text-only rows,
    and hidden spanning rows do not change. A second call adds nothing.

    Parameters
    ----------
    widget : `QWidget`
        Widget that contains the forms.
    """
    label_role = QFormLayout.ItemRole.LabelRole
    field_role = QFormLayout.ItemRole.FieldRole
    span_role = QFormLayout.ItemRole.SpanningRole
    for form in widget.findChildren(QFormLayout):
        parent = form.parentWidget()
        if parent is None:
            continue
        for row in range(form.rowCount()):
            span_item = form.itemAt(row, span_role)
            if span_item is not None:
                # Qt also returns a spanning item for the field role.
                span = span_item.widget()
                controls = _explained_controls(span_item)
                if span is None or not controls or span.isHidden():
                    continue
                form.removeWidget(span)
                button = FieldHelpButton(controls, _control_title(span), parent)
                # A layout, not a widget: the macOS style then aligns the
                # control exactly as it aligned it directly in the form.
                layout = QHBoxLayout()
                layout.setContentsMargins(0, 0, 0, 0)
                layout.setSpacing(4)
                layout.addWidget(span)
                layout.addWidget(button)
                layout.addStretch(1)
                form.setLayout(row, span_role, layout)
                continue
            controls = _explained_controls(form.itemAt(row, field_role))
            if not controls:
                continue
            label_item = form.itemAt(row, label_role)
            if label_item is None:
                # An empty label: the button alone fills the label cell.
                button = FieldHelpButton(controls, _control_title(controls[0]), parent)
                help_row = _help_row(parent, button)
                form.setWidget(row, label_role, help_row)
                help_row.setVisible(not controls[0].isHidden())
                continue
            label = label_item.widget()
            if not isinstance(label, QLabel):
                # The row already has help, or a label that is not text.
                continue
            # A hidden row stays hidden. `setRowVisible` still works on it,
            # because the control stays in the form.
            hidden = label.isHidden()
            title = label.text().strip().rstrip(":")
            form.removeWidget(label)
            button = FieldHelpButton(controls, title, parent)
            help_row = _help_row(parent, label, button)
            label.setVisible(True)
            form.setWidget(row, label_role, help_row)
            help_row.setVisible(not hidden)
