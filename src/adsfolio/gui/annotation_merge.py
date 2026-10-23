"""Interactive review dialog for moving annotations between PDF editions."""

from __future__ import annotations

from difflib import SequenceMatcher
from html import escape
from pathlib import Path
from threading import Event

from PySide6.QtCore import (
    QEvent,
    QObject,
    QPointF,
    QRectF,
    QRunnable,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..pdf.annotation_matching import (
    TextIndex,
    TextMatch,
    source_evidence,
    text_matches,
    wording_matches,
)
from ..pdf.annotation_merge import (
    AnnotationItem,
    AnnotationMergeError,
    MergeReport,
    Placement,
    build_merged_copy,
    enumerate_annotations,
    import_fitz,
    line_rects,
    placement_error,
    words_inside,
)

ZOOM = 1.4
_Matrix = tuple[float, float, float, float, float, float]
_IDENTITY: _Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _apply(matrix: _Matrix, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = matrix
    return a * x + c * y + e, b * x + d * y + f


def _apply_rect(matrix: _Matrix, rect: _RectTuple) -> _RectTuple:
    corners = [
        _apply(matrix, x, y)
        for x, y in (
            (rect[0], rect[1]),
            (rect[2], rect[1]),
            (rect[0], rect[3]),
            (rect[2], rect[3]),
        )
    ]
    xs, ys = [c[0] for c in corners], [c[1] for c in corners]
    return (min(xs), min(ys), max(xs), max(ys))


_RectTuple = tuple[float, float, float, float]


class PdfPageView(QLabel):
    """Render one PDF page and report clicks and drags in PDF points."""

    point_clicked = Signal(float, float)
    area_selected = Signal(float, float, float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._press: QPointF | None = None
        self._drag: QPointF | None = None
        self.overlays: list[tuple[_RectTuple, QColor, bool]] = []
        self._to_view: _Matrix = _IDENTITY
        self._from_view: _Matrix = _IDENTITY
        self.zoom = ZOOM
        self._image: QImage | None = None
        self._scroll: QScrollArea | None = None
        self._fit_mode = "Fit width"
        self._focus: _RectTuple | None = None

    def attach_scroll(self, scroll: QScrollArea) -> None:
        """Fit the page to this viewport and follow its size changes."""
        self._scroll = scroll
        scroll.viewport().installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Resize:
            self._fit()
        return super().eventFilter(watched, event)

    def set_fit_mode(self, mode: str) -> None:
        """Display the page at pane width, pane height, or 100 percent."""
        self._fit_mode = mode
        self._fit()

    def _fit(self) -> None:
        if self._image is None:
            return
        scale = 1.0
        if self._scroll is not None:
            viewport = self._scroll.viewport()
            width = max(1, viewport.width() - 2) / self._image.width()
            height = max(1, viewport.height() - 2) / self._image.height()
            scale = {
                "Fit width": width,
                "Fit page": min(width, height),
                "100%": 1 / ZOOM,
            }[self._fit_mode]
        self.zoom = ZOOM * scale
        pixmap = QPixmap.fromImage(self._image)
        size = self._image.size() * scale
        self.setPixmap(pixmap.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.resize(self.pixmap().size())
        if self._focus is not None:
            QTimer.singleShot(0, self._center_focus)

    def focus_rect(self, rect: _RectTuple) -> None:
        """Center the selected annotation in its page viewport."""
        self._focus = rect
        self._center_focus()
        QTimer.singleShot(0, self._center_focus)

    def _center_focus(self) -> None:
        if self._scroll is None or self._focus is None:
            return
        x0, y0, x1, y1 = _apply_rect(self._to_view, self._focus)
        viewport = self._scroll.viewport()
        self._scroll.horizontalScrollBar().setValue(
            round((x0 + x1) * self.zoom / 2 - viewport.width() / 2)
        )
        self._scroll.verticalScrollBar().setValue(
            round((y0 + y1) * self.zoom / 2 - viewport.height() / 2)
        )

    def show_page(self, path: Path, page_number: int) -> _RectTuple:
        """Draw a page as displayed (rotation and crop box applied).

        Returns the page rectangle in the unrotated coordinates used by
        annotations and text; overlays and mouse events use those coordinates.
        """
        fitz = import_fitz()
        doc = fitz.open(str(path))
        try:
            page = doc[page_number]
            pix = page.get_pixmap(matrix=fitz.Matrix(ZOOM, ZOOM), alpha=False)
            image = QImage(
                pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888
            ).copy()
            self._to_view = tuple(page.rotation_matrix)
            self._from_view = tuple(page.derotation_matrix)
            rect = tuple(page.rect * page.derotation_matrix)
        finally:
            doc.close()
        self._image = image
        self._focus = None
        self._fit()
        return rect

    def set_overlays(self, overlays: list[tuple[_RectTuple, QColor, bool]]) -> None:
        """Set boxes (PDF points, colour, dashed) drawn above the page."""
        self.overlays = overlays
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        for rect, color, dashed in self.overlays:
            x0, y0, x1, y1 = _apply_rect(self._to_view, rect)
            pen = QPen(color, 2, Qt.DashLine if dashed else Qt.SolidLine)
            painter.setPen(pen)
            fill = QColor(color)
            fill.setAlpha(40)
            painter.setBrush(fill)
            painter.drawRect(
                QRectF(
                    x0 * self.zoom,
                    y0 * self.zoom,
                    (x1 - x0) * self.zoom,
                    (y1 - y0) * self.zoom,
                )
            )
        if self._press is not None and self._drag is not None:
            painter.setPen(QPen(QColor("#d04000"), 1, Qt.DotLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(QRectF(self._press, self._drag).normalized())

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._press = event.position()
            self._drag = event.position()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._press is not None:
            self._drag = event.position()
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.LeftButton or self._press is None:
            return
        press, release = self._press, event.position()
        self._press = self._drag = None
        self.update()
        box = QRectF(press, release).normalized()
        moved = (release - press).toPoint().manhattanLength()
        if moved < QApplication.startDragDistance():
            self.point_clicked.emit(
                *_apply(
                    self._from_view, release.x() / self.zoom, release.y() / self.zoom
                )
            )
        else:
            self.area_selected.emit(
                *_apply_rect(
                    self._from_view,
                    (
                        box.left() / self.zoom,
                        box.top() / self.zoom,
                        box.right() / self.zoom,
                        box.bottom() / self.zoom,
                    ),
                )
            )


class _MatchSignals(QObject):
    ready = Signal(object)
    failed = Signal(str)
    finished = Signal()


class _MatchJob(QRunnable):
    """Find local wording candidates without blocking the review dialog."""

    def __init__(
        self, items: list[AnnotationItem], source: TextIndex, target: TextIndex
    ) -> None:
        super().__init__()
        self.items, self.source, self.target = items, source, target
        self.signals = _MatchSignals()
        self.cancelled = Event()

    def run(self) -> None:
        try:
            result = wording_matches(
                self.items,
                self.source,
                self.target,
                self.cancelled.is_set,
            )
            if not self.cancelled.is_set():
                self.signals.ready.emit(result)
        except (OSError, RuntimeError, ValueError) as exc:
            if not self.cancelled.is_set():
                self.signals.failed.emit(str(exc))
        finally:
            self.signals.finished.emit()


class AnnotationMergeDialog(QDialog):
    """Review each source annotation and write a verified merged copy.

    The target PDF is canonical and is never modified. Each source annotation
    is placed on the target, or, if its type cannot be copied, explicitly
    waived. The copy is saved to a new path chosen by the user.
    """

    copy_saved = Signal(str)
    originals_trashed = Signal(object)

    def __init__(
        self, target: Path, source: Path, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Merge annotations into a new copy")
        self.resize(1400, 860)
        self.target, self.source = target, source
        self.items: list[AnnotationItem] = enumerate_annotations(source)
        self.visible_items: list[AnnotationItem] = []
        self._hide_source_links = False
        self.placements: dict[str, Placement] = {}
        self.waived: set[str] = set()
        self.suggestions: dict[str, Placement | None] = {}
        self._index: TextIndex | None = None
        self._source_index: TextIndex | None = None
        self.matches: dict[str, list[TextMatch]] = {}
        self._wording_checked: set[str] = set()
        self._match_job: _MatchJob | None = None
        self._closed = False
        self._target_rotation = 0
        self.report: MergeReport | None = None
        self._target_page = 0
        self._target_rect: _RectTuple = (0, 0, 0, 0)

        self.item_list = QListWidget(self)
        self.item_list.setMinimumWidth(300)
        self.item_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.item_list.setWordWrap(True)
        self.item_list.setTextElideMode(Qt.ElideRight)
        self.detail = QLabel(self)
        self.detail.setWordWrap(True)
        self.source_view, self.target_view = PdfPageView(), PdfPageView()
        self.target_spin = QSpinBox(self)
        self.target_spin.setPrefix("Target page ")
        self.target_spin.setMinimum(1)
        self.target_spin.setMaximum(self._page_count(target))
        self.target_spin.installEventFilter(self)
        self.target_spin.lineEdit().installEventFilter(self)
        self.suggest_button = QPushButton("Accept placement", self)
        self.undo_button = QPushButton("Undo decision", self)
        self.undo_button.setToolTip(
            "Return this annotation to review without changing either PDF."
        )
        self.undo_button.hide()
        self.next_button = QPushButton("Accept && Next", self)
        self.next_button.setShortcut("Ctrl+Return")
        self.next_button.setToolTip(
            "Accept this location and review the next unresolved annotation (Ctrl+Return)."
        )
        self.next_button.setDefault(True)
        font = self.next_button.font()
        font.setBold(True)
        self.next_button.setFont(font)
        self.suggest_button.setFlat(True)
        self.skip_button = QPushButton("Next unresolved", self)
        self.candidates = QComboBox(self)
        self.candidates.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.candidates.setMinimumContentsLength(15)
        self.previous_candidate = QPushButton("‹", self)
        self.next_candidate = QPushButton("›", self)
        for button in (self.previous_candidate, self.next_candidate):
            button.setFixedWidth(button.fontMetrics().horizontalAdvance("›") * 4)
            button.setAutoDefault(False)
        self.previous_candidate.setToolTip("Previous candidate or search occurrence")
        self.next_candidate.setToolTip("Next candidate or search occurrence")
        self.source_excerpt, self.target_excerpt = (
            QTextBrowser(self),
            QTextBrowser(self),
        )
        for excerpt in (self.source_excerpt, self.target_excerpt):
            excerpt.setFixedHeight(
                excerpt.fontMetrics().lineSpacing() * 5 + excerpt.frameWidth() * 2
            )
            excerpt.setOpenExternalLinks(False)
        self._manual_navigation = False
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Find target text…")
        self.search.installEventFilter(self)
        self.search_button = QPushButton("Find", self)
        self.retry_button = QPushButton("Retry wording search", self)
        self.match_status = QLabel(self)
        self.match_status.setWordWrap(True)
        self.fit_mode = QComboBox(self)
        self.fit_mode.addItems(["Fit width", "Fit page", "100%"])
        self.fit_mode.setToolTip("Page display scale for both panes.")
        self.show_links_button = QPushButton("Show source hyperlinks", self)
        self._show_links = False
        self.waive_button = QPushButton("Waive (do not copy)…", self)
        link_count = sum(item.subtype == "Link" for item in self.items)
        self.exclude_links_button = QPushButton(
            f"Exclude {link_count} source hyperlinks…", self
        )
        self.exclude_links_button.setEnabled(link_count > 0)
        self.show_links_button.setEnabled(link_count > 0)
        self.exclude_links_button.setToolTip(
            "Retain all target hyperlinks and explicitly waive source hyperlinks."
        )
        self.save_button = QPushButton("Save merged copy…", self)
        self.close_button = QPushButton("Close", self)
        self.cleanup_button = QPushButton("Remove original PDFs…", self)
        self.cleanup_button.setToolTip(
            "Choose originals to move to Trash after a complete, verified merge."
        )
        self.cleanup_button.hide()
        self.status = QLabel(self)
        self.status.setWordWrap(True)

        panels = QHBoxLayout()
        panels.setContentsMargins(0, 0, 0, 0)
        self.source_scroll, self.target_scroll = QScrollArea(self), QScrollArea(self)
        self.target_scroll.viewport().installEventFilter(self)
        self.target_view.installEventFilter(self)
        for title, path, view, scroll, excerpt in (
            (
                "Source · annotations to copy",
                self.source,
                self.source_view,
                self.source_scroll,
                self.source_excerpt,
            ),
            (
                "Target · pages to keep",
                self.target,
                self.target_view,
                self.target_scroll,
                self.target_excerpt,
            ),
        ):
            column = QVBoxLayout()
            heading = QLabel(f"{title}: {path.name}", self)
            heading.setToolTip(str(path))
            heading.setWordWrap(True)
            heading.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            column.addWidget(heading)
            column.addWidget(excerpt)
            scroll.setWidget(view)
            view.attach_scroll(scroll)
            column.addWidget(scroll)
            panels.addLayout(column, 1)
        side = QVBoxLayout()
        side.setContentsMargins(0, 0, 0, 0)
        reader_count = sum(
            item.subtype != "Link" and not item.companion for item in self.items
        )
        side.addWidget(
            QLabel(
                f"{reader_count} source annotations; {link_count} source hyperlinks",
                self,
            )
        )
        side.addWidget(self.exclude_links_button)
        side.addWidget(self.show_links_button)
        side.addWidget(self.item_list, 1)
        side.addWidget(self.detail)
        candidate_row = QHBoxLayout()
        candidate_row.addWidget(self.previous_candidate)
        candidate_row.addWidget(self.candidates, 1)
        candidate_row.addWidget(self.next_candidate)
        side.addLayout(candidate_row)
        actions = QHBoxLayout()
        actions.addWidget(self.suggest_button)
        actions.addWidget(self.undo_button)
        actions.addWidget(self.next_button)
        side.addLayout(actions)
        review_row = QHBoxLayout()
        review_row.addWidget(self.skip_button)
        review_row.addWidget(self.waive_button)
        side.addLayout(review_row)
        find_row = QHBoxLayout()
        find_row.addWidget(self.search)
        find_row.addWidget(self.search_button)
        side.addLayout(find_row)
        side.addWidget(self.match_status)
        side.addWidget(self.retry_button)
        side.addWidget(self.status)
        side.addWidget(self.cleanup_button)
        finish_row = QHBoxLayout()
        finish_row.addWidget(self.save_button)
        finish_row.addWidget(self.close_button)
        side.addLayout(finish_row)
        controls, pair = QWidget(self), QWidget(self)
        controls.setLayout(side)
        controls.setMinimumWidth(300)
        document_layout = QVBoxLayout()
        document_layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Both pages:", self))
        toolbar.addWidget(self.fit_mode)
        toolbar.addStretch()
        toolbar.addWidget(self.target_spin)
        document_layout.addLayout(toolbar)
        document_layout.addLayout(panels, 1)
        pair.setLayout(document_layout)
        self.splitter = QSplitter(Qt.Horizontal, self)
        self.splitter.addWidget(controls)
        self.splitter.addWidget(pair)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([300, 1100])
        layout = QHBoxLayout(self)
        layout.addWidget(self.splitter)

        self.item_list.currentRowChanged.connect(self.select_item)
        self.target_spin.valueChanged.connect(self._target_page_changed)
        self.target_spin.editingFinished.connect(self._mark_manual_navigation)
        self.suggest_button.clicked.connect(self.accept_suggestion)
        self.undo_button.clicked.connect(self.undo_decision)
        self.next_button.clicked.connect(self.accept_and_next)
        self.skip_button.clicked.connect(self.next_unresolved)
        self.candidates.currentIndexChanged.connect(self._candidate_changed)
        self.previous_candidate.clicked.connect(
            lambda: self.candidates.setCurrentIndex(self.candidates.currentIndex() - 1)
        )
        self.next_candidate.clicked.connect(
            lambda: self.candidates.setCurrentIndex(self.candidates.currentIndex() + 1)
        )
        self.search_button.clicked.connect(self.find_target_text)
        self.search.returnPressed.connect(self.find_target_text)
        self.retry_button.clicked.connect(self.start_sentence_matching)
        self.show_links_button.clicked.connect(self.toggle_links)
        self.fit_mode.currentTextChanged.connect(self.source_view.set_fit_mode)
        self.fit_mode.currentTextChanged.connect(self.target_view.set_fit_mode)
        self.finished.connect(self._cancel_matching)
        self.waive_button.clicked.connect(lambda: self.waive_current())
        self.exclude_links_button.clicked.connect(lambda: self.exclude_source_links())
        self.save_button.clicked.connect(lambda: self.save_copy())
        self.cleanup_button.clicked.connect(self.cleanup_originals)
        self.close_button.clicked.connect(self.reject)
        self.target_view.point_clicked.connect(self.place_point)
        self.target_view.area_selected.connect(self.place_area)
        self._fill_list()
        self._target_page_changed(1)
        if self.items:
            self.item_list.setCurrentRow(0)
            self.select_item(0)
        QTimer.singleShot(0, self.start_sentence_matching)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched in (
            self.target_spin,
            self.target_spin.lineEdit(),
            self.target_view,
            self.target_scroll.viewport(),
        ) and event.type() in (QEvent.KeyPress, QEvent.MouseButtonPress, QEvent.Wheel):
            self._manual_navigation = True
        if (
            watched is self.search
            and event.type() == QEvent.KeyPress
            and event.key() in (Qt.Key_Return, Qt.Key_Enter)
            and event.modifiers() == Qt.NoModifier
        ):
            self.find_target_text()
            return True
        return super().eventFilter(watched, event)

    def _word_index(self) -> TextIndex:
        """Read the target's words once, when the first lookup needs them."""
        if self._index is None:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                self._index = TextIndex(self.target)
            finally:
                QApplication.restoreOverrideCursor()
        return self._index

    @staticmethod
    def _page_count(path: Path) -> int:
        doc = import_fitz().open(str(path))
        try:
            return int(doc.page_count)
        finally:
            doc.close()

    @property
    def current(self) -> AnnotationItem | None:
        """Return the selected source annotation."""
        row = self.item_list.currentRow()
        return self.visible_items[row] if 0 <= row < len(self.visible_items) else None

    def item_state(self, item: AnnotationItem) -> str:
        """Return a short status for `item`."""
        if item.companion:
            return "popup (content is on its parent)"
        if item.item_id in self.waived:
            return "waived"
        if item.item_id in self.placements:
            return f"placed on page {self.placements[item.item_id].page + 1}"
        if not item.supported:
            return "unsupported: waive to leave out"
        matches = self.matches.get(item.item_id, [])
        if matches:
            return "suggested" if len(matches) == 1 else f"{len(matches)} candidates"
        if item.item_id in self._wording_checked:
            return "no wording candidate"
        return "not placed"

    def _fill_list(self) -> None:
        self.item_list.blockSignals(True)
        previous = self.current
        self.item_list.clear()
        self.visible_items = [
            item
            for item in self.items
            if (not item.companion)
            and (item.subtype != "Link" or self._show_links)
            and not (self._hide_source_links and item.subtype == "Link")
        ]
        for item in self.visible_items:
            label = f"p{item.page + 1} {item.subtype}  [{self.item_state(item)}]"
            if item.text:
                label += f"\n    {item.text[:60]}"
            row_item = QListWidgetItem(label, self.item_list)
            row_item.setToolTip(item.text or item.content or item.reason)
        row = (
            self.visible_items.index(previous) if previous in self.visible_items else 0
        )
        self.item_list.setCurrentRow(row)
        self.item_list.blockSignals(False)
        open_count = sum(
            1
            for i in self.items
            if not i.companion
            and i.subtype != "Link"
            and i.item_id not in self.placements
            and i.item_id not in self.waived
        )
        pending_links = sum(
            i.subtype == "Link" and i.item_id not in self.waived for i in self.items
        )
        status = (
            f"{open_count} annotation(s) to review."
            if open_count
            else "All reader annotations are resolved."
        )
        if pending_links:
            status += f" {pending_links} source links still need an exclusion decision."
        self.status.setText(status)

    def select_item(self, row: int) -> None:
        """Show the selected annotation on the source page and offer a placement."""
        item = self.current
        if item is None:
            return
        self._manual_navigation = False
        self._show_source(item)
        if item.item_id not in self.matches:
            if self._source_index is None:
                self._source_index = TextIndex(self.source)
            evidence = source_evidence(item, self._source_index)
            query = evidence["marked_text"]
            offset = evidence["offset"] if item.kind == "point" else (0.0, 0.0)
            self.matches[item.item_id] = text_matches(
                item, self._word_index(), query, offset, evidence["words"]
            )
        matches = self.matches[item.item_id]
        self.candidates.blockSignals(True)
        self.candidates.clear()
        for n, match in enumerate(matches):
            self.candidates.addItem(
                f"{n + 1}/{len(matches)} · page {match.placement.page + 1} · {match.method}"
            )
        self.candidates.setEnabled(bool(matches))
        if (
            matches
            and item.item_id not in self.placements
            and item.item_id not in self.waived
        ):
            self.item_list.currentItem().setText(
                f"p{item.page + 1} {item.subtype}  [{self.item_state(item)}]\n    {item.text[:60]}"
            )
        placed = self.placements.get(item.item_id)
        if placed:
            accepted_row = next(
                (n for n, match in enumerate(matches) if match.placement == placed), 0
            )
            self.candidates.setCurrentIndex(accepted_row)
        self.candidates.blockSignals(False)
        self.suggestions[item.item_id] = (
            matches[self.candidates.currentIndex()].placement if matches else None
        )
        if placed is not None:
            self.target_spin.setValue(placed.page + 1)
        elif matches:
            self.target_spin.setValue(matches[0].placement.page + 1)
        elif item.supported:
            self.target_spin.setValue(min(item.page + 1, self.target_spin.maximum()))
        self.waive_button.setEnabled(
            not item.companion and item.item_id not in self.waived
        )
        self.detail.setText(self._describe(item, self.suggestions[item.item_id]))
        self._candidate_changed(self.candidates.currentIndex())

    def _describe(self, item: AnnotationItem, suggestion: Placement | None) -> str:
        lines = [
            f"{item.subtype} · source page {item.page + 1} · {self.item_state(item)}"
        ]
        if not item.supported:
            lines.append(item.reason)
        elif item.kind == "markup":
            lines.append("Review a candidate, or drag across target words.")
        else:
            lines.append("Review the anchor, or click the target position.")
        return "\n".join(lines)

    def _candidate_changed(self, row: int) -> None:
        item = self.current
        if item is None:
            return
        matches = self.matches.get(item.item_id, [])
        match = matches[row] if 0 <= row < len(matches) else None
        self.suggestions[item.item_id] = match.placement if match else None
        self.previous_candidate.setEnabled(row > 0)
        self.next_candidate.setEnabled(0 <= row < len(matches) - 1)
        self.candidates.setToolTip(self.candidates.currentText())
        placed = self.placements.get(item.item_id)
        decided = placed is not None or item.item_id in self.waived
        self.undo_button.setVisible(decided)
        self.suggest_button.setVisible(not decided)
        self.candidates.setEnabled(bool(matches) and not decided)
        self.previous_candidate.setEnabled(row > 0 and not decided)
        self.next_candidate.setEnabled(0 <= row < len(matches) - 1 and not decided)
        if placed is not None:
            if placed.rects:
                words = self._word_index().pages[placed.page]
                text = " ".join(
                    str(w[4])
                    for w in words
                    if any(
                        r[0] <= (w[0] + w[2]) / 2 <= r[2]
                        and r[1] <= (w[1] + w[3]) / 2 <= r[3]
                        for r in placed.rects
                    )
                )
            else:
                text = f"{item.subtype} placed on page {placed.page + 1}."
            match = TextMatch(placed, text, "Accepted placement")
        if match and placed is None:
            self.target_spin.setValue(match.placement.page + 1)
        enabled = (
            match is not None and placed is None and item.item_id not in self.waived
        )
        self.suggest_button.setEnabled(enabled)
        self.next_button.setEnabled(enabled)
        source_text = (
            (match.source_text if match else "")
            or item.text
            or item.content
            or "No marked text; use the source page as evidence."
        )
        source, target = (
            escape(source_text),
            "No candidate. Find target text or place manually.",
        )
        if match and match.operations:
            source_parts, target_parts = [], []
            for tag, left, right in match.operations:
                a, b = escape(left), escape(right)
                if tag != "equal":
                    a = f'<span style="background-color:#ffe0c0;color:#402000">{a}</span>'
                    b = f'<span style="background-color:#d5ecff;color:#002040">{b}</span>'
                source_parts.append(a)
                target_parts.append(b)
            source, target = " ".join(source_parts), " ".join(target_parts)
        elif match:
            left, right = source_text.split(), match.text.split()
            source_parts, target_parts = [], []
            for tag, a0, a1, b0, b1 in SequenceMatcher(
                None, left, right, autojunk=False
            ).get_opcodes():
                a, b = escape(" ".join(left[a0:a1])), escape(" ".join(right[b0:b1]))
                if tag != "equal":
                    a = f'<span style="background-color:#ffe0c0;color:#402000">{a}</span>'
                    b = f'<span style="background-color:#d5ecff;color:#002040">{b}</span>'
                source_parts.append(a)
                target_parts.append(b)
            source, target = " ".join(source_parts), " ".join(target_parts)
        method = escape(match.method) if match else ""
        if match and match.edits is not None:
            method += f" · {match.edits} normalized word edit(s) · {match.matched_words}/{match.source_words} source words matched"
            missing = [left for tag, left, _ in match.operations if tag == "delete"]
            if missing:
                method += "; source words without a target match: " + escape(
                    " ".join(missing)
                )
            self.target_excerpt.setToolTip(
                "Text similarity does not establish the same scientific meaning. Review the wording and boundaries."
            )
        else:
            self.target_excerpt.setToolTip("")
        comment = (
            f"<p><b>Comment</b><br>{escape(item.content)}</p>" if item.content else ""
        )
        self.source_excerpt.setHtml(f"<b>Source selection</b><br>{source}{comment}")
        heading = "Accepted target placement" if placed else "Target candidate"
        if item.item_id in self.waived:
            heading, target, method = (
                "Excluded annotation",
                "This annotation will not be copied.",
                "Use Undo decision to return it to review.",
            )
        context = ""
        if match and match.context and placed is None:
            context = (
                f"<p><b>Surrounding target text</b><br>{escape(match.context)}</p>"
            )
        self.target_excerpt.setHtml(
            f"<b>{heading}</b><br>{target}<p>{method}</p>{context}"
        )
        self._draw_target()
        self._focus_target()

    def _focus_target(self) -> None:
        item = self.current
        if item is None:
            return
        placement = self.placements.get(item.item_id) or self.suggestions.get(
            item.item_id
        )
        if placement is None or placement.page != self._target_page:
            return
        if placement.rects:
            boxes = placement.rects
            rect = (
                min(r[0] for r in boxes),
                min(r[1] for r in boxes),
                max(r[2] for r in boxes),
                max(r[3] for r in boxes),
            )
        elif placement.point:
            x, y = placement.point
            rect = (
                x,
                y,
                x + item.rect[2] - item.rect[0],
                y + item.rect[3] - item.rect[1],
            )
        else:
            return
        self.target_view.focus_rect(rect)

    def find_target_text(self) -> None:
        """Show all target occurrences of the reader's search text."""
        item, query = self.current, self.search.text().strip()
        if item is None or not query:
            return
        self.matches[item.item_id] = text_matches(item, self._word_index(), query)
        self._fill_list()
        self.select_item(self.item_list.currentRow())
        self.match_status.setText(
            f"Found {len(self.matches[item.item_id])} text occurrence(s). Review before accepting."
        )

    def toggle_links(self) -> None:
        """Show source hyperlinks separately from the annotation review queue."""
        self._show_links = not self._show_links
        self.show_links_button.setText(
            "Hide source hyperlinks" if self._show_links else "Show source hyperlinks"
        )
        self._fill_list()
        self.select_item(self.item_list.currentRow())

    def start_sentence_matching(self) -> None:
        """Find closest wording locally while the reader continues reviewing."""
        if self._closed or self._match_job is not None:
            return
        index = self._word_index()
        if self._source_index is None:
            self._source_index = TextIndex(self.source)
        pending = []
        for item in self.items:
            if (
                not item.supported
                or item.item_id in self.placements
                or item.item_id in self.waived
            ):
                continue
            if item.item_id not in self.matches:
                evidence = source_evidence(item, self._source_index)
                self.matches[item.item_id] = text_matches(
                    item,
                    index,
                    evidence["marked_text"],
                    evidence["offset"],
                    evidence["words"],
                )
            if not self.matches[item.item_id]:
                pending.append(item)
        self._fill_list()
        if not pending:
            self.match_status.setText("Text candidates found. Review each placement.")
            return
        job = _MatchJob(pending, self._source_index, index)
        self._match_job = job
        job.signals.ready.connect(self._sentences_ready)
        job.signals.failed.connect(self._sentences_failed)
        job.signals.finished.connect(self._sentences_finished)
        self.retry_button.setEnabled(False)
        self.match_status.setText(
            f"Finding wording candidates locally for {len(pending)} annotation(s)… You can continue reviewing."
        )
        QThreadPool.globalInstance().start(job)

    def _mark_manual_navigation(self) -> None:
        self._manual_navigation = True

    def _sentences_ready(self, results: dict[str, list[TextMatch]]) -> None:
        if self._closed or self._match_job is None:
            return
        added = set()
        for item in self._match_job.items:
            self._wording_checked.add(item.item_id)
            if item.item_id in results and not self.matches.get(item.item_id):
                self.matches[item.item_id] = results[item.item_id]
                added.add(item.item_id)
        self._fill_list()
        if (
            self.current
            and self.current.item_id in added
            and not self._manual_navigation
        ):
            self.select_item(self.item_list.currentRow())
        self.match_status.setText(
            f"Local wording candidates found for {len(results)} annotation(s). Similar wording can have different meaning. Review before accepting."
        )

    def _sentences_failed(self, message: str) -> None:
        if not self._closed:
            self.match_status.setText(
                f"Local text search could not finish: {message}. Select Retry wording search, or place the annotation manually."
            )

    def _sentences_finished(self) -> None:
        self._match_job = None
        if not self._closed:
            self.retry_button.setEnabled(True)

    def _cancel_matching(self, _result: int) -> None:
        self._closed = True
        if self._match_job is not None:
            self._match_job.cancelled.set()

    def next_unresolved(self) -> None:
        """Select the next annotation that still needs a placement or waiver."""
        count, start = len(self.visible_items), self.item_list.currentRow()
        for step in range(1, count + 1):
            row = (start + step) % count
            item = self.visible_items[row]
            if item.item_id not in self.placements and item.item_id not in self.waived:
                self.item_list.setCurrentRow(row)
                return
        self.status.setText(
            "Every visible annotation is resolved. Save the merged copy when ready."
        )

    def accept_and_next(self) -> None:
        """Accept the visible candidate and review the next unresolved item."""
        if self.accept_suggestion() is None:
            self.next_unresolved()

    def _show_source(self, item: AnnotationItem) -> None:
        self.source_view.show_page(self.source, item.page)
        self.source_view.set_overlays([(item.rect, QColor("#c00000"), False)])
        self.source_view.focus_rect(item.rect)

    def _target_page_changed(self, value: int) -> None:
        self._target_page = value - 1
        self._target_rect = self.target_view.show_page(self.target, self._target_page)
        self._target_rotation = self._rotation_of(self.target, self._target_page)
        self._draw_target()
        self._focus_target()

    @staticmethod
    def _rotation_of(path: Path, page_number: int) -> int:
        doc = import_fitz().open(str(path))
        try:
            return int(doc[page_number].rotation)
        finally:
            doc.close()

    def _draw_target(self) -> None:
        item = self.current
        overlays: list[tuple[_RectTuple, QColor, bool]] = []
        if item is not None and item.supported:
            placed = self.placements.get(item.item_id)
            suggestion = self.suggestions.get(item.item_id)
            for entry, dashed in (
                (placed, False),
                (suggestion if placed is None else None, True),
            ):
                if entry is None or entry.page != self._target_page:
                    continue
                if entry.rects:
                    overlays += [(r, QColor("#0060c0"), dashed) for r in entry.rects]
                elif entry.point is not None:
                    dx, dy = (
                        entry.point[0] - item.rect[0],
                        entry.point[1] - item.rect[1],
                    )
                    r = item.rect
                    overlays.append(
                        (
                            (r[0] + dx, r[1] + dy, r[2] + dx, r[3] + dy),
                            QColor("#0060c0"),
                            dashed,
                        )
                    )
        self.target_view.set_overlays(overlays)

    def _commit(self, item: AnnotationItem, placement: Placement) -> str | None:
        problem = placement_error(
            item, placement, self._target_rect, self._target_rotation
        )
        if problem is None:
            self.placements[item.item_id] = placement
            self.waived.discard(item.item_id)
            self._fill_list()
            self.select_item(self.item_list.currentRow())
        return problem

    def undo_decision(self) -> None:
        """Return the current placement or waiver to the review queue."""
        item = self.current
        if item is None:
            return
        self.placements.pop(item.item_id, None)
        self.waived.discard(item.item_id)
        self._fill_list()
        self.select_item(self.item_list.currentRow())

    def accept_suggestion(self) -> str | None:
        """Accept the currently displayed text or sentence candidate."""
        item = self.current
        suggestion = self.suggestions.get(item.item_id) if item else None
        if item is None or suggestion is None:
            return "No suggestion."
        self.target_spin.setValue(suggestion.page + 1)
        problem = self._commit(item, suggestion)
        if problem:
            self.status.setText(problem)
        return problem

    def place_area(self, x0: float, y0: float, x1: float, y1: float) -> str | None:
        """Place the selected markup on the target words inside the dragged box."""
        item = self.current
        if item is None or item.kind != "markup":
            return "Select a markup annotation first."
        fitz = import_fitz()
        doc = fitz.open(str(self.target))
        try:
            words = words_inside(doc[self._target_page], (x0, y0, x1, y1))
        finally:
            doc.close()
        problem = self._commit(
            item, Placement(item.item_id, self._target_page, rects=line_rects(words))
        )
        if problem:
            self.status.setText(problem)
        else:
            self.target_excerpt.setPlainText(
                "Manual placement accepted. The solid blue boxes show the words that will be copied."
            )
        return problem

    def place_point(self, x: float, y: float) -> str | None:
        """Place the selected note or shape with its top-left corner at (x, y)."""
        item = self.current
        if item is None or item.kind != "point":
            return "Select a note or shape first."
        problem = self._commit(
            item, Placement(item.item_id, self._target_page, point=(x, y))
        )
        if problem:
            self.status.setText(problem)
        else:
            self.target_excerpt.setPlainText(
                "Manual position accepted. Review the solid blue outline before saving."
            )
        return problem

    def exclude_source_links(self, confirmed: bool | None = None) -> int:
        """Waive source hyperlinks together after an explicit user decision."""
        links = [
            item
            for item in self.items
            if item.subtype == "Link" and item.item_id not in self.waived
        ]
        if not links:
            return 0
        if confirmed is None:
            confirmed = (
                QMessageBox.question(
                    self,
                    "Keep the target PDF's hyperlinks",
                    f"Exclude {len(links)} hyperlinks from the source PDF?\n\n"
                    "All hyperlinks already in the target PDF are retained. "
                    "Highlights, underlines, notes, and other source annotations still require review. "
                    "The excluded hyperlinks will be listed as waived in the merge report.",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                == QMessageBox.Yes
            )
        if not confirmed:
            return 0
        self.waived.update(item.item_id for item in links)
        self._hide_source_links = True
        self.exclude_links_button.setText(f"{len(links)} source hyperlinks excluded")
        self.exclude_links_button.setEnabled(False)
        self._fill_list()
        self.select_item(self.item_list.currentRow())
        return len(links)

    def waive_current(self, confirmed: bool | None = None) -> bool:
        """Exclude the selected annotation after explicit confirmation."""
        item = self.current
        if item is None or item.companion:
            return False
        if confirmed is None:
            confirmed = (
                QMessageBox.question(
                    self,
                    "Waive annotation",
                    f"{item.subtype} on source page {item.page + 1}.\n\nIt will NOT be copied to the merged file. "
                    "Waive this annotation?",
                )
                == QMessageBox.Yes
            )
        if not confirmed:
            return False
        self.placements.pop(item.item_id, None)
        self.waived.add(item.item_id)
        self._fill_list()
        self.select_item(self.item_list.currentRow())
        return True

    def save_copy(self, output: Path | None = None) -> MergeReport | None:
        """Write the merged copy to a new path and show an honest summary."""
        unresolved = [
            i
            for i in self.items
            if not i.companion
            and i.item_id not in self.placements
            and i.item_id not in self.waived
        ]
        if output is None:
            name, _ = QFileDialog.getSaveFileName(
                self,
                "Save merged copy",
                str(self.target.with_name(self.target.stem + "-merged.pdf")),
                "PDF (*.pdf)",
                options=QFileDialog.DontConfirmOverwrite,
            )
            if not name:
                return None
            output = Path(name)
        if unresolved and not self._confirm_partial(len(unresolved)):
            return None
        try:
            self.report = build_merged_copy(
                self.target,
                self.source,
                self.items,
                self.placements,
                self.waived,
                output,
            )
        except AnnotationMergeError as exc:
            QMessageBox.warning(self, "Cannot save merged copy", str(exc))
            self.status.setText(str(exc))
            return None
        self.copy_saved.emit(self.report.output_path)
        self.status.setText(self.report.summary())
        self.cleanup_button.setVisible(self.report.complete)
        self._show_saved_result()
        return self.report

    def _show_saved_result(self) -> None:
        """Offer cleanup only after a complete merged copy is verified."""
        if self.report is None:
            return
        if not self.report.complete:
            QMessageBox.information(self, "Merged copy saved", self.report.summary())
            return
        message = QMessageBox(self)
        message.setWindowTitle("Merged copy saved")
        message.setIcon(QMessageBox.Information)
        message.setText("The complete merged PDF is saved and verified.")
        message.setInformativeText(
            "Keep the originals, or choose which original PDFs to move to Trash."
        )
        message.setDetailedText(self.report.summary())
        keep = message.addButton("Keep originals", QMessageBox.RejectRole)
        cleanup = message.addButton("Remove original PDFs…", QMessageBox.ActionRole)
        message.setDefaultButton(keep)
        message.exec()
        if message.clickedButton() is cleanup:
            self.cleanup_originals()

    def cleanup_originals(self) -> None:
        """Let the reader remove originals after verifying the saved output."""
        from .merge_cleanup import MergeCleanupDialog

        if self.report is None or not self.report.complete:
            self.status.setText(
                "Finish and save the merge before removing original PDFs."
            )
            return
        cleanup = MergeCleanupDialog(self.report, self)
        cleanup.exec()
        if cleanup.trashed_paths:
            self.originals_trashed.emit(tuple(cleanup.trashed_paths))
            # The review pages may now be in Trash. Return to the library.
            self.accept()

    def _confirm_partial(self, count: int) -> bool:
        return (
            QMessageBox.question(
                self,
                "Unresolved annotations",
                f"{count} annotation(s) are neither placed nor waived. The saved "
                "copy will be a partial merge, not a full consolidation. Save it anyway?",
            )
            == QMessageBox.Yes
        )
