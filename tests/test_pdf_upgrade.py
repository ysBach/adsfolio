from types import SimpleNamespace

from adsfolio.api.search import SearchResult
from adsfolio.pdf.sidecar import Sidecar
from adsfolio.pdf.upgrade import (
    PdfUpgrader,
    check_published_version,
    detect_arxiv_entries,
)


class _SearchStub:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def query(self, q, fl=None, rows=10, start=0, sort=None, fq=None):
        self.calls.append(q)
        return self.mapping.get(q, SearchResult(num_found=0, start=0, docs=[]))


def _sidecar_entry(**overrides):
    base = {
        "bibcode": "2024arXiv240112345B",
        "doi": "10.48550/arXiv.2401.12345",
        "title": ["Sample Title"],
        "author": ["Bach, Yoonsoo"],
        "year": "2024",
        "bibstem": ["arXiv"],
        "volume": "",
        "page": [""],
        "pub": "arXiv e-prints",
        "sha256": "sha-arxiv",
        "current_filename": "paper.pdf",
    }
    base.update(overrides)
    return base


def test_detect_arxiv_entries_matches_multiple_signals(tmp_path):
    sidecar = Sidecar(tmp_path)
    sidecar.upsert_file("arxiv-by-bibcode.pdf", _sidecar_entry())
    sidecar.upsert_file(
        "arxiv-by-source.pdf",
        {
            "bibcode": "2025ApJ...123..456A",
            "source": "arxiv",
            "current_filename": "arxiv-source.pdf",
        },
    )
    sidecar.upsert_file(
        "published.pdf",
        {
            "bibcode": "2025ApJ...123..456A",
            "doi": "10.3847/example",
            "bibstem": ["ApJ"],
            "current_filename": "published.pdf",
        },
    )

    detected = detect_arxiv_entries(sidecar)
    assert sorted(detected) == ["arxiv-by-bibcode.pdf", "arxiv-by-source.pdf"]


def test_check_published_version_uses_alternate_bibcode():
    arxiv_bibcode = "2024arXiv240112345B"
    published_doc = {
        "bibcode": "2025ApJ...123..456A",
        "alternate_bibcode": [arxiv_bibcode],
        "doi": ["10.3847/example"],
    }
    search = _SearchStub(
        {
            f'bibcode:"{arxiv_bibcode}"': SearchResult(
                num_found=1,
                start=0,
                docs=[published_doc],
            )
        }
    )
    client = SimpleNamespace(search=search)

    match = check_published_version(client, arxiv_bibcode)
    assert match is not None
    assert match.bibcode == "2025ApJ...123..456A"


def test_upgrade_pdf_apply_overwrite_with_backup_and_unported_file(
    tmp_path, monkeypatch
):
    current_path = tmp_path / "paper.pdf"
    current_path.write_bytes(b"%PDF-arxiv")
    downloaded_path = tmp_path / "2025ApJ...123..456A.pdf"
    downloaded_path.write_bytes(b"%PDF-official")
    ported_path = tmp_path / "2025ApJ...123..456A__ported.pdf"
    ported_path.write_bytes(b"%PDF-ported")

    sidecar = Sidecar(tmp_path)
    sidecar.upsert_file("paper.pdf", _sidecar_entry(current_filename="paper.pdf"))
    sidecar.save()

    arxiv_bibcode = "2024arXiv240112345B"
    published_doc = {
        "bibcode": "2025ApJ...123..456A",
        "alternate_bibcode": [arxiv_bibcode],
        "doi": ["10.3847/example"],
        "title": ["Published Title"],
        "bibstem": ["ApJ"],
    }
    search = _SearchStub(
        {
            f'bibcode:"{arxiv_bibcode}"': SearchResult(
                num_found=1,
                start=0,
                docs=[published_doc],
            )
        }
    )
    client = SimpleNamespace(
        search=search,
        download_pdf=lambda bibcode, output_dir: downloaded_path,
    )
    upgrader = PdfUpgrader(client)

    monkeypatch.setattr(
        "adsfolio.pdf.upgrade.port_annotations",
        lambda source_path, target_path, output_path: SimpleNamespace(
            total_portable=2,
            ported=1,
            failed=1,
            skipped_nonportable=0,
            output_path=str(ported_path),
            unported=[{"text": "could not match"}],
        ),
    )

    backup_dir = tmp_path / "backup"
    result = upgrader.upgrade_pdf(
        sidecar=sidecar,
        original_filename="paper.pdf",
        apply=True,
        overwrite_arxiv_with_official=True,
        arxiv_backup_if_overwritten=backup_dir,
        skip_annotation_port=False,
    )

    assert result.status == "upgraded"
    assert current_path.read_bytes() == b"%PDF-ported"
    assert (backup_dir / "paper.pdf").exists()

    updated = sidecar.get_file("paper.pdf")
    assert updated is not None
    assert updated["bibcode"] == "2025ApJ...123..456A"
    assert updated["original_bibcode"] == "2024arXiv240112345B"
    assert updated["source"] == "published"
    assert updated["upgrade_status"] == "upgraded"
    assert updated["annotations_ported"] == 1
    assert updated["annotations_failed"] == 1
    assert updated["unported_annotations_file"]
    assert (tmp_path / updated["unported_annotations_file"]).exists()


def test_batch_upgrade_processes_only_arxiv_entries(tmp_path):
    sidecar = Sidecar(tmp_path)
    sidecar.upsert_file("arxiv.pdf", _sidecar_entry(current_filename="arxiv.pdf"))
    sidecar.upsert_file(
        "published.pdf",
        {
            "bibcode": "2025ApJ...123..456A",
            "doi": "10.3847/example",
            "bibstem": ["ApJ"],
            "current_filename": "published.pdf",
        },
    )
    (tmp_path / "arxiv.pdf").write_bytes(b"%PDF")

    arxiv_bibcode = "2024arXiv240112345B"
    published_doc = {
        "bibcode": "2025ApJ...123..456A",
        "alternate_bibcode": [arxiv_bibcode],
        "doi": ["10.3847/example"],
    }
    search = _SearchStub(
        {
            f'bibcode:"{arxiv_bibcode}"': SearchResult(
                num_found=1,
                start=0,
                docs=[published_doc],
            )
        }
    )
    client = SimpleNamespace(
        search=search, download_pdf=lambda bibcode, output_dir: None
    )
    upgrader = PdfUpgrader(client)

    results = upgrader.batch_upgrade(sidecar=sidecar, apply=False)
    assert len(results) == 1
    assert results[0].original_filename == "arxiv.pdf"
    assert results[0].status == "dry-run"


def test_batch_upgrade_prefetches_unique_bibcodes_once(tmp_path):
    arxiv_bibcode = "2024arXiv240112345B"
    published_doc = {
        "bibcode": "2025ApJ...123..456A",
        "alternate_bibcode": [arxiv_bibcode],
        "doi": ["10.3847/example"],
    }

    sidecar = Sidecar(tmp_path)
    sidecar.upsert_file(
        "first.pdf",
        _sidecar_entry(current_filename="first.pdf", bibcode=arxiv_bibcode),
    )
    sidecar.upsert_file(
        "second.pdf",
        _sidecar_entry(current_filename="second.pdf", bibcode=arxiv_bibcode),
    )
    (tmp_path / "first.pdf").write_bytes(b"%PDF")
    (tmp_path / "second.pdf").write_bytes(b"%PDF")

    query = f'bibcode:"{arxiv_bibcode}"'
    search = _SearchStub(
        {query: SearchResult(num_found=1, start=0, docs=[published_doc])}
    )
    client = SimpleNamespace(
        search=search,
        download_pdf=lambda bibcode, output_dir: None,
    )
    upgrader = PdfUpgrader(client)

    results = upgrader.batch_upgrade(sidecar=sidecar, apply=False)

    assert len(results) == 2
    assert [result.status for result in results] == ["dry-run", "dry-run"]
    assert search.calls == [query]
