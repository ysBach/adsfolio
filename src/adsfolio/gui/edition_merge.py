"""Choose PDF editions using shared identifiers and explicit user review."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ..pdf.arxiv import normalize_arxiv_id
from ..pdf.upgrade_utils import _normalize_doi, is_arxiv_bibcode, is_arxiv_doi
from ..pdf.watch_session import WatchEvent


def edition_identifiers(record: Mapping[str, Any]) -> set[tuple[str, str]]:
    """Collect exact ADS, DOI, and version-free arXiv identifiers for a PDF."""
    identifiers: set[tuple[str, str]] = set()
    layers = [record]
    for key in ("metadata", "raw", "ads"):
        value = record.get(key)
        if isinstance(value, Mapping):
            layers.append(value)
            raw = value.get("raw")
            if isinstance(raw, Mapping):
                layers.append(raw)
    for layer in layers:
        for key in (
            "bibcode",
            "alternate_bibcode",
            "identifier",
            "doi",
            "arxiv",
            "arxiv_id",
        ):
            values = layer.get(key) or []
            if not isinstance(values, (list, tuple)):
                values = [values]
            for value in values:
                text = str(value).strip()
                if not text:
                    continue
                if key in {"bibcode", "alternate_bibcode"} or re.fullmatch(
                    r"\d{4}\S{15}", text
                ):
                    identifiers.add(("bibcode", text.casefold()))
                doi = _normalize_doi(text)
                if doi.startswith("10."):
                    identifiers.add(("doi", doi))
                arxiv = normalize_arxiv_id(text)
                if arxiv:
                    identifiers.add(("arxiv", arxiv))
    return identifiers


def edition_role(record: Mapping[str, Any]) -> str:
    """Classify a stored publication identifier as preprint, published, or unknown."""
    metadata = record.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    bibcode = metadata.get("bibcode") or record.get("bibcode") or ""
    if isinstance(bibcode, (list, tuple)):
        bibcode = next(iter(bibcode), "")
    if bibcode:
        return "preprint" if is_arxiv_bibcode(str(bibcode)) else "published"
    identifiers = edition_identifiers(record)
    if any(kind == "doi" and not is_arxiv_doi(value) for kind, value in identifiers):
        return "published"
    if any(kind == "arxiv" for kind, _ in identifiers):
        return "preprint"
    return "unknown"


def edition_candidates(
    arrival: Mapping[str, Any], records: Sequence[dict[str, Any]]
) -> tuple[tuple[dict[str, Any], ...], bool]:
    """Find identifier-linked files, or offer an explicitly unlinked edition picker."""
    path = arrival.get("current_path") or arrival.get("relative_path")
    others = [
        record
        for record in records
        if (record.get("current_path") or record.get("relative_path")) != path
        and not record.get("missing_at")
    ]
    identifiers = edition_identifiers(arrival)
    linked = tuple(
        record for record in others if identifiers & edition_identifiers(record)
    )
    if linked:
        return linked, True
    role = edition_role(arrival)
    opposite = {"published": "preprint", "preprint": "published"}.get(role)
    return tuple(
        record for record in others if opposite and edition_role(record) == opposite
    ), False


@dataclass(frozen=True)
class EditionMergeRequest:
    """Keep a merge review tied to its library, files, and watcher arrival."""

    root: Path
    records: tuple[dict[str, Any], ...]
    target: Path
    choose_source: bool = False
    linked: bool = False
    event: WatchEvent | None = None


class EditionMergeDialog(QDialog):
    """Confirm source and target before reviewing any annotation transfers."""

    def __init__(
        self, request: EditionMergeRequest, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Merge PDF editions")
        self.resize(880, 300)
        self.request = request
        self.paths = tuple(
            request.root / str(record.get("current_path") or record["relative_path"])
            for record in request.records
        )
        layout = QVBoxLayout(self)
        self.explanation = QLabel(
            "These files share an ADS, DOI, or arXiv identifier. Review which PDF supplies the pages."
            if request.linked
            else "ADS has not supplied a shared identifier for these files. Select the matching editions yourself; no title-based match has been assumed.",
            self,
        )
        self.explanation.setWordWrap(True)
        layout.addWidget(self.explanation)
        form = QFormLayout()
        self.target_combo = QComboBox(self)
        self.source_combo = QComboBox(self)
        for path, record in zip(self.paths, request.records):
            self.target_combo.addItem(
                f"[{edition_role(record)}] {path.name}", str(path)
            )
        self.target_combo.setCurrentIndex(
            self.target_combo.findData(str(request.target))
        )
        form.addRow("Keep pages from (target)", self.target_combo)
        form.addRow("Copy annotations from (source)", self.source_combo)
        layout.addLayout(form)
        note = QLabel(
            "Next, review annotation placements side by side. Existing target annotations are retained. Save the result as a new PDF; both original PDFs remain intact.",
            self,
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self
        )
        self.review_button = self.buttons.button(QDialogButtonBox.Ok)
        self.review_button.setText("Review Annotations…")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.target_combo.currentIndexChanged.connect(self._update_sources)
        self.source_combo.currentIndexChanged.connect(self._update_review)
        layout.addWidget(self.buttons)
        self._update_sources()

    @property
    def target(self) -> Path | None:
        """Return the PDF chosen to supply the page layout."""
        value = self.target_combo.currentData()
        return Path(value) if value else None

    @property
    def source(self) -> Path | None:
        """Return the explicitly selected annotation source, if any."""
        value = self.source_combo.currentData()
        return Path(value) if value else None

    def _update_sources(self) -> None:
        """Exclude the target and require an explicit choice for an unlinked arrival."""
        previous = self.source
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        if self.request.choose_source:
            self.source_combo.addItem("Choose the matching source PDF…", None)
        for path in self.paths:
            if path != self.target:
                self.source_combo.addItem(path.name, str(path))
        if previous is not None:
            index = self.source_combo.findData(str(previous))
            if index >= 0:
                self.source_combo.setCurrentIndex(index)
        self.source_combo.blockSignals(False)
        self._update_review()

    def _update_review(self) -> None:
        """Enable review only when two distinct, available files have been chosen."""
        target, source = self.target, self.source
        self.review_button.setEnabled(
            target is not None
            and source is not None
            and target != source
            and target.is_file()
            and source.is_file()
        )
