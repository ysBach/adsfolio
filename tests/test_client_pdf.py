import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from adsfolio import ADSClient


class FakeResponse:
    def __init__(self, status_code=200, headers=None, url="", text="", content=b""):
        self.status_code = status_code
        self.headers = headers or {}
        self.url = url
        self.text = text
        self._content = content

    def iter_content(self, chunk_size=65536):
        if self._content:
            yield self._content


class TestDownloadPdf(unittest.TestCase):
    def setUp(self):
        self.client = ADSClient(token="dummy-token")

    def test_base_client_close_closes_http_session(self):
        session = MagicMock()
        self.client._base._session = session

        self.client._base.close()

        session.close.assert_called_once_with()

    def test_ads_client_close_delegates_to_owned_base_client(self):
        self.client._base.close = MagicMock()

        self.client.close()

        self.client._base.close.assert_called_once_with()

    def test_download_pdf_direct_response(self):
        pdf_resp = FakeResponse(
            status_code=200,
            headers={"Content-Type": "application/pdf"},
            url="https://example.org/paper.pdf",
            content=b"%PDF-direct",
        )
        self.client._base._session.get = MagicMock(return_value=pdf_resp)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = self.client.download_pdf(
                "2020ApJ...900..100S",
                output_dir=tmpdir,
            )

            self.assertIsNotNone(path)
            assert path is not None
            self.assertTrue(path.exists())
            self.assertEqual(path.read_bytes(), b"%PDF-direct")

    def test_download_pdf_from_html_embedded_link(self):
        html_resp = FakeResponse(
            status_code=200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            url="https://publisher.org/landing",
            text='<html><a href="/files/paper.pdf">PDF</a></html>',
        )
        embedded_pdf_resp = FakeResponse(
            status_code=200,
            headers={"Content-Type": "application/pdf"},
            url="https://publisher.org/files/paper.pdf",
            content=b"%PDF-embedded",
        )
        self.client._base._session.get = MagicMock(
            side_effect=[html_resp, embedded_pdf_resp]
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = self.client.download_pdf(
                "2021A&A...654A.113B",
                output_dir=tmpdir,
            )

            self.assertIsNotNone(path)
            assert path is not None
            self.assertTrue(path.exists())
            self.assertEqual(path.read_bytes(), b"%PDF-embedded")

    def test_download_pdf_force_print_html_success(self):
        html_resp = FakeResponse(
            status_code=200,
            headers={"Content-Type": "text/html"},
            url="https://publisher.org/no-pdf",
            text="<html><body>No direct PDF</body></html>",
        )
        self.client._base._session.get = MagicMock(return_value=html_resp)

        def fake_print(
            url: str,
            output_path: Path,
            timeout: int = 30,
            html_content: str | None = None,
        ) -> bool:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"%PDF-printed")
            return True

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(
                ADSClient, "_print_webpage_to_pdf", side_effect=fake_print
            ):
                path = self.client.download_pdf(
                    "2025arXiv251207318L",
                    output_dir=tmpdir,
                    force_print_html=True,
                )

            self.assertIsNotNone(path)
            assert path is not None
            self.assertTrue(path.exists())
            self.assertTrue(path.name.endswith("__printed.pdf"))
            self.assertEqual(path.read_bytes(), b"%PDF-printed")

    def test_download_pdf_force_print_html_unavailable(self):
        html_resp = FakeResponse(
            status_code=200,
            headers={"Content-Type": "text/html"},
            url="https://publisher.org/no-pdf",
            text="<html><body>No direct PDF</body></html>",
        )
        self.client._base._session.get = MagicMock(return_value=html_resp)

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(ADSClient, "_print_webpage_to_pdf", return_value=False):
                path = self.client.download_pdf(
                    "2026arXiv260209139H",
                    output_dir=tmpdir,
                    force_print_html=True,
                )

            self.assertIsNone(path)

    def test_abstract_url_builder(self):
        url = self.client.abstract_url("2021A&A...654A.113B")
        self.assertEqual(
            url,
            "https://ui.adsabs.harvard.edu/abs/2021A%26A...654A.113B/abstract",
        )

    def test_download_pdfs_batch_wrapper(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            expected_path = Path(tmpdir) / "2020ApJ...900..100S.pdf"
            with patch.object(
                ADSClient,
                "download_pdf",
                side_effect=[expected_path, None],
            ) as mocked:
                out = self.client.download_pdfs(
                    ["2020ApJ...900..100S", "2021A&A...654A.113B"],
                    output_dir=tmpdir,
                    show_progress=False,
                    max_workers=1,
                )

            self.assertEqual(out["2020ApJ...900..100S"], expected_path)
            self.assertIsNone(out["2021A&A...654A.113B"])
            self.assertEqual(mocked.call_count, 2)

    def test_download_pdfs_parallel_wrapper(self):
        with tempfile.TemporaryDirectory() as tmpdir:

            def fake_download_pdf(
                self,
                bibcode,
                output_dir=".",
                link_types=None,
                force_print_html=False,
                timeout=30,
                printed_suffix="__printed",
            ):
                if bibcode.endswith("100S"):
                    return Path(tmpdir) / "2020ApJ...900..100S.pdf"
                return None

            with patch.object(ADSClient, "download_pdf", new=fake_download_pdf):
                out = self.client.download_pdfs(
                    ["2020ApJ...900..100S", "2021A&A...654A.113B"],
                    output_dir=tmpdir,
                    show_progress=False,
                    max_workers=2,
                )

            self.assertIsNotNone(out["2020ApJ...900..100S"])
            self.assertIsNone(out["2021A&A...654A.113B"])

    def test_force_print_html_via_pub_html_fallback(self):
        """Regression: when PUB_PDF/EPRINT_PDF/AUTHOR_PDF are absent (non-200)
        but PUB_HTML returns an HTML page, force_print_html must fire."""
        not_found = FakeResponse(status_code=404)
        html_resp = FakeResponse(
            status_code=200,
            headers={"Content-Type": "text/html"},
            url="https://publisher.org/abstract/BKAS2023-001",
            text="<html><body>Abstract page only</body></html>",
        )
        # PUB_PDF, EPRINT_PDF, AUTHOR_PDF → 404; PUB_HTML → 200 HTML
        self.client._base._session.get = MagicMock(
            side_effect=[not_found, not_found, not_found, html_resp]
        )

        def fake_print(
            url: str,
            output_path: Path,
            timeout: int = 30,
            html_content: str | None = None,
        ) -> bool:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"%PDF-printed-from-pub-html")
            return True

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(
                ADSClient, "_print_webpage_to_pdf", side_effect=fake_print
            ) as mock_print:
                path = self.client.download_pdf(
                    "2023BKAS...42....1B",
                    output_dir=tmpdir,
                    force_print_html=True,
                )

            self.assertIsNotNone(path)
            assert path is not None
            self.assertTrue(path.exists())
            self.assertTrue(path.name.endswith("__printed.pdf"))
            self.assertEqual(path.read_bytes(), b"%PDF-printed-from-pub-html")
            mock_print.assert_called_once()
            called_url = mock_print.call_args[0][0]
            self.assertEqual(called_url, "https://publisher.org/abstract/BKAS2023-001")


if __name__ == "__main__":
    unittest.main()
