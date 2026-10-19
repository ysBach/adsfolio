"""Compact non-modal notifications for the desktop GUI."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

__all__ = ["Notice", "NoticeAction", "NoticeBar"]


class NoticeAction(str, Enum):
    """Typed action identity with its user-facing label."""

    IDENTIFY_RENAME = "Identify & Rename"
    RETRY = "Retry"


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

    def __post_init__(self) -> None:
        if self.root is not None and not isinstance(self.root, Path):
            object.__setattr__(self, "root", Path(self.root))
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


class NoticeBar(QFrame):
    """Show one notice from an insertion-ordered replacement queue."""

    action_requested = Signal(object)
    dismiss_requested = Signal(object)
    action_all_requested = Signal(tuple)
    dismiss_all_requested = Signal(tuple)

    @staticmethod
    def _button_text(label: str) -> str:
        """Escape literal ampersands so Qt does not consume them as mnemonics."""
        return label.replace("&", "&&")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._notices: dict[str, Notice] = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        self.message = QLabel(self)
        self.message.setWordWrap(True)
        self.pending_count = QLabel(self)
        self.action_button = QPushButton(self)
        self.action_all_button = QPushButton(
            self._button_text("Identify & Rename All"), self
        )
        self.dismiss_button = QPushButton("Dismiss", self)
        self.dismiss_all_button = QPushButton("Dismiss All", self)
        layout.addWidget(self.message, 1)
        layout.addWidget(self.pending_count)
        layout.addWidget(self.action_button)
        layout.addWidget(self.action_all_button)
        layout.addWidget(self.dismiss_button)
        layout.addWidget(self.dismiss_all_button)
        self.action_button.clicked.connect(self._request_action)
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
            self.action_all_button.hide()
            self.dismiss_button.hide()
            self.dismiss_all_button.hide()
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
        self.action_button.setVisible(notice.action_label is not None)
        manual_actions = sum(
            item.action is NoticeAction.IDENTIFY_RENAME
            for item in self._notices.values()
        )
        self.action_all_button.setVisible(
            notice.action is NoticeAction.IDENTIFY_RENAME and manual_actions > 1
        )
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
