"""Tests for the accessible compact resolution marker."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt

from adsfolio.config import Config
from adsfolio.gui.library import COLUMNS, FileQuery
from adsfolio.gui.models import FileTableModel
from adsfolio.pdf.registry import PdfRegistry


@pytest.fixture(autouse=True)
def _use_test_catalog(monkeypatch, tmp_path):
    monkeypatch.setattr(
        Config,
        "get_config_dir",
        classmethod(lambda cls: tmp_path / "config"),
    )


def test_resolution_marker_has_accessible_state(tmp_path) -> None:
    """The blank marker cell exposes its resolution state to assistive tools."""
    root = tmp_path / "library"
    registry = PdfRegistry(root)
    registry.upsert_file(
        root=root,
        relative_path="resolved.pdf",
        current_path="resolved.pdf",
        sha256="a",
        size_bytes=1,
        mtime_ns=1,
        status="resolved",
        metadata={"bibcode": "2024TEST...1A"},
    )
    registry.upsert_file(
        root=root,
        relative_path="needs-attention.pdf",
        current_path="needs-attention.pdf",
        sha256="b",
        size_bytes=1,
        mtime_ns=1,
        status="unresolved",
        metadata={"bibcode": "2024TEST...1B"},
    )
    model = FileTableModel(registry, FileQuery())
    status_index = next(
        index for index, column in enumerate(COLUMNS) if column.key == "status"
    )

    resolved = model.index(1, status_index)
    attention = model.index(0, status_index)
    assert resolved.data(Qt.DisplayRole) == ""
    assert resolved.data(Qt.ToolTipRole) == "ADS record linked"
    assert resolved.data(Qt.AccessibleTextRole) == "ADS record linked"
    assert attention.data(Qt.DisplayRole) == ""
    assert attention.data(Qt.ToolTipRole) == (
        "Bibcode present. ADS lookup is pending. "
        "Run Identify to check it in a batch. Existing metadata is retained."
    )
    assert attention.data(Qt.AccessibleTextRole) == (
        "Bibcode present. ADS lookup is pending. "
        "Run Identify to check it in a batch. Existing metadata is retained."
    )
    registry.close()
