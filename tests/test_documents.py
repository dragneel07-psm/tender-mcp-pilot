"""Security- and correctness-focused tests for the Milestone 3 document pipeline. No test here
touches a real network -- net.fetch/urlopen are mocked throughout."""
import unittest
from io import BytesIO
from unittest import mock

from pypdf import PdfWriter

from tender_monitor import documents


def _make_pdf_bytes(text_pages=1):
    writer = PdfWriter()
    for _ in range(text_pages):
        writer.add_blank_page(width=200, height=200)
    buf = BytesIO(); writer.write(buf)
    return buf.getvalue()


class SsrfGuardTests(unittest.TestCase):
    def test_private_ip_url_is_rejected_before_any_fetch(self):
        with mock.patch("urllib.request.urlopen") as urlopen_mock:
            result = documents.download_and_extract("http://127.0.0.1/notice.pdf")
        urlopen_mock.assert_not_called()
        self.assertEqual(result["extraction_status"], "rejected_unsafe_url")

    def test_localhost_hostname_is_rejected(self):
        result = documents.download_and_extract("http://localhost/notice.pdf")
        self.assertEqual(result["extraction_status"], "rejected_unsafe_url")


class DownloadAndExtractTests(unittest.TestCase):
    def _mock_response(self, data):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        chunks = [data[i:i+65536] for i in range(0, len(data), 65536)] + [b""]
        response.read.side_effect = chunks
        return response

    def test_successful_pdf_is_extracted(self):
        pdf_bytes = _make_pdf_bytes()
        with mock.patch("urllib.request.urlopen", return_value=self._mock_response(pdf_bytes)):
            result = documents.download_and_extract("https://example.gov.np/notice.pdf")
        self.assertIn(result["extraction_status"], ("ok", "empty_text_likely_scanned"))
        self.assertIsNotNone(result["sha256"])
        self.assertEqual(result["size_bytes"], len(pdf_bytes))

    def test_non_pdf_content_is_rejected_by_magic_bytes(self):
        with mock.patch("urllib.request.urlopen", return_value=self._mock_response(b"<html>not a pdf</html>")):
            result = documents.download_and_extract("https://example.gov.np/fake.pdf")
        self.assertEqual(result["extraction_status"], "not_a_pdf")

    def test_oversized_download_is_rejected_before_full_download(self):
        import os
        os.environ["DOCUMENT_MAX_SIZE_BYTES"] = "10"
        try:
            with mock.patch("urllib.request.urlopen", return_value=self._mock_response(b"%PDF-" + b"x" * 1000)):
                result = documents.download_and_extract("https://example.gov.np/big.pdf")
            self.assertEqual(result["extraction_status"], "rejected_too_large")
        finally:
            os.environ.pop("DOCUMENT_MAX_SIZE_BYTES", None)

    def test_corrupt_pdf_bytes_do_not_raise(self):
        with mock.patch("urllib.request.urlopen", return_value=self._mock_response(b"%PDF-1.4\ncorrupted garbage")):
            result = documents.download_and_extract("https://example.gov.np/broken.pdf")
        self.assertEqual(result["extraction_status"], "parse_failed")


class ClassifyDocumentTypeTests(unittest.TestCase):
    def test_boq_keyword(self):
        self.assertEqual(documents.classify_document_type("Bill of Quantities.pdf"), "boq")

    def test_default_is_tender_notice(self):
        self.assertEqual(documents.classify_document_type("Notice.pdf"), "tender_notice")


class DiscoverPdfLinksTests(unittest.TestCase):
    def test_finds_pdf_links_only(self):
        html = '<a href="/docs/notice.pdf">Notice</a><a href="/n/2">Not a PDF</a>'
        links = documents.discover_pdf_links(html, "https://x.gov.np/")
        self.assertEqual(links, [("https://x.gov.np/docs/notice.pdf", "Notice")])


class ExtractSubmissionDeadlineTests(unittest.TestCase):
    def test_finds_date_near_deadline_keyword(self):
        text = "Notice details here. Submission deadline: 2026-09-15. Other text."
        self.assertEqual(documents.extract_submission_deadline(text), "2026-09-15")

    def test_returns_none_without_a_deadline_keyword(self):
        text = "Published on 2026-01-01 with no closing information here."
        self.assertIsNone(documents.extract_submission_deadline(text))


if __name__ == "__main__":
    unittest.main()
