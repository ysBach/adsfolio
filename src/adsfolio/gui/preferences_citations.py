"""Embeddable citation preferences with separate save actions."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialogButtonBox,
    QFrame,
    QLabel,
    QMessageBox,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..citations import CitationStyle
from ..citekeys import KeyPolicy
from .bibtex_export import CitationKeySettingsDialog
from .citation_styles import CitationStylesDialog


class _KeyEditor(CitationKeySettingsDialog):
    """Keep the embedded editor open after Save or Escape."""

    def accept(self) -> None:
        """Leave closing to the parent settings window."""

    def reject(self) -> None:
        """Leave closing to the parent settings window."""


class _StylesEditor(CitationStylesDialog):
    """Keep the embedded editor open after Save or Escape."""

    def accept(self) -> None:
        """Leave closing to the parent settings window."""

    def reject(self) -> None:
        """Leave closing to the parent settings window."""


class CitationPreferences(QWidget):
    """Edit citation keys and styles inside a settings window.

    The page saves a tab only when the user selects its Save button.
    Each callback must leave all settings unchanged if its save fails.
    A callback can raise an exception or return `False` to report failure.

    Parameters
    ----------
    initial_policy : `KeyPolicy`
        Initial citation key policy.
    initial_styles : sequence of `CitationStyle`
        Initial custom citation styles.
    save_policy : callable
        Save callback that receives a validated `KeyPolicy`.
    save_styles : callable
        Save callback that receives a tuple of validated `CitationStyle` objects.
    preview_record : mapping or `None`, optional
        Record for style previews. `None` uses the editor's example record.
    parent : `QWidget` or `None`, optional
        Parent settings widget.
    """

    def __init__(
        self,
        initial_policy: KeyPolicy,
        initial_styles: Sequence[CitationStyle],
        *,
        save_policy: Callable[[KeyPolicy], Any],
        save_styles: Callable[[tuple[CitationStyle, ...]], Any],
        preview_record: Mapping[str, Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._save_policy_callback = save_policy
        self._save_styles_callback = save_styles

        self.key_dialog = _KeyEditor(self, initial_policy)
        self.styles_dialog = _StylesEditor(tuple(initial_styles), preview_record, self)
        self.key_status = QLabel(self.key_dialog)
        self.styles_status = QLabel(self.styles_dialog)
        self.key_dialog.layout().addWidget(self.key_status)
        self.styles_dialog.layout().addWidget(self.styles_status)
        self.tabs = QTabWidget(self)
        self.tabs.addTab(self._embed(self.key_dialog, self.key_dialog.buttons), "Keys")
        self.tabs.addTab(
            self._embed(self.styles_dialog, self.styles_dialog.button_box), "Styles"
        )

        self.key_dialog.buttons.accepted.connect(self._save_policy)
        self.styles_dialog.saved.connect(self._save_styles)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tabs)

    def _embed(self, editor: QWidget, buttons: QDialogButtonBox) -> QScrollArea:
        editor.setWindowFlags(Qt.WindowType.Widget)
        cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel is not None:
            cancel.hide()
        scroll = QScrollArea(self)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(editor)
        return scroll

    def _save_policy(self) -> None:
        try:
            policy = self.key_dialog.policy()
            result = self._save_policy_callback(policy)
            if result is False:
                raise RuntimeError("The save failed.")
        except Exception as exc:  # noqa: BLE001 -- report callback failures
            self.key_status.clear()
            self._show_save_error("Citation keys not saved", exc)
        else:
            self.key_status.setText("Citation keys saved.")

    def _save_styles(self, styles: tuple[CitationStyle, ...]) -> None:
        try:
            result = self._save_styles_callback(styles)
            if result is False:
                raise RuntimeError("The save failed.")
        except Exception as exc:  # noqa: BLE001 -- report callback failures
            self.styles_status.clear()
            self._show_save_error("Citation styles not saved", exc)
        else:
            self.styles_status.setText("Citation styles saved.")

    def _show_save_error(self, title: str, error: Exception) -> None:
        detail = str(error) or "The save failed."
        QMessageBox.warning(
            self,
            title,
            f"Your changes remain in this tab. Try Save again.\n\n{detail}",
        )
