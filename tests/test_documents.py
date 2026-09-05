"""Security- and correctness-focused tests for the Milestone 3 document pipeline (Milestone 15:
OCR fallback for scanned PDFs and photo notices). No test here touches a real network --
net.fetch/urlopen are mocked throughout; OCR itself is mocked except where noted."""
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


def _make_scanned_pdf_bytes():
    """A PDF with no text layer at all, just one embedded full-page image -- shaped like a real
    scan, unlike _make_pdf_bytes' truly empty blank page (which has no image for OCR to even find).
    Built via Pillow's own "save an image as a one-page PDF" support, not a hand-rolled PDF writer,
    so it's a realistic fixture for exercising documents._ocr_scanned_pdf's page.images path."""
    from PIL import Image
    buf = BytesIO()
    Image.new("RGB", (100, 100), "white").save(buf, format="PDF")
    return buf.getvalue()


def _mock_response(data):
    response = mock.MagicMock()
    response.__enter__.return_value = response
    chunks = [data[i:i+65536] for i in range(0, len(data), 65536)] + [b""]
    response.read.side_effect = chunks
    return response


class SsrfGuardTests(unittest.TestCase):
    def test_private_ip_url_is_rejected_before_any_fetch(self):
        with mock.patch("urllib.request.urlopen") as urlopen_mock:
            result = documents.download_and_extract("http://127.0.0.1/notice.pdf")
        urlopen_mock.assert_not_called()
        self.assertEqual(result["extraction_status"], "rejected_unsafe_url")

    def test_localhost_hostname_is_rejected(self):
        result = documents.download_and_extract("http://localhost/notice.pdf")
        self.assertEqual(result["extraction_status"], "rejected_unsafe_url")

    def test_image_download_also_rejects_private_ip_before_any_fetch(self):
        with mock.patch("urllib.request.urlopen") as urlopen_mock:
            result = documents.download_and_extract_image("http://127.0.0.1/notice.jpg")
        urlopen_mock.assert_not_called()
        self.assertEqual(result["extraction_status"], "rejected_unsafe_url")


class DownloadAndExtractTests(unittest.TestCase):
    def _mock_response(self, data):
        return _mock_response(data)

    def test_successful_pdf_is_extracted(self):
        """The plain pypdf path (Milestone 3), independent of OCR -- explicitly forced off here so
        this test's outcome doesn't depend on OCR_ENABLED in whatever environment it runs in (e.g.
        a developer's local .env with OCR turned on for real testing, per Milestone 15)."""
        pdf_bytes = _make_pdf_bytes()
        with mock.patch("urllib.request.urlopen", return_value=self._mock_response(pdf_bytes)), \
             mock.patch("tender_monitor.ocr.available", return_value=False):
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

    def test_scanned_pdf_stays_empty_text_likely_scanned_when_ocr_unavailable(self):
        """The pre-Milestone-14 outcome, unchanged: with ocr.available() false (its actual default
        -- OCR_ENABLED=0), an empty text layer is reported as-is, never silently upgraded."""
        with mock.patch("urllib.request.urlopen", return_value=self._mock_response(_make_scanned_pdf_bytes())), \
             mock.patch("tender_monitor.ocr.available", return_value=False):
            result = documents.download_and_extract("https://example.gov.np/scanned.pdf")
        self.assertEqual(result["extraction_status"], "empty_text_likely_scanned")
        self.assertIsNone(result["extracted_text"])

    def test_scanned_pdf_falls_back_to_ocr_when_available(self):
        with mock.patch("urllib.request.urlopen", return_value=self._mock_response(_make_scanned_pdf_bytes())), \
             mock.patch("tender_monitor.ocr.available", return_value=True), \
             mock.patch("tender_monitor.ocr.pil_image_to_text", return_value="CCTV camera notice"):
            result = documents.download_and_extract("https://example.gov.np/scanned.pdf")
        self.assertEqual(result["extraction_status"], "ok_ocr")
        self.assertEqual(result["extracted_text"], "CCTV camera notice")

    def test_scanned_pdf_ocr_finding_nothing_is_reported_distinctly(self):
        """Different status than "never tried" (empty_text_likely_scanned above) -- OCR ran and
        still found nothing, which is worth distinguishing when reading extraction_status later."""
        with mock.patch("urllib.request.urlopen", return_value=self._mock_response(_make_scanned_pdf_bytes())), \
             mock.patch("tender_monitor.ocr.available", return_value=True), \
             mock.patch("tender_monitor.ocr.pil_image_to_text", return_value=""):
            result = documents.download_and_extract("https://example.gov.np/scanned.pdf")
        self.assertEqual(result["extraction_status"], "empty_after_ocr")


class DownloadAndExtractImageTests(unittest.TestCase):
    def _png_bytes(self):
        from PIL import Image
        buf = BytesIO()
        Image.new("RGB", (10, 10), "white").save(buf, format="PNG")
        return buf.getvalue()

    def test_non_image_content_is_rejected_by_magic_bytes(self):
        with mock.patch("urllib.request.urlopen", return_value=_mock_response(b"<html>not an image</html>")):
            result = documents.download_and_extract_image("https://example.gov.np/fake.jpg")
        self.assertEqual(result["extraction_status"], "not_an_image")

    def test_oversized_image_download_is_rejected_before_full_download(self):
        import os
        os.environ["DOCUMENT_MAX_SIZE_BYTES"] = "10"
        try:
            with mock.patch("urllib.request.urlopen", return_value=_mock_response(b"\xff\xd8\xff" + b"x" * 1000)):
                result = documents.download_and_extract_image("https://example.gov.np/big.jpg")
            self.assertEqual(result["extraction_status"], "rejected_too_large")
        finally:
            os.environ.pop("DOCUMENT_MAX_SIZE_BYTES", None)

    def test_real_image_is_downloaded_and_hashed_even_with_ocr_disabled(self):
        """A genuine, honestly-labeled outcome (see documents.download_and_extract_image's
        docstring) -- the image is still evidenced in the documents table (sha256/size/content_type
        set) even though OCR itself never ran."""
        png_bytes = self._png_bytes()
        with mock.patch("urllib.request.urlopen", return_value=_mock_response(png_bytes)), \
             mock.patch("tender_monitor.ocr.available", return_value=False):
            result = documents.download_and_extract_image("https://example.gov.np/notice.png")
        self.assertEqual(result["extraction_status"], "ocr_disabled")
        self.assertEqual(result["content_type"], "image/png")
        self.assertEqual(result["size_bytes"], len(png_bytes))
        self.assertIsNotNone(result["sha256"])
        self.assertIsNone(result["extracted_text"])

    def test_ocr_success_populates_extracted_text(self):
        with mock.patch("urllib.request.urlopen", return_value=_mock_response(self._png_bytes())), \
             mock.patch("tender_monitor.ocr.available", return_value=True), \
             mock.patch("tender_monitor.ocr.image_bytes_to_text", return_value="Tender notice text"):
            result = documents.download_and_extract_image("https://example.gov.np/notice.png")
        self.assertEqual(result["extraction_status"], "ok_ocr")
        self.assertEqual(result["extracted_text"], "Tender notice text")

    def test_ocr_finding_nothing_is_reported_distinctly(self):
        with mock.patch("urllib.request.urlopen", return_value=_mock_response(self._png_bytes())), \
             mock.patch("tender_monitor.ocr.available", return_value=True), \
             mock.patch("tender_monitor.ocr.image_bytes_to_text", return_value=""):
            result = documents.download_and_extract_image("https://example.gov.np/notice.png")
        self.assertEqual(result["extraction_status"], "empty_after_ocr")


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


class DiscoverImageLinksTests(unittest.TestCase):
    def test_finds_image_links_only(self):
        html = ('<a href="/notices/scan.jpg">Scanned notice</a>'
                '<a href="/docs/notice.pdf">Not an image</a>'
                '<a href="/n/2">Not an attachment</a>')
        links = documents.discover_image_links(html, "https://x.gov.np/")
        self.assertEqual(links, [("https://x.gov.np/notices/scan.jpg", "Scanned notice")])

    def test_matches_every_supported_extension_case_insensitively(self):
        html = "".join(f'<a href="/n{i}.{ext.upper()}">Notice {i}</a>'
                        for i, ext in enumerate(("jpg", "jpeg", "png", "gif", "bmp", "tif", "tiff", "webp")))
        links = documents.discover_image_links(html, "https://x.gov.np/")
        self.assertEqual(len(links), 8)

    def test_query_string_after_extension_does_not_break_matching(self):
        html = '<a href="/notices/scan.jpg?v=2">Scanned notice</a>'
        links = documents.discover_image_links(html, "https://x.gov.np/")
        self.assertEqual(links, [("https://x.gov.np/notices/scan.jpg?v=2", "Scanned notice")])


class SniffImageContentTypeTests(unittest.TestCase):
    def test_recognizes_jpeg(self):
        self.assertEqual(documents._sniff_image_content_type(b"\xff\xd8\xff\xe0rest"), "image/jpeg")

    def test_recognizes_png(self):
        self.assertEqual(documents._sniff_image_content_type(b"\x89PNG\r\n\x1a\nrest"), "image/png")

    def test_recognizes_webp_by_riff_container_and_tag(self):
        self.assertEqual(documents._sniff_image_content_type(b"RIFF" + b"\x00\x00\x00\x00" + b"WEBPrest"), "image/webp")

    def test_html_disguised_with_an_image_extension_is_not_an_image(self):
        self.assertIsNone(documents._sniff_image_content_type(b"<html>not an image</html>"))


class ExtractSubmissionDeadlineTests(unittest.TestCase):
    def test_finds_date_near_deadline_keyword(self):
        text = "Notice details here. Submission deadline: 2026-09-15. Other text."
        self.assertEqual(documents.extract_submission_deadline(text), "2026-09-15")

    def test_returns_none_without_a_deadline_keyword(self):
        text = "Published on 2026-01-01 with no closing information here."
        self.assertIsNone(documents.extract_submission_deadline(text))


if __name__ == "__main__":
    unittest.main()
