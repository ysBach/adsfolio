"""``pdf identify --file`` identifies only the chosen PDFs."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from adsfolio.config import Config
from adsfolio.pdf.cli import pdf
from adsfolio.pdf.registry import open_registry_state


@pytest.fixture
def library(tmp_path: Path, monkeypatch) -> SimpleNamespace:
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    root = tmp_path / "library"
    (root / "unresolved").mkdir(parents=True)
    (root / "10.1234_first.pdf").write_bytes(b"%PDF-1.4 first")
    (root / "10.1234_second.pdf").write_bytes(b"%PDF-1.4 second")
    (root / "unresolved" / "10.1234_third.pdf").write_bytes(b"%PDF-1.4 third")
    looked_up: list[str] = []

    def crossref(doi: str):
        looked_up.append(doi)
        return {
            "doi": [doi],
            "title": [f"Paper {doi}"],
            "author": ["Curie, Marie"],
            "year": "2024",
        }

    monkeypatch.setattr("adsfolio.pdf.identifier.fetch_crossref_doi", crossref)
    monkeypatch.setattr(
        "adsfolio.pdf.cli._get_client", lambda ctx: pytest.fail("no ADS needed")
    )
    return SimpleNamespace(root=root, looked_up=looked_up)


def _identify(root: Path, *files: Path):
    args = ["identify", str(root), "--provider", "crossref"]
    args += ["--strategy", "filename_doi"]
    for file in files:
        args += ["--file", str(file)]
    return CliRunner().invoke(pdf, args)


def _identified(root: Path) -> set[str]:
    state = open_registry_state(root)
    try:
        return {
            str(record["current_path"])
            for record in state.registry.iter_files()
            if record["metadata"].get("title")
        }
    finally:
        state.close()


def test_only_the_chosen_file_is_identified(library):
    result = _identify(library.root, library.root / "10.1234_second.pdf")
    assert result.exit_code == 0, result.output
    assert library.looked_up == ["10.1234/second"]
    assert _identified(library.root) == {"10.1234_second.pdf"}


def test_a_chosen_file_in_the_unresolved_directory_is_included(library):
    root = library.root
    result = _identify(root, root / "unresolved" / "10.1234_third.pdf")
    assert result.exit_code == 0, result.output
    assert _identified(root) == {"unresolved/10.1234_third.pdf"}


def test_a_file_outside_the_library_is_refused(library, tmp_path):
    outside = tmp_path / "10.1234_elsewhere.pdf"
    outside.write_bytes(b"%PDF-1.4 elsewhere")
    result = _identify(library.root, outside)
    assert result.exit_code != 0
    assert "is not inside" in result.output
    assert library.looked_up == []


def test_identify_dialog_passes_the_selected_files(qtbot, tmp_path):
    from adsfolio.gui.dialogs import IdentifyDialog

    first, second = tmp_path / "a.pdf", tmp_path / "b.pdf"
    dialog = IdentifyDialog(tmp_path, files=[first, second])
    qtbot.addWidget(dialog)
    assert dialog.windowTitle() == "Identify 2 Selected PDFs"
    argv = dialog.build_argv()
    assert argv[-4:] == ["--file", str(first), "--file", str(second)]
