"""Verify local references are accessible without configuring PDF roots."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QDialog, QMessageBox

from adsfolio.config import Config
from adsfolio.gui.main_window import MainWindow
from adsfolio.gui.references_dialog import ReferencesDialog
from adsfolio.references import LocalReferences


def test_local_references_action_without_root(
    qtbot: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    opened: list[ReferencesDialog] = []

    def capture_dialog(dialog: ReferencesDialog) -> int:
        opened.append(dialog)
        dialog.reject()
        return QDialog.Rejected

    monkeypatch.setattr(ReferencesDialog, "exec", capture_dialog)
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.current_root is None
    assert window.local_references_action.isEnabled()
    window.local_references_action.trigger()
    assert len(opened) == 1
    assert opened[0].parent() is window
    window.close()


def test_pdf_editor_restores_owned_reference_metadata_after_rejected_edit(
    qtbot: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    root = tmp_path / "pdfs"
    root.mkdir()
    pdf = root / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    window = MainWindow()
    qtbot.addWidget(window)
    window._register_root(root)
    window._select_root(root)
    registry = window.registries[str(root)]
    service = LocalReferences(registry.catalog)
    reference = service.add(cite_key="LocalReport", fields={"title": "Saved title"})
    record = service.attach_pdf(reference["id"], pdf)
    window.refresh()
    window._select_file_id(record["location_id"])
    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda parent, title, message: warnings.append(message)
    )
    metadata = dict(record["metadata"])
    metadata["title"] = ["Rejected title"]
    window._save_detail_metadata(metadata)
    assert len(warnings) == 1
    assert "Local References" in warnings[0]
    registry = window.registries[str(root)]
    service = LocalReferences(registry.catalog)
    assert service.get(reference["id"])["fields"]["title"] == "Saved title"
    assert (
        registry.find_location_by_id(record["location_id"])["document_id"]
        == reference["id"]
    )
    assert window.detail._metadata["title"] == ["Saved title"]
    window.close()
