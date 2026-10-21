"""Small stroke icons rendered as vectors using each widget's Qt palette."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QByteArray, QEvent, QObject, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QLabel, QToolButton, QWidget

ICON_SIZE = QSize(16, 16)

# All paths share a 24-unit canvas, rounded strokes, and a two-unit pen.
_PATHS = {
    "edit": '<path d="m15 5 4 4M4 20l4-1L20 7a2.8 2.8 0 0 0-4-4L4 15z"/>',
    "lock": '<rect x="5" y="10" width="14" height="11" rx="2"/>'
    '<path d="M8 10V7a4 4 0 0 1 8 0v3M12 15v2"/>',
    "unlock": '<rect x="5" y="10" width="14" height="11" rx="2"/>'
    '<path d="M8 10V7a4 4 0 0 1 8 0M12 15v2"/>',
    "grip": "".join(
        f'<circle cx="{x}" cy="{y}" r="1"/>' for x in (9, 15) for y in (5, 12, 19)
    ),
    "refresh": '<path d="M20 9a8 8 0 1 0 0 6M20 3v6h-6"/>',
    "open": '<path d="M14 3h7v7M21 3 10 14M10 5H5a2 2 0 0 0-2 2v12'
    'a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-5"/>',
    "folder": '<path d="M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v2'
    'M3 7h5l2 3h11l-3 11H3z"/>',
    "download": '<path d="M12 3v12M7 10l5 5 5-5M5 21h14"/>',
    # GitHub mark in outline form (Lucide "github", ISC license).
    "github": '<path d="M15 22v-4a4.8 4.8 0 0 0-1-3.5c3 0 6-2 6-5.5.08-1.25-.27-2.48'
    "-1-3.5.28-1.15.28-2.35 0-3.5 0 0-1 0-3 1.5-2.64-.5-5.36-.5-8 0C6 2 5 2 5 2"
    "c-.3 1.15-.3 2.35 0 3.5A5.403 5.403 0 0 0 4 9c0 3.5 3 5.5 6 5.5-.39.49-.68"
    '1.05-.85 1.65-.17.6-.22 1.23-.15 1.85v4"/><path d="M9 18c-4.51 2-5-2-7-2"/>',
    "close": '<path d="m6 6 12 12M18 6 6 18"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/>',
    "warning": '<path d="m12 3 10 18H2zM12 9v5M12 17h.01"/>',
    "error": '<circle cx="12" cy="12" r="9"/><path d="m9 9 6 6M15 9l-6 6"/>',
    "success": '<circle cx="12" cy="12" r="9"/><path d="m7 12 3 3 7-7"/>',
}


class _IconPaletteObserver(QObject):
    """Refresh native button icons when Qt changes their palette."""

    def __init__(self, name: str, widget: QWidget, role: QPalette.ColorRole) -> None:
        super().__init__(widget)
        self.name = name
        self.role = role
        widget.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Rebuild cached native pixmaps after a palette or enabled-state change."""
        if event.type() in (QEvent.PaletteChange, QEvent.EnabledChange):
            watched.setIcon(vector_icon(self.name, watched, self.role))
        return False


def vector_icon(
    name: str, widget: QWidget, role: QPalette.ColorRole = QPalette.ButtonText
) -> QIcon:
    """Render code-generated SVG strokes in the widget's active and disabled colors.

    Native Qt icons contain 1x, 2x, and 3x pixmaps for the shared 16-pixel size.
    Button icons refresh when their palette changes. No font glyphs are used.
    """
    path = _PATHS[name]
    icon = QIcon()
    for mode, group in (
        (QIcon.Normal, QPalette.Active),
        (QIcon.Disabled, QPalette.Disabled),
    ):
        color = widget.palette().color(group, role).name()
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
            f'fill="none" stroke="{color}" stroke-width="2" '
            f'stroke-linecap="round" stroke-linejoin="round">{path}</svg>'
        )
        renderer = QSvgRenderer(QByteArray(svg.encode()))
        for scale in (1, 2, 3):
            pixmap = QPixmap(ICON_SIZE * scale)
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            renderer.render(painter, QRectF(pixmap.rect()))
            painter.end()
            pixmap.setDevicePixelRatio(scale)
            icon.addPixmap(pixmap, mode)
    if hasattr(widget, "setIcon"):
        observer = widget.findChild(_IconPaletteObserver)
        if observer is None:
            observer = _IconPaletteObserver(name, widget, role)
        observer.name = name
        observer.role = role
    return icon


class VectorIconLabel(QLabel):
    """Display a vector icon with an accessible name and tooltip."""

    def __init__(
        self, name: str, description: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._name = name
        self._cached_icon: QIcon | None = None
        self._icon_key: tuple[str, int, float] | None = None
        self.setFixedSize(24, 24)
        self.setAccessibleName(description)
        self.setToolTip(description)

    def set_icon(self, name: str, description: str) -> None:
        """Change the displayed symbol and its accessible description."""
        self._name = name
        self.setAccessibleName(description)
        self.setToolTip(description)
        self.update()

    def paintEvent(self, event: Any) -> None:
        """Center the icon without rasterizing it at a fixed display scale."""
        painter = QPainter(self)
        rect = self.rect()
        rect.setSize(ICON_SIZE)
        rect.moveCenter(self.rect().center())
        mode = QIcon.Normal if self.isEnabled() else QIcon.Disabled
        key = (self._name, self.palette().cacheKey(), self.devicePixelRatioF())
        if self._cached_icon is None or self._icon_key != key:
            self._cached_icon = vector_icon(self._name, self, QPalette.WindowText)
            self._icon_key = key
        self._cached_icon.paint(painter, rect, Qt.AlignCenter, mode)


class RoundIconButton(QToolButton):
    """Circular icon-only button with a thin outline.

    The `label` is the tooltip and accessible name, because the button shows
    no text.
    """

    DIAMETER = 28

    def __init__(
        self, icon_name: str, label: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setText(label)
        self.setToolTip(label)
        self.setAccessibleName(label)
        self.setIcon(vector_icon(icon_name, self))
        self.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.setFixedSize(self.DIAMETER, self.DIAMETER)
        radius = self.DIAMETER // 2
        self.setStyleSheet(
            "QToolButton { border: 1px solid palette(mid); "
            f"border-radius: {radius}px; background: palette(base); padding: 0; }}"
            "QToolButton:hover { background: palette(alternate-base); }"
            "QToolButton:pressed { background: palette(midlight); }"
        )
