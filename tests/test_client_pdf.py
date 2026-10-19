import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from adsfolio import ADSClient


class FakeResponse:
    def __init__(self, status_code=200, headers=None, url="", text="", content=b""):
        self.status_code = status_code
        self.headers = headers or {}
        self.url = url
        self.text = text
        self._content = content
        self.closed = False

    def close(self):
        self.closed = True

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

    def test_save_stream_to_file_preserves_existing_on_stream_failure(self):
        class FailingResponse:
            def iter_content(self, chunk_size=65536):
                yield b"initial chunk"
                raise requests.RequestException("connection broken midway")

        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "paper.pdf"
            target.write_bytes(b"%PDF-original-valid")

            with self.assertRaises(requests.RequestException):
                ADSClient._save_stream_to_file(FailingResponse(), target)

            self.assertTrue(target.exists())
            self.assertEqual(target.read_bytes(), b"%PDF-original-valid")

    def test_save_stream_to_file_validation_preserves_existing_on_non_pdf(self):
        non_pdf_resp = FakeResponse(
            status_code=200,
            headers={"Content-Type": "application/octet-stream"},
            content=b"<!DOCTYPE html><html>Not a PDF</html>",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "paper.pdf"
            target.write_bytes(b"%PDF-original-valid")

            saved = ADSClient._save_stream_to_file(
                non_pdf_resp, target, validate_pdf=True
            )

            self.assertIsNone(saved)
            self.assertTrue(target.exists())
            self.assertEqual(target.read_bytes(), b"%PDF-original-valid")

    def test_download_pdfs_deduplicates_bibcodes_order_preserving(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            expected_1 = Path(tmpdir) / "2020ApJ...900..100S.pdf"
            expected_2 = Path(tmpdir) / "2021A&A...654A.113B.pdf"

            calls = []

            def fake_download(
                self,
                bibcode,
                output_dir=".",
                link_types=None,
                force_print_html=False,
                timeout=30,
                printed_suffix="__printed",
            ):
                calls.append(bibcode)
                if bibcode == "2020ApJ...900..100S":
                    return expected_1
                return expected_2

            with patch.object(ADSClient, "download_pdf", new=fake_download):
                input_bibcodes = [
                    "2020ApJ...900..100S",
                    "2021A&A...654A.113B",
                    "2020ApJ...900..100S",
                ]
                out = self.client.download_pdfs(
                    input_bibcodes,
                    output_dir=tmpdir,
                    show_progress=False,
                    max_workers=2,
                )

                self.assertEqual(calls.count("2020ApJ...900..100S"), 1)
                self.assertEqual(calls.count("2021A&A...654A.113B"), 1)
                self.assertEqual(
                    list(out.keys()), ["2020ApJ...900..100S", "2021A&A...654A.113B"]
                )
                self.assertEqual(out["2020ApJ...900..100S"], expected_1)
                self.assertEqual(out["2021A&A...654A.113B"], expected_2)

    def test_download_pdf_closes_response_on_success_and_failure(self):
        closed = []

        class ClosableFakeResponse(FakeResponse):
            def close(self):
                closed.append(self.url)

        resp = ClosableFakeResponse(
            status_code=200,
            headers={"Content-Type": "application/pdf"},
            url="https://example.org/download.pdf",
            content=b"%PDF-data",
        )
        self.client._base._session.get = MagicMock(return_value=resp)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = self.client.download_pdf("2020ApJ...900..100S", output_dir=tmpdir)
            self.assertIsNotNone(path)
            self.assertIn("https://example.org/download.pdf", closed)

    def test_download_pdf_serializes_concurrent_session_access(self):
        active_calls = 0
        max_concurrent_calls = 0
        call_lock = threading.Lock()
        started_event = threading.Event()
        release_event = threading.Event()

        def synchronized_get(url, *args, **kwargs):
            nonlocal active_calls, max_concurrent_calls
            with call_lock:
                active_calls += 1
                max_concurrent_calls = max(max_concurrent_calls, active_calls)
                if active_calls == 1:
                    started_event.set()
            try:
                # Wait briefly until release event or timeout
                release_event.wait(timeout=0.05)
                return FakeResponse(
                    status_code=200,
                    headers={"Content-Type": "application/pdf"},
                    url=url,
                    content=b"%PDF-concurrent",
                )
            finally:
                with call_lock:
                    active_calls -= 1

        self.client._base._session.get = synchronized_get

        with tempfile.TemporaryDirectory() as tmpdir:
            out = self.client.download_pdfs(
                ["2020ApJ...900..100S", "2021A&A...654A.113B", "2022MNRAS.500..200C"],
                output_dir=tmpdir,
                show_progress=False,
                max_workers=3,
            )

            self.assertEqual(len(out), 3)
            self.assertEqual(max_concurrent_calls, 1)

    def test_base_client_methods_share_lock_with_download(self):
        download_in_get = threading.Event()
        base_get_started = threading.Event()
        lock_acquired_while_downloading = []

        def blocking_download_get(url, *args, **kwargs):
            download_in_get.set()
            # Wait for base_client.get to attempt to acquire lock
            base_get_started.wait(timeout=0.1)
            return FakeResponse(
                status_code=200,
                headers={"Content-Type": "application/pdf"},
                url=url,
                content=b"%PDF-data",
            )

        self.client._base._session.get = blocking_download_get

        def call_base_client():
            base_get_started.set()
            # Attempting this while download_pdf holds the lock should block until download releases it
            acquired = self.client._base._lock.acquire(blocking=False)
            if acquired:
                self.client._base._lock.release()
                lock_acquired_while_downloading.append(True)
            else:
                lock_acquired_while_downloading.append(False)

        worker = threading.Thread(target=call_base_client)

        with tempfile.TemporaryDirectory() as tmpdir:
            # Start download in a thread
            dl_thread = threading.Thread(
                target=self.client.download_pdf,
                args=("2020ApJ...900..100S",),
                kwargs={"output_dir": tmpdir},
            )
            dl_thread.start()
            download_in_get.wait(timeout=1.0)

            # Start base call while download_pdf is holding the lock
            worker.start()
            worker.join()
            dl_thread.join()

        self.assertEqual(lock_acquired_while_downloading, [False])

    def test_print_webpage_to_pdf_injects_base_tag_when_missing(self):
        saved_calls = []

        class FakePage:
            def set_content(self, html_content, timeout=30000):
                saved_calls.append(html_content)

            def wait_for_load_state(self, state, timeout=30000):
                pass

            def emulate_media(self, media):
                pass

            def pdf(self, path, **kwargs):
                Path(path).write_bytes(b"%PDF-printed")

        class FakeContext:
            def add_init_script(self, script):
                pass

            def new_page(self):
                return FakePage()

            def close(self):
                pass

        class FakeBrowser:
            def new_context(self, **kwargs):
                return FakeContext()

            def close(self):
                pass

        class FakePlaywright:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                pass

            @property
            def chromium(self):
                class FakeChromium:
                    def launch(self, **kwargs):
                        return FakeBrowser()

                return FakeChromium()

        with tempfile.TemporaryDirectory() as tmpdir:
            out_pdf = Path(tmpdir) / "test.pdf"
            with patch(
                "sys.modules",
                {
                    **__import__("sys").modules,
                    "playwright.sync_api": __import__("types").SimpleNamespace(
                        sync_playwright=lambda: FakePlaywright()
                    ),
                },
            ):
                # Case 1: head exists without base
                res1 = ADSClient._print_webpage_to_pdf(
                    "https://publisher.org/paper",
                    out_pdf,
                    html_content="<html><head><title>Test</title></head><body>Hi</body></html>",
                )
                self.assertTrue(res1)
                self.assertIn(
                    '<base href="https://publisher.org/paper">', saved_calls[-1]
                )

                # Case 2: existing base tag is preserved, no duplicate inserted
                res2 = ADSClient._print_webpage_to_pdf(
                    "https://publisher.org/paper",
                    out_pdf,
                    html_content='<html><head><base href="https://custom.org/base"><title>Test</title></head></html>',
                )
                self.assertTrue(res2)
                self.assertEqual(saved_calls[-1].count("<base"), 1)
                self.assertIn('href="https://custom.org/base"', saved_calls[-1])

    def test_download_pdfs_allows_parallel_stream_reading_outside_lock(self):
        # Barrier for 2 workers simultaneously reading chunks in iter_content
        stream_barrier = threading.Barrier(2, timeout=2.0)
        workers_in_stream = 0
        max_workers_in_stream = 0
        stream_lock = threading.Lock()

        class BarrierResponse(FakeResponse):
            def iter_content(self, chunk_size=65536):
                nonlocal workers_in_stream, max_workers_in_stream
                with stream_lock:
                    workers_in_stream += 1
                    max_workers_in_stream = max(
                        max_workers_in_stream, workers_in_stream
                    )
                try:
                    # Both workers must reach here concurrently
                    stream_barrier.wait()
                    yield b"%PDF-parallel-stream"
                finally:
                    with stream_lock:
                        workers_in_stream -= 1

        def fake_get(url, *args, **kwargs):
            return BarrierResponse(
                status_code=200,
                headers={"Content-Type": "application/pdf"},
                url=url,
            )

        self.client._base._session.get = fake_get

        with tempfile.TemporaryDirectory() as tmpdir:
            out = self.client.download_pdfs(
                ["2020ApJ...900..100S", "2021A&A...654A.113B"],
                output_dir=tmpdir,
                show_progress=False,
                max_workers=2,
            )

            self.assertEqual(len(out), 2)
            self.assertIsNotNone(out["2020ApJ...900..100S"])
            self.assertIsNotNone(out["2021A&A...654A.113B"])
            self.assertEqual(max_workers_in_stream, 2)

    def test_download_pdfs_logs_warning_on_failed_future_and_preserves_others(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            success_pdf = Path(tmpdir) / "2020ApJ...900..100S.pdf"
            success_pdf.write_bytes(b"%PDF-valid")

            def fake_download(self, bibcode, *args, **kwargs):
                if bibcode == "2021A&A...654A.113B":
                    raise RuntimeError("simulated future crash")
                return success_pdf

            with (
                patch.object(ADSClient, "download_pdf", new=fake_download),
                self.assertLogs("adsfolio.client", level="WARNING") as captured,
            ):
                results = self.client.download_pdfs(
                    ["2020ApJ...900..100S", "2021A&A...654A.113B"],
                    output_dir=tmpdir,
                    show_progress=False,
                    max_workers=2,
                )

            self.assertEqual(results["2020ApJ...900..100S"], success_pdf)
            self.assertIsNone(results["2021A&A...654A.113B"])
            self.assertTrue(
                any(
                    "Failed downloading PDF for bibcode 2021A&A...654A.113B"
                    in record.getMessage()
                    and record.exc_info is not None
                    for record in captured.records
                )
            )


if __name__ == "__main__":
    unittest.main()
