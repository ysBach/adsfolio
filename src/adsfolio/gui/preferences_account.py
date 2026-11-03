"""Account settings that can be embedded in the Settings window."""

from __future__ import annotations

import os

from PySide6.QtCore import QEvent, QObject, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import Config
from ..user_profile import UserProfile
from .dialogs import UserProfileDialog
from .token_dialog import TokenDialog


class _RedactedReply:
    """Hide network error text while the original dialog handles the reply."""

    def __init__(self, reply) -> None:
        self._reply = reply

    def attribute(self, attribute):
        return self._reply.attribute(attribute)

    def error(self):
        return self._reply.error()

    def errorString(self) -> str:
        return "Check your connection and try again."

    def deleteLater(self) -> None:
        self._reply.deleteLater()


class _TokenPreferences(TokenDialog):
    """Reuse the token dialog inside the Settings window."""

    saved = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Widget)
        instructions = self.layout().itemAt(0).widget()
        instructions.setText(
            'Get your token from <a href="https://scixplorer.org/user/settings/token">'
            "ADS / SciXplorer settings</a>. Paste it below to enable ADS queries."
        )
        buttons = self.findChild(QDialogButtonBox)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).hide()
        self.save_status = self.error
        self.token.textChanged.connect(self.save_status.clear)
        self.token.textChanged.connect(self.test_status.clear)
        self.token.textChanged.connect(self.cancel_test)

    def _show_source(self) -> None:
        self.environment_notice.setVisible(
            bool(os.environ.get(Config.ENV_VAR, "").strip())
        )
        try:
            super()._show_source()
        except (OSError, ValueError):
            self.source_label.setText("Cannot read the active token source.")

    @Slot()
    def accept(self) -> None:
        if not self.token.text().strip():
            self.save_status.setText("Paste a token to save it.")
            self.token.setFocus()
            return
        self.save_status.clear()
        try:
            super().accept()
        except ValueError:
            self.save_status.setText("Could not save the token. Check your settings.")

    def done(self, result: int) -> None:
        if result == QDialog.DialogCode.Accepted:
            self._show_source()
            self.save_status.setText("Token saved.")
            self.saved.emit()
        else:
            self.cancel_test()

    def reject(self) -> None:
        """Keep the embedded controls visible when Escape reaches the dialog."""

    @Slot()
    def test_token(self) -> None:
        """Use the token dialog to start a test and report safe errors."""
        try:
            super().test_token()
        except (OSError, ValueError, RuntimeError):
            self.test_button.setEnabled(True)
            self.test_status.setText("Could not test the token. Check your settings.")

    @Slot()
    def _test_finished(self) -> None:
        if self._reply is not None:
            self._reply = _RedactedReply(self._reply)
        super()._test_finished()

    def cancel_test(self) -> None:
        """Use the token dialog to cancel a running test."""
        self._cancel_test(restore=True)

    def _cancel_test(self, *, restore: bool) -> None:
        if getattr(self, "_reply", None) is None:
            return
        was_visible = self.isVisible()
        TokenDialog.done(self, QDialog.DialogCode.Rejected)
        self.test_status.setText("Token test canceled.")
        if restore and was_visible:
            self.show()

    def hideEvent(self, event) -> None:
        self._cancel_test(restore=False)
        super().hideEvent(event)


class _ProfilePreferences(UserProfileDialog):
    """Reuse profile fields and validation in the Settings window."""

    saved = Signal()

    def __init__(self, profile: UserProfile, parent: QWidget | None = None) -> None:
        super().__init__(
            profile,
            save_profile=lambda value: Config.save_user_profile(value),
            parent=parent,
        )
        self.setWindowFlags(Qt.WindowType.Widget)
        buttons = self.findChild(QDialogButtonBox)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).hide()
        self.save_status = QLabel(self)
        self.save_status.setWordWrap(True)
        self.layout().addRow(self.save_status)
        for field in (self.orcid, self.author_names, self.library_ids):
            field.textChanged.connect(self.save_status.clear)
        for field in (self.first_year, self.last_year):
            field.valueChanged.connect(self.save_status.clear)

    @Slot()
    def accept(self) -> None:
        try:
            profile = self.selected_profile()
        except ValueError:
            if (
                self.first_year.value()
                and self.last_year.value()
                and self.first_year.value() > self.last_year.value()
            ):
                message = "First publication year must not exceed the last year."
            else:
                message = "Enter a valid ORCID iD, or leave it blank."
            self.save_status.setText(message)
            return
        try:
            self._save_profile(profile)
        except (OSError, ValueError):
            self.save_status.setText(
                "Could not save the publication profile. "
                "Check configuration directory permissions."
            )
            return
        self.save_status.setText("Publication profile saved.")
        self.saved.emit()

    def reject(self) -> None:
        """Keep the embedded controls visible when Escape reaches the dialog."""


class AccountPreferences(QWidget):
    """Show ADS token and publication profile settings inside a window.

    Parameters
    ----------
    parent : `QWidget`, optional
        Parent widget for the account page.

    Attributes
    ----------
    token_saved : `Signal`
        Emit after ADSfolio saves a token. The signal has no arguments.
    profile_saved : `Signal`
        Emit after ADSfolio saves a publication profile. The signal has no arguments.
    """

    token_saved = Signal()
    profile_saved = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._watched_window: QWidget | None = None
        self.token_dialog: _TokenPreferences | None = None
        self.profile_dialog: _ProfilePreferences | None = None
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        self.tabs.addTab(QWidget(self.tabs), "ADS token")
        self.tabs.addTab(QWidget(self.tabs), "Publication profile")
        self.tabs.currentChanged.connect(self._show_current_tab)
        layout.addWidget(self.tabs)
        self.load_status = QLabel(self)
        self.load_status.setWordWrap(True)
        layout.addWidget(self.load_status)
        self.load_button = QPushButton("Load Account Settings", self)
        self.load_button.clicked.connect(self._load_account)
        layout.addWidget(self.load_button)
        self._load_account()
        self._watch_window()

    @Slot()
    def _load_account(self) -> None:
        try:
            profile = Config.load_user_profile()
            token_dialog = _TokenPreferences(self.tabs)
            profile_dialog = _ProfilePreferences(profile, self.tabs)
        except (OSError, ValueError):
            self.load_status.setText(
                "Could not load account settings. Check configuration directory permissions."
            )
            self.load_button.show()
            return
        index = self.tabs.currentIndex()
        while self.tabs.count():
            old = self.tabs.widget(0)
            self.tabs.removeTab(0)
            old.deleteLater()
        self.token_dialog = token_dialog
        self.profile_dialog = profile_dialog
        token_dialog.saved.connect(self.token_saved.emit)
        profile_dialog.saved.connect(self.profile_saved.emit)
        self.tabs.addTab(token_dialog, "ADS token")
        self.tabs.addTab(profile_dialog, "Publication profile")
        self.tabs.setCurrentIndex(max(0, index))
        self.load_status.clear()
        self.load_button.hide()

    def _show_current_tab(self, _index: int) -> None:
        if (
            self.token_dialog is not None
            and self.tabs.currentWidget() is self.token_dialog
        ):
            self.token_dialog.show()

    def _cancel_test(self) -> None:
        if self.token_dialog is not None:
            self.token_dialog.cancel_test()

    def _watch_window(self) -> None:
        window = self.window()
        previous = getattr(self, "_watched_window", None)
        if previous is window:
            return
        if previous is not None:
            try:
                previous.removeEventFilter(self)
            except RuntimeError:
                pass
        self._watched_window = None if window is self else window
        if self._watched_window is not None:
            self._watched_window.installEventFilter(self)

    def event(self, event: QEvent) -> bool:
        result = super().event(event)
        if event.type() == QEvent.Type.ParentChange:
            self._watch_window()
        return result

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is getattr(self, "_watched_window", None) and event.type() in (
            QEvent.Type.Close,
            QEvent.Type.Hide,
        ):
            self._cancel_test()
        return super().eventFilter(watched, event)

    def showEvent(self, event) -> None:
        self._watch_window()
        self._show_current_tab(self.tabs.currentIndex())
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        self._cancel_test()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:
        self._cancel_test()
        super().closeEvent(event)
