"""One PDF among many is not on ADS: type its DOI, rename it, export BibTeX."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QApplication

from adsfolio.config import Config
from adsfolio.gui.main_window import MainWindow
from adsfolio.pdf.cli import pdf
from adsfolio.pdf.files import sha256_file
from adsfolio.pdf.registry import open_registry_state


def test_typed_doi_renames_the_pdf_and_exports_bibtex(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    root = tmp_path / "library"
    root.mkdir()
    scan = root / "scan_0042.pdf"
    scan.write_bytes(b"%PDF-1.4\n%a scanned paper\n")
    state = open_registry_state(root)
    try:
        state.upsert_file(
            scan.name,
            {
                "current_filename": scan.name,
                "sha256": sha256_file(scan),
                "status": "unresolved",
            },
        )
        state.save()
    finally:
        state.close()

    class AdsKnowsNothing:
        def query(self, *, q, fl, rows):
            return SimpleNamespace(num_found=0, docs=[])

    monkeypatch.setattr(
        "adsfolio.pdf.cli._get_client",
        lambda _ctx: SimpleNamespace(search=AdsKnowsNothing()),
    )
    monkeypatch.setattr(
        "adsfolio.pdf.identifier.fetch_crossref_doi",
        lambda doi: {
            "doi": [doi],
            "title": ["Grain Growth in Cold Clouds"],
            "author": ["Roe, Alice", "Kim, Sun"],
            "year": "2019",
            "pub": "Journal of Examples",
            "volume": "12",
            "page": ["34"],
        },
    )

    # 1. Type the DOI for the selected PDF (the app runs this same command).
    result = CliRunner().invoke(
        pdf,
        [
            "refresh-ads",
            str(root),
            "--identifier",
            "doi:10.5555/JEX.2019.12.34",
            "--relative-path",
            scan.name,
        ],
    )
    assert result.exit_code == 0, result.output

    # 2. The file is renamed from the Crossref metadata.
    assert not scan.exists()
    [renamed] = list(root.glob("*.pdf"))
    assert renamed.name == "RoeA+KimS_2019-Grain_Growth_in_Cold_Clouds.pdf"

    # 3. Copy its BibTeX from the main table, with the DOI as the key.
    window = MainWindow()
    qtbot.addWidget(window)
    window._register_root(root)
    window._select_root(root)
    catalog = window.registries[str(root)].catalog
    record = catalog.get_file(root, renamed.name)
    window._copy_records_bibtex([record])
    text = QApplication.clipboard().text()
    assert "@article{10.5555/jex.2019.12.34," in text
    assert "Grain Growth in Cold Clouds" in text
    assert "Roe, Alice and Kim, Sun" in text
    window.close()
