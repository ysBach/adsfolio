"""Compact non-modal notifications for the desktop GUI."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QPalette
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QScrollArea,
    QSizePolicy,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from .icons import (
    ICON_SIZE,
    BadgeToolButton,
    RoundIconButton,
    VectorIconLabel,
    vector_icon,
)

__all__ = [
    "Notice",
    "NoticeAction",
    "NoticeBar",
    "NotificationButton",
    "NotificationPanel",
]


class NoticeAction(str, Enum):
    """Typed action identity with its user-facing label."""

    IDENTIFY_RENAME = "Identify & Rename"
    IDENTIFY = "Identify…"
    RETRY = "Retry"
    VIEW_RELEASE = "Update Details"
    MERGE_EDITIONS = "Review Merge…"
    SHOW_CONFLICT = "Show Both PDFs"


@dataclass(frozen=True)
class Notice:
    """Immutable display data and opaque action payload for one notice."""

    key: str
    message: str
    severity: str = "info"
    action_label: str | None = None
    payload: object | None = None
    root: Path | None = None
    action: NoticeAction | None = None
    details: str | None = None
    manual_entry: bool = False
    reveal_path: Path | None = None
    update_by_id: bool = False
    candidate_bibcode: str | None = None
    candidate_sha256: str | None = None
    candidate_fingerprint: str | None = None
    candidate_confirmable: bool = False

    def __post_init__(self) -> None:
        if self.root is not None and not isinstance(self.root, Path):
            object.__setattr__(self, "root", Path(self.root))
        if self.reveal_path is not None and not isinstance(self.reveal_path, Path):
            object.__setattr__(self, "reveal_path", Path(self.reveal_path))
        action = self.action
        if action is not None and not isinstance(action, NoticeAction):
            action = NoticeAction(action)
        if action is None and self.action_label is not None:
            action = NoticeAction(self.action_label)
        if (
            action is not None
            and self.action_label is not None
            and self.action_label != action.value
        ):
            raise ValueError("Notice action identity and label do not match")
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "action_label", action.value if action else None)


def _update_immediate_tooltip(widget: QWidget, event: QEvent) -> None:
    """Display an action tooltip without the platform's hover delay."""
    show = event.type() in (QEvent.Enter, QEvent.FocusIn) or (
        event.type() == QEvent.ToolTipChange
        and (widget.underMouse() or widget.hasFocus())
    )
    if show:
        if widget.isVisible() and widget.isEnabled() and widget.toolTip():
            QToolTip.showText(
                widget.mapToGlobal(widget.rect().bottomLeft()), widget.toolTip(), widget
            )
    elif event.type() in (QEvent.Leave, QEvent.FocusOut, QEvent.Hide):
        QToolTip.hideText()


class _NoticeButton(RoundIconButton):
    """Display a compact action icon with an immediate hover or focus tooltip."""

    def event(self, event: QEvent) -> bool:
        """Update the action tooltip when hover or keyboard focus changes."""
        _update_immediate_tooltip(self, event)
        return super().event(event)


class NotificationButton(BadgeToolButton):
    """Keep notification access in the toolbar without changing its height."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setText("Notifications")
        self.setAccessibleName("Notifications")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.setIconSize(ICON_SIZE)
        self.setFixedHeight(RoundIconButton.DIAMETER)
        self.setMinimumWidth(RoundIconButton.DIAMETER)
        self.setAutoRaise(True)

    def event(self, event: QEvent) -> bool:
        """Display the notification tooltip immediately on hover or focus."""
        _update_immediate_tooltip(self, event)
        return super().event(event)


class NoticeBar(QFrame):
    """Show one notice from an insertion-ordered replacement queue."""

    action_requested = Signal(object)
    manual_entry_requested = Signal(object)
    update_by_id_requested = Signal(object)
    open_requested = Signal(object)
    ads_entry_requested = Signal(str)
    use_id_requested = Signal(object)
    reveal_requested = Signal(object)
    dismiss_requested = Signal(object)
    action_all_requested = Signal(tuple)
    dismiss_all_requested = Signal(tuple)
    changed = Signal()

    @staticmethod
    def _button_text(label: str) -> str:
        """Escape literal ampersands so Qt does not consume them as mnemonics."""
        return label.replace("&", "&&")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._notices: dict[str, Notice] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        message_row = QHBoxLayout()
        actions = QHBoxLayout()
        layout.addLayout(message_row)
        layout.addLayout(actions)
        self.severity_icon = VectorIconLabel("info", "Information", self)
        self.message = QLabel(self)
        self.message.setWordWrap(True)
        self.message.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard
        )
        self.message.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.pending_count = QLabel(self)
        self.action_button = _NoticeButton("refresh", "", self)
        self.open_button = _NoticeButton("pdf", "Open PDF", self)
        self.open_button.setIcon(vector_icon("pdf", self.open_button))
        self.open_button.setIconSize(ICON_SIZE)
        self.open_button.setAccessibleName("Open PDF")
        self.ads_entry_button = _NoticeButton("open", "View ADS Entry", self)
        self.ads_entry_button.setIcon(vector_icon("open", self.ads_entry_button))
        self.ads_entry_button.setIconSize(ICON_SIZE)
        self.ads_entry_button.setAccessibleName("View ADS Entry")
        self.use_id_button = _NoticeButton("success", "Use This ID", self)
        self.update_by_id_button = _NoticeButton("identify", "Update by ID…", self)
        self.update_by_id_button.setIcon(
            vector_icon("identify", self.update_by_id_button)
        )
        self.update_by_id_button.setIconSize(ICON_SIZE)
        self.update_by_id_button.setAccessibleName("Update by ID")
        self.update_by_id_button.setToolTip(
            "Update by ID\nLook up this PDF by Bibcode, DOI, arXiv ID, or bibliographic reference."
        )
        self.manual_entry_button = _NoticeButton("edit", "Enter Details…", self)
        self.manual_entry_button.setIcon(vector_icon("edit", self.manual_entry_button))
        self.manual_entry_button.setIconSize(ICON_SIZE)
        self.manual_entry_button.setAccessibleName("Enter Details")
        self.manual_entry_button.setToolTip(
            "Enter Details\nEnter paper metadata without an online lookup."
        )
        self.reveal_button = _NoticeButton(
            "folder",
            "Reveal in Finder"
            if sys.platform == "darwin"
            else "Reveal in File Manager",
            self,
        )
        self.reveal_button.setIcon(vector_icon("folder", self.reveal_button))
        self.reveal_button.setIconSize(ICON_SIZE)
        self.reveal_button.setAccessibleName(self.reveal_button.text())
        self.action_all_button = _NoticeButton(
            "identify", self._button_text("Identify & Rename All"), self
        )
        self.dismiss_button = _NoticeButton("close", "Dismiss", self)
        self.dismiss_all_button = _NoticeButton("close_all", "Dismiss All", self)
        for button in (self.dismiss_button, self.dismiss_all_button):
            button.setIcon(
                vector_icon(
                    "close_all" if button is self.dismiss_all_button else "close",
                    button,
                )
            )
            button.setIconSize(ICON_SIZE)
            button.setAccessibleName(button.text())
            button.setToolTip(button.text())
        message_row.addWidget(self.severity_icon, 0, Qt.AlignTop)
        message_row.addWidget(self.message, 1)
        actions.addWidget(self.pending_count)
        actions.addStretch()
        for button in (
            self.open_button,
            self.ads_entry_button,
            self.use_id_button,
            self.update_by_id_button,
            self.action_button,
            self.manual_entry_button,
            self.reveal_button,
            self.action_all_button,
            self.dismiss_button,
            self.dismiss_all_button,
        ):
            actions.addWidget(button)
        self.action_button.clicked.connect(self._request_action)
        self.open_button.clicked.connect(self._request_open)
        self.ads_entry_button.clicked.connect(self._request_ads_entry)
        self.use_id_button.clicked.connect(self._request_use_id)
        self.update_by_id_button.clicked.connect(self._request_update_by_id)
        self.manual_entry_button.clicked.connect(self._request_manual_entry)
        self.reveal_button.clicked.connect(self._request_reveal)
        self.action_all_button.clicked.connect(self._request_all_actions)
        self.dismiss_button.clicked.connect(self._request_dismiss)
        self.dismiss_all_button.clicked.connect(self._request_all_dismissals)
        self._render()

    def enqueue(self, notice: Notice) -> None:
        """Add or replace one notice without changing its queue position."""
        self._notices[notice.key] = notice
        self._render()

    def dismiss(self, key: str) -> None:
        """Remove one keyed notice if it is queued."""
        self._notices.pop(key, None)
        self._render()

    def clear_root(self, root: Path) -> None:
        """Remove notices explicitly associated with `root`."""
        normalized = Path(root).expanduser().resolve()
        self._notices = {
            key: notice
            for key, notice in self._notices.items()
            if notice.root is None or notice.root.expanduser().resolve() != normalized
        }
        self._render()

    def queued_keys(self) -> tuple[str, ...]:
        """Return queued notice keys in display order."""
        return tuple(self._notices)

    def queued_notices(self) -> tuple[Notice, ...]:
        """Return notices in display order without changing the queue."""
        return tuple(self._notices.values())

    def _current(self) -> Notice | None:
        return next(iter(self._notices.values()), None)

    def _request_action(self) -> None:
        notice = self._current()
        if notice is not None:
            self.action_requested.emit(notice.payload)

    def _request_dismiss(self) -> None:
        notice = self._current()
        if notice is None:
            return
        self.dismiss_requested.emit(notice.payload)
        self.dismiss(notice.key)

    def _request_manual_entry(self) -> None:
        notice = self._current()
        if notice is not None and notice.manual_entry:
            self.manual_entry_requested.emit(notice.payload)

    def _request_reveal(self) -> None:
        """Request the displayed notice's path without dismissing it."""
        notice = self._current()
        if notice is not None and notice.reveal_path is not None:
            self.reveal_requested.emit(notice.reveal_path)

    def _request_open(self) -> None:
        """Open the displayed notice's PDF without dismissing its notice."""
        notice = self._current()
        if notice is not None and notice.reveal_path is not None:
            self.open_requested.emit(notice.reveal_path)

    def _request_update_by_id(self) -> None:
        """Request an identifier lookup for the displayed notice's PDF."""
        notice = self._current()
        if notice is not None and notice.update_by_id:
            self.update_by_id_requested.emit(notice.payload)

    def _request_ads_entry(self) -> None:
        """Open the displayed candidate's ADS entry without confirming its identity."""
        notice = self._current()
        if notice is not None and notice.candidate_bibcode:
            self.ads_entry_requested.emit(notice.candidate_bibcode)

    @staticmethod
    def _can_use_id(notice: Notice) -> bool:
        return bool(
            notice.candidate_confirmable
            and notice.candidate_bibcode
            and notice.candidate_sha256
            and notice.candidate_fingerprint
        )

    def _request_use_id(self) -> None:
        """Confirm the displayed candidate for the exact PDF that produced it."""
        notice = self._current()
        if notice is not None and self._can_use_id(notice):
            self.use_id_requested.emit(notice)

    def _request_all_actions(self) -> None:
        payloads = tuple(
            notice.payload
            for notice in self._notices.values()
            if notice.action is NoticeAction.IDENTIFY_RENAME
        )
        self.action_all_requested.emit(payloads)

    def _request_all_dismissals(self) -> None:
        payloads = tuple(notice.payload for notice in self._notices.values())
        self.dismiss_all_requested.emit(payloads)
        self._notices.clear()
        self._render()

    def _render(self) -> None:
        notice = self._current()
        self.setVisible(notice is not None)
        if notice is None:
            self.message.clear()
            self.message.setToolTip("")
            self.pending_count.clear()
            self.pending_count.setToolTip("")
            self.action_button.hide()
            self.open_button.hide()
            self.ads_entry_button.hide()
            self.ads_entry_button.setToolTip("")
            self.use_id_button.hide()
            self.update_by_id_button.hide()
            self.manual_entry_button.hide()
            self.reveal_button.hide()
            self.action_all_button.hide()
            self.dismiss_button.hide()
            self.dismiss_all_button.hide()
            self.changed.emit()
            return
        self.message.setText(notice.message)
        self.message.setToolTip(notice.details or notice.message)
        remaining = len(self._notices) - 1
        self.pending_count.setText(f"{remaining} more" if remaining else "")
        self.pending_count.setToolTip(
            "\n\n".join(
                f"{item.message}\n{item.details}" if item.details else item.message
                for item in self._notices.values()
            )
            if remaining
            else ""
        )
        self.action_button.setText(self._button_text(notice.action_label or ""))
        for button in (self.action_button, self.action_all_button):
            button.setIcon(
                vector_icon(
                    "open" if notice.action is NoticeAction.VIEW_RELEASE else "refresh",
                    button,
                )
            )
            button.setIconSize(ICON_SIZE)
            button.setAccessibleName(button.text().replace("&&", "&"))
            button.setToolTip(button.accessibleName())
        self.action_button.setVisible(notice.action_label is not None)
        self.open_button.setVisible(notice.reveal_path is not None)
        self.open_button.setToolTip(
            f"Open PDF\n{notice.reveal_path}" if notice.reveal_path is not None else ""
        )
        self.ads_entry_button.setVisible(bool(notice.candidate_bibcode))
        self.ads_entry_button.setToolTip(
            f"View ADS Entry: {notice.candidate_bibcode}\n"
            "Compare its title, authors, year, and DOI with the PDF."
            if notice.candidate_bibcode
            else ""
        )
        self.use_id_button.setVisible(self._can_use_id(notice))
        self.use_id_button.setToolTip(
            f"Use This ID: {notice.candidate_bibcode}\n"
            "Confirm this record matches the PDF. Update its catalog metadata and apply saved Rename settings. "
            "The PDF contents stay unchanged."
            if self._can_use_id(notice)
            else ""
        )
        self.update_by_id_button.setVisible(notice.update_by_id)
        self.manual_entry_button.setVisible(notice.manual_entry)
        self.reveal_button.setVisible(notice.reveal_path is not None)
        self.reveal_button.setToolTip(
            f"{self.reveal_button.accessibleName()}\n{notice.reveal_path}"
            if notice.reveal_path is not None
            else ""
        )
        manual_actions = sum(
            item.action is NoticeAction.IDENTIFY_RENAME
            for item in self._notices.values()
        )
        self.action_all_button.setVisible(
            notice.action is NoticeAction.IDENTIFY_RENAME and manual_actions > 1
        )
        acknowledged = notice.severity == "success" and notice.action is None
        self.dismiss_button.setText("OK" if acknowledged else "Dismiss")
        all_acknowledged = all(
            item.severity == "success" and item.action is None
            for item in self._notices.values()
        )
        self.dismiss_all_button.setText("OK All" if all_acknowledged else "Dismiss All")
        for button, positive in (
            (self.dismiss_button, acknowledged),
            (self.dismiss_all_button, all_acknowledged),
        ):
            icon = (
                "success"
                if positive
                else "close_all"
                if button is self.dismiss_all_button
                else "close"
            )
            button.setIcon(vector_icon(icon, button))
            button.setAccessibleName(button.text())
            button.setToolTip(button.text())
        self.dismiss_button.show()
        self.dismiss_all_button.setVisible(len(self._notices) > 1)
        colors = {
            "error": ("#fde7e9", "#8b0000"),
            "warning": ("#fff4ce", "#6b4f00"),
            "success": ("#dff6dd", "#165c26"),
        }
        background, foreground = colors.get(notice.severity, ("#eef3f8", "#1f2933"))
        self.setStyleSheet(
            f"NoticeBar {{ background: {background}; color: {foreground}; }}"
        )

        severity = notice.severity if notice.severity in colors else "info"
        self.severity_icon.set_icon(severity, severity.capitalize())
        palette = self.severity_icon.palette()
        palette.setColor(QPalette.WindowText, QColor(foreground))
        self.severity_icon.setPalette(palette)
        self.changed.emit()


class NotificationPanel(QFrame):
    """Display queued notices and view help outside the main pane layout."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.Popup)
        self.setObjectName("notification_panel")
        self.setAccessibleName("Notifications and view help")
        self.setFocusPolicy(Qt.StrongFocus)
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        header.addWidget(QLabel("Notifications", self))
        header.addStretch()
        close = _NoticeButton("close", "Close Notifications", self)
        close.clicked.connect(self.hide)
        header.addWidget(close)
        layout.addLayout(header)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget(self.scroll_area)
        contents = QVBoxLayout(content)
        contents.setSizeConstraint(QLayout.SetMinimumSize)
        self.notice_bar = NoticeBar(content)
        contents.addWidget(self.notice_bar)
        self.guidance = QWidget(content)
        guidance_layout = QHBoxLayout(self.guidance)
        self.view_explanation = QLabel(self.guidance)
        self.view_explanation.setWordWrap(True)
        self.view_explanation.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard
        )
        self.view_explanation.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        guidance_layout.addWidget(self.view_explanation, 1)
        self.identify_button = _NoticeButton(
            "identify", "Identify papers…", self.guidance
        )
        guidance_layout.addWidget(self.identify_button)
        self.guidance.hide()
        contents.addWidget(self.guidance)
        self.empty = QLabel("No notifications.", content)
        contents.addWidget(self.empty)
        contents.addStretch()
        self.scroll_area.setWidget(content)
        layout.addWidget(self.scroll_area)
        self._displayed_notice: tuple[str, str] | None = None
        self.notice_bar.changed.connect(self._sync_empty)
        self.hide()

    def set_guidance(self, text: str, *, identify: bool) -> None:
        """Update view help without changing the main window's geometry.

        Parameters
        ----------
        text
            Explanation for the selected sidebar group. Empty hides the help.
        identify
            Whether the help offers the Identify papers action.
        """
        self.view_explanation.setText(text)
        self.guidance.setVisible(bool(text))
        self.identify_button.setVisible(bool(text) and identify)
        self._sync_empty()

    def _sync_empty(self) -> None:
        self.empty.setVisible(
            not self.notice_bar.queued_keys() and self.guidance.isHidden()
        )
        notices = self.notice_bar.queued_notices()
        current = (notices[0].key, notices[0].message) if notices else None
        if current != self._displayed_notice:
            self.scroll_area.verticalScrollBar().setValue(0)
            self._displayed_notice = current

    def show_for(self, anchor: QWidget) -> None:
        """Open the scrollable panel beside its toolbar control.

        Parameters
        ----------
        anchor
            Toolbar control whose screen and position place the popup.
        """
        available = anchor.screen().availableGeometry()
        width = min(720, anchor.window().width(), available.width())
        height = min(420, anchor.window().height(), available.height())
        self.resize(width, height)
        point = anchor.mapToGlobal(QPoint(0, anchor.height()))
        x = max(available.left(), min(point.x(), available.right() - width + 1))
        y = max(available.top(), min(point.y(), available.bottom() - height + 1))
        self.move(x, y)
        self.show()
        self.raise_()
        self.setFocus(Qt.PopupFocusReason)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Close the panel on Escape without dismissing queued notices."""
        if event.key() == Qt.Key_Escape:
            self.hide()
            event.accept()
            return
        super().keyPressEvent(event)
