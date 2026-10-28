"""Automatic arXiv-to-published upgrades must prove PDF identity first."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter

from adsfolio.pdf.identity import IDENTITY_KEY, assert_mutation_allowed
from adsfolio.pdf.sidecar import Sidecar
from adsfolio.pdf.upgrade import PdfUpgrader, PublishedMatch

ARXIV_BIBCODE = "2024arXiv240100001A"
PUB_BIBCODE = "2025ApJ...900..100A"
OWN = "2401.00001"
OTHER = "2401.99999"
DOC = {
    "bibcode": PUB_BIBCODE,
    "alternate_bibcode": [ARXIV_BIBCODE],
    "identifier": [f"arXiv:{OWN}", ARXIV_BIBCODE, PUB_BIBCODE],
    "doi": ["10.3847/example"],
    "title": ["Own paper"],
}


def make_pdf(path: Path, arxiv: str | None, tag: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    info = {"/Title": f"fixture {tag}"}
    if arxiv:
        info["/arXiv"] = f"arXiv:{arxiv}"
    writer.add_metadata(info)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Setup:
    def __init__(self, tmp_path: Path, source_arxiv: str, download_arxiv: str):
        self.library = tmp_path / "library"
        self.library.mkdir()
        self.source = make_pdf(self.library / "paper.pdf", source_arxiv, "source")
        self.download_arxiv = download_arxiv
        self.download_calls = 0
        self.output_dirs: list[Path] = []
        self.sidecar = Sidecar(self.library)
        self.sidecar.upsert_file(
            "paper.pdf",
            {
                "bibcode": ARXIV_BIBCODE,
                "doi": "10.48550/arXiv.2401.00001",
                "current_filename": "paper.pdf",
                "source": "arxiv",
                "sha256": digest(self.source),
            },
        )
        self.sidecar.save()
        self.client = SimpleNamespace(download_pdf=self._download)
        self.match = PublishedMatch(bibcode=PUB_BIBCODE, document=dict(DOC))

    def _download(self, bibcode: str, output_dir: Path) -> Path:
        self.download_calls += 1
        self.output_dirs.append(Path(output_dir))
        return make_pdf(
            Path(output_dir) / f"{bibcode}.pdf", self.download_arxiv, "official"
        )

    def upgrade(self, **kwargs):
        return PdfUpgrader(self.client).upgrade_pdf(
            self.sidecar,
            "paper.pdf",
            apply=True,
            skip_annotation_port=True,
            published_match=self.match,
            **kwargs,
        )

    def library_state(self) -> dict[str, str]:
        return {p.name: digest(p) for p in self.library.iterdir() if p.is_file()}


def test_valid_source_and_download_upgrade_keeps_existing_named_pdf(tmp_path):
    s = Setup(tmp_path, OWN, OWN)
    preexisting = make_pdf(s.library / f"{PUB_BIBCODE}.pdf", OWN, "preexisting")
    before = digest(preexisting)

    result = s.upgrade()

    assert result.status == "upgraded", result.message
    assert digest(preexisting) == before
    assert result.current_filename == f"{PUB_BIBCODE}_1.pdf"
    assert (s.library / result.current_filename).exists()
    assert s.output_dirs and s.library not in s.output_dirs
    record = s.sidecar.get_file("paper.pdf")
    assert record["bibcode"] == PUB_BIBCODE
    assert record[IDENTITY_KEY]["status"] == "verified"
    assert record["sha256"] == digest(s.library / result.current_filename)
    assert "10.48550/arxiv.2401.00001" not in str(record.get("doi", "")).lower()


def test_wrong_source_blocks_before_download_even_with_force(tmp_path):
    s = Setup(tmp_path, OTHER, OWN)
    before = s.library_state()
    catalog = dict(s.sidecar.get_file("paper.pdf"))

    for force in (False, True):
        result = s.upgrade(force=force)
        assert result.status == "failed"

    assert s.download_calls == 0
    assert s.library_state() == before
    assert s.sidecar.get_file("paper.pdf") == catalog


@pytest.mark.parametrize("overwrite", [False, True])
def test_wrong_download_never_touches_library_or_catalog(tmp_path, overwrite):
    s = Setup(tmp_path, OWN, OTHER)
    before = s.library_state()
    catalog = dict(s.sidecar.get_file("paper.pdf"))

    result = s.upgrade(
        force=True,
        overwrite_arxiv_with_official=overwrite,
        arxiv_backup_if_overwritten=tmp_path / "backup",
    )

    assert result.status == "failed"
    assert s.download_calls == 1
    assert s.library_state() == before
    assert s.sidecar.get_file("paper.pdf") == catalog
    assert not (tmp_path / "backup").exists()
    assert not list(s.library.glob("*__ported*"))


@pytest.mark.parametrize("overwrite", [False, True])
def test_saved_record_passes_guard_from_stored_proof(tmp_path, overwrite):
    s = Setup(tmp_path, OWN, OWN)
    result = s.upgrade(overwrite_arxiv_with_official=overwrite)
    assert result.status == "upgraded", result.message

    record = s.sidecar.get_file("paper.pdf")
    reloaded = Sidecar(s.library).get_file("paper.pdf")
    for saved in (record, reloaded):
        proof = assert_mutation_allowed(
            s.library / result.current_filename,
            saved,
            expected_sha256=saved["sha256"],
        )
        assert proof["status"] == "verified"
        assert proof["authority"] == "stored_proof"


def test_exclusive_copy_never_replaces_and_cleans_up_on_failure(tmp_path, monkeypatch):
    s = Setup(tmp_path, OWN, OWN)
    src = make_pdf(tmp_path / "src.pdf", OWN, "src")
    taken = make_pdf(s.library / "x.pdf", OWN, "taken")
    before = digest(taken)
    upgrader = PdfUpgrader(s.client)

    out = upgrader._copy_exclusive(src, s.library, "x.pdf")
    assert out.name == "x_1.pdf" and digest(taken) == before

    def boom(*_args):
        raise OSError("disk full")

    monkeypatch.setattr("adsfolio.pdf.upgrade.shutil.copystat", boom)
    with pytest.raises(OSError):
        upgrader._copy_exclusive(src, s.library, "x.pdf")
    assert sorted(p.name for p in s.library.glob("x*.pdf")) == ["x.pdf", "x_1.pdf"]
