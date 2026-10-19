"""Identify keeps catalog paths relative for alternate spellings of a root."""

from __future__ import annotations

import pytest
from click.testing import CliRunner
from pypdf import PdfWriter

from adsfolio.cli.commands import main
from adsfolio.config import Config
from adsfolio.pdf.registry import PdfRegistry


@pytest.mark.parametrize("use_symlink", [False, True])
def test_identify_normalizes_scan_root(tmp_path, monkeypatch, use_symlink) -> None:
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    monkeypatch.setattr(
        "adsfolio.pubcache.cache_path", lambda: tmp_path / "cache" / "publications.json"
    )
    root = tmp_path / "Papers 한글 with spaces"
    root.mkdir()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(root / "Paper 한글.pdf")
    scan_root = root
    if use_symlink:
        scan_root = tmp_path / "library-alias"
        try:
            scan_root.symlink_to(root, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"Directory symlinks unavailable: {exc}")
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["pdf", "identify", scan_root.name])
    assert result.exit_code == 0, result.output
    registry = PdfRegistry(root)
    try:
        record = registry.find_file_by_current_path(root, "Paper 한글.pdf")
        assert record is not None
        assert record["relative_path"] == "Paper 한글.pdf"
    finally:
        registry.close()
