"""Name downloaded PDFs after their bibcode without overwriting existing files."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock

import pytest

from adsfolio.client import ADSClient
from adsfolio.pdf.identifier import PdfIdentifier

BIBCODE = "2021A&A...654A.113B"
PDF_CONTENT = b"%PDF-synthetic-download"


class FakePdfResponse:
    status_code = 200
    url = "https://example.invalid/paper.pdf"

    def __init__(self):
        self.headers = {"Content-Type": "application/pdf"}

    def iter_content(self, chunk_size=65536):
        yield PDF_CONTENT

    def close(self):
        pass


@pytest.fixture
def client():
    client = ADSClient(token="synthetic-token")
    client._base._session.get = Mock(
        side_effect=lambda *args, **kwargs: FakePdfResponse()
    )
    yield client
    client.close()


@pytest.mark.parametrize(
    "bibcode", [BIBCODE, "2020ApJ...900..100S", "2008AgrJ..100.-166S"]
)
def test_downloaded_filename_preserves_bibcode(client, tmp_path, bibcode):
    saved = client.download_pdf(bibcode, output_dir=tmp_path)

    assert saved == tmp_path / f"{bibcode}.pdf"
    assert saved.read_bytes() == PDF_CONTENT
    assert PdfIdentifier().extract_bibcode_from_filename(saved.name) == bibcode


@pytest.mark.parametrize("suffix", ["", "_1", "-printed"])
def test_bibcode_page_hyphen_survives_filename_extraction(suffix):
    bibcode = "2008AgrJ..100.-166S"
    assert (
        PdfIdentifier().extract_bibcode_from_filename(f"Author_{bibcode}{suffix}.pdf")
        == bibcode
    )


@pytest.mark.parametrize(
    "stem",
    [
        BIBCODE,
        "2021A&A...654A-113B",
        "2021A&A...654A+113B",
        "2021A&A...654A.113.",
    ],
)
def test_filename_stem_preserves_filesystem_safe_characters(stem):
    assert ADSClient._safe_filename(stem) == stem


def test_collision_keeps_existing_files_and_identifies_new_file(client, tmp_path):
    existing = tmp_path / f"{BIBCODE}.pdf"
    previous_collision = tmp_path / f"{BIBCODE}_1.pdf"
    legacy_filename = tmp_path / f"{BIBCODE.replace('&', '_')}.pdf"
    existing.write_bytes(b"%PDF-existing-with-annotations")
    previous_collision.write_bytes(b"%PDF-previous-download")
    legacy_filename.write_bytes(b"%PDF-previously-misnamed")

    saved = client.download_pdf(BIBCODE, output_dir=tmp_path)

    assert saved == tmp_path / f"{BIBCODE}_2.pdf"
    assert existing.read_bytes() == b"%PDF-existing-with-annotations"
    assert previous_collision.read_bytes() == b"%PDF-previous-download"
    assert legacy_filename.read_bytes() == b"%PDF-previously-misnamed"
    assert saved.read_bytes() == PDF_CONTENT
    assert PdfIdentifier().extract_bibcode_from_filename(saved.name) == BIBCODE


def test_collision_does_not_replace_symlink(client, tmp_path):
    target = tmp_path / "original.pdf"
    target.write_bytes(b"%PDF-original")
    existing = tmp_path / f"{BIBCODE}.pdf"
    try:
        existing.symlink_to(target)
    except OSError:
        pytest.skip("Symbolic links are unavailable.")

    saved = client.download_pdf(BIBCODE, output_dir=tmp_path)

    assert saved == tmp_path / f"{BIBCODE}_1.pdf"
    assert existing.is_symlink()
    assert target.read_bytes() == b"%PDF-original"
    assert saved.read_bytes() == PDF_CONTENT
    assert PdfIdentifier().extract_bibcode_from_filename(saved.name) == BIBCODE


def test_concurrent_download_collisions_do_not_overwrite(client, tmp_path):
    barrier = Barrier(2)

    class SynchronizedResponse(FakePdfResponse):
        def __init__(self, content):
            super().__init__()
            self.content = content

        def iter_content(self, chunk_size=65536):
            barrier.wait(timeout=5)
            yield self.content

    contents = [b"%PDF-first", b"%PDF-second"]
    client._base._session.get = Mock(
        side_effect=[SynchronizedResponse(content) for content in contents]
    )
    existing = tmp_path / f"{BIBCODE}.pdf"
    existing.write_bytes(b"%PDF-original")

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(client.download_pdf, BIBCODE, output_dir=tmp_path)
            for _ in contents
        ]
        saved_paths = [future.result() for future in futures]

    assert set(saved_paths) == {
        tmp_path / f"{BIBCODE}_1.pdf",
        tmp_path / f"{BIBCODE}_2.pdf",
    }
    assert existing.read_bytes() == b"%PDF-original"
    assert {path.read_bytes() for path in saved_paths} == set(contents)
    assert all(
        PdfIdentifier().extract_bibcode_from_filename(path.name) == BIBCODE
        for path in saved_paths
    )


def test_printed_filename_preserves_bibcode_on_collision(client, tmp_path, monkeypatch):
    response = FakePdfResponse()
    response.headers = {"Content-Type": "text/html"}
    response.url = "https://example.invalid/article"
    response.text = "<html>Article</html>"
    client._base._session.get = Mock(return_value=response)

    def fake_print(url, output_path, timeout, html_content):
        output_path.write_bytes(PDF_CONTENT)
        return True

    monkeypatch.setattr(client, "_print_webpage_to_pdf", fake_print)
    existing = tmp_path / f"{BIBCODE}__printed.pdf"
    existing.write_bytes(b"%PDF-existing-print")

    saved = client.download_pdf(BIBCODE, output_dir=tmp_path, force_print_html=True)

    assert saved == tmp_path / f"{BIBCODE}__printed_1.pdf"
    assert existing.read_bytes() == b"%PDF-existing-print"
    assert saved.read_bytes() == PDF_CONTENT
    assert PdfIdentifier().extract_bibcode_from_filename(saved.name) == BIBCODE


@pytest.mark.parametrize(
    "bibcode",
    [
        ".",
        "..",
        "../../outside",
        r"..\..\outside",
        "/absolute/path",
        r"C:\outside",
        "CON",
        "nul.txt",
        "COM1",
        "COM¹",
        "CON .txt",
        "LPT9",
        *[f"name{char}part" for char in '<>:"/\\|?*\x00\x1f\x7f'],
    ],
)
def test_invalid_bibcode_fails_before_request_or_write(client, tmp_path, bibcode):
    destination = tmp_path / "downloads"

    with pytest.raises(ValueError):
        client.download_pdf(bibcode, output_dir=destination)

    assert not destination.exists()
    client._base._session.get.assert_not_called()


@pytest.mark.parametrize("suffix", ["/../../outside", r"\..\outside", ":bad", "?"])
def test_invalid_printed_suffix_fails_before_request_or_write(client, tmp_path, suffix):
    destination = tmp_path / "downloads"

    with pytest.raises(ValueError):
        client.download_pdf(
            BIBCODE,
            output_dir=destination,
            force_print_html=True,
            printed_suffix=suffix,
        )

    assert not destination.exists()
    client._base._session.get.assert_not_called()
