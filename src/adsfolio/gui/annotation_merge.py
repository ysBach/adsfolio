"""Interactive review dialog for moving annotations between PDF editions."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..pdf.annotation_merge import (
    AnnotationItem,
    AnnotationMergeError,
    MergeReport,
    Placement,
    WordIndex,
    build_merged_copy,
    enumerate_annotations,
    import_fitz,
    line_rects,
    placement_error,
    suggest_exact_placement,
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
        self.setPixmap(QPixmap.fromImage(image))
        self.resize(image.size())
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
                QRectF(x0 * ZOOM, y0 * ZOOM, (x1 - x0) * ZOOM, (y1 - y0) * ZOOM)
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
                *_apply(self._from_view, release.x() / ZOOM, release.y() / ZOOM)
            )
        else:
            self.area_selected.emit(
                *_apply_rect(
                    self._from_view,
                    (
                        box.left() / ZOOM,
                        box.top() / ZOOM,
                        box.right() / ZOOM,
                        box.bottom() / ZOOM,
                    ),
                )
            )


class AnnotationMergeDialog(QDialog):
    """Review each source annotation and write a verified merged copy.

    The target PDF is canonical and is never modified. Each source annotation
    is placed on the target, or, if its type cannot be copied, explicitly
    waived. The copy is saved to a new path chosen by the user.
    """

    def __init__(
        self, target: Path, source: Path, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Merge annotations into a new copy")
        self.resize(1400, 860)
        self.target, self.source = target, source
        self.items: list[AnnotationItem] = enumerate_annotations(source)
        self.placements: dict[str, Placement] = {}
        self.waived: set[str] = set()
        self.suggestions: dict[str, Placement | None] = {}
        self._index: WordIndex | None = None
        self._target_rotation = 0
        self.report: MergeReport | None = None
        self._target_page = 0
        self._target_rect: _RectTuple = (0, 0, 0, 0)

        self.item_list = QListWidget(self)
        self.item_list.setMinimumWidth(300)
        self.detail = QLabel(self)
        self.detail.setWordWrap(True)
        self.source_view, self.target_view = PdfPageView(), PdfPageView()
        self.target_spin = QSpinBox(self)
        self.target_spin.setPrefix("Target page ")
        self.target_spin.setMinimum(1)
        self.target_spin.setMaximum(self._page_count(target))
        self.suggest_button = QPushButton("Accept exact-text suggestion", self)
        self.waive_button = QPushButton("Waive (do not copy)…", self)
        self.save_button = QPushButton("Save merged copy…", self)
        self.close_button = QPushButton("Close", self)
        self.status = QLabel(self)
        self.status.setWordWrap(True)

        panels = QHBoxLayout()
        panels.setContentsMargins(0, 0, 0, 0)
        for title, path, view in (
            ("Source (read only)", self.source, self.source_view),
            ("Target (canonical)", self.target, self.target_view),
        ):
            column = QVBoxLayout()
            heading = QLabel(f"{title}: {path.name}", self)
            heading.setToolTip(str(path))
            column.addWidget(heading)
            scroll = QScrollArea(self)
            scroll.setWidget(view)
            column.addWidget(scroll)
            panels.addLayout(column, 1)
        side = QVBoxLayout()
        side.setContentsMargins(0, 0, 0, 0)
        side.addWidget(QLabel(f"{len(self.items)} source annotations", self))
        side.addWidget(self.item_list, 1)
        side.addWidget(self.detail)
        side.addWidget(self.target_spin)
        side.addWidget(self.suggest_button)
        side.addWidget(self.waive_button)
        side.addWidget(self.save_button)
        side.addWidget(self.status)
        side.addWidget(self.close_button)
        controls, pair = QWidget(self), QWidget(self)
        controls.setLayout(side)
        controls.setMinimumWidth(300)
        pair.setLayout(panels)
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
        self.suggest_button.clicked.connect(self.accept_suggestion)
        self.waive_button.clicked.connect(self.waive_current)
        self.save_button.clicked.connect(self.save_copy)
        self.close_button.clicked.connect(self.reject)
        self.target_view.point_clicked.connect(self.place_point)
        self.target_view.area_selected.connect(self.place_area)
        self._fill_list()
        self._target_page_changed(1)
        if self.items:
            self.item_list.setCurrentRow(0)
            self.select_item(0)

    def _word_index(self) -> WordIndex:
        """Read the target's words once, when the first lookup needs them."""
        if self._index is None:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                self._index = WordIndex(self.target)
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
        return self.items[row] if 0 <= row < len(self.items) else None

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
        return "not placed"

    def _fill_list(self) -> None:
        self.item_list.blockSignals(True)
        row = self.item_list.currentRow()
        self.item_list.clear()
        for item in self.items:
            label = f"p{item.page + 1} {item.subtype}  [{self.item_state(item)}]"
            if item.text:
                label += f"\n    {item.text[:60]}"
            QListWidgetItem(label, self.item_list)
        self.item_list.setCurrentRow(max(row, 0))
        self.item_list.blockSignals(False)
        open_count = sum(
            1
            for i in self.items
            if not i.companion
            and i.item_id not in self.placements
            and i.item_id not in self.waived
        )
        self.status.setText(
            "All annotations are resolved."
            if open_count == 0
            else f"{open_count} annotation(s) are neither placed nor waived."
        )

    def select_item(self, row: int) -> None:
        """Show the selected annotation on the source page and offer a placement."""
        item = self.current
        if item is None:
            return
        self._show_source(item)
        if item.item_id not in self.suggestions:
            self.suggestions[item.item_id] = suggest_exact_placement(
                item, self._word_index()
            )
        suggestion = self.suggestions[item.item_id]
        placed = self.placements.get(item.item_id)
        if placed is not None:
            self.target_spin.setValue(placed.page + 1)
        elif suggestion is not None:
            self.target_spin.setValue(suggestion.page + 1)
        self.suggest_button.setEnabled(
            suggestion is not None and item.item_id not in self.placements
        )
        self.waive_button.setEnabled(
            item.needs_waiver and item.item_id not in self.waived
        )
        self.detail.setText(self._describe(item, suggestion))
        self._draw_target()

    def _describe(self, item: AnnotationItem, suggestion: Placement | None) -> str:
        lines = [f"{item.subtype} on source page {item.page + 1}"]
        if item.content:
            lines.append(f"Comment: {item.content}")
        if not item.supported:
            lines.append(item.reason)
        elif item.kind == "markup":
            lines.append(
                "Drag over target words to place it."
                + (
                    " The source text occurs exactly once in the target; review the dashed box and accept."
                    if suggestion
                    else " No exact unique text match was found."
                )
            )
        else:
            lines.append("Click the target position for the top-left corner.")
        return "\n".join(lines)

    def _show_source(self, item: AnnotationItem) -> None:
        self.source_view.show_page(self.source, item.page)
        self.source_view.set_overlays([(item.rect, QColor("#c00000"), False)])

    def _target_page_changed(self, value: int) -> None:
        self._target_page = value - 1
        self._target_rect = self.target_view.show_page(self.target, self._target_page)
        self._target_rotation = self._rotation_of(self.target, self._target_page)
        self._draw_target()

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

    def accept_suggestion(self) -> str | None:
        """Accept the exact-text suggestion for the selected annotation."""
        item = self.current
        suggestion = self.suggestions.get(item.item_id) if item else None
        if item is None or suggestion is None:
            return "No suggestion."
        return self._commit(item, suggestion)

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
        return problem

    def waive_current(self, confirmed: bool | None = None) -> bool:
        """Waive the selected unsupported annotation after explicit confirmation."""
        item = self.current
        if item is None or not item.needs_waiver:
            return False
        if confirmed is None:
            confirmed = (
                QMessageBox.question(
                    self,
                    "Waive annotation",
                    f"{item.reason}.\n\nIt will NOT be copied to the merged file. "
                    "Waive this annotation?",
                )
                == QMessageBox.Yes
            )
        if not confirmed:
            return False
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
        self.status.setText(self.report.summary())
        QMessageBox.information(self, "Merged copy saved", self.report.summary())
        return self.report

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
