"""Document discovery, safe download, and text extraction (Milestone 3; OCR fallback Milestone 15).

Deliberately narrow scope: PDF and (Milestone 15) common image formats only -- no ZIP/DOC/DOCX/XLS
yet, no raw-bytes persistence (text + metadata only), and only submission_deadline gets
back-populated onto notices -- estimated_amount/bid_security_amount/eligibility are NOT extracted
here. A tender document routinely states several monetary figures (document fee, bid security,
estimated cost); picking the right one via regex without real NLP is unreliable, and a
wrong-but-confident number is worse than none (never fabricate). Deferred to Milestone 10 (AI) or
a dedicated pass once there's a way to attach a real confidence/provenance story to it.

Milestone 15: a PDF with no text layer (a scan) and a directly-attached photo of a notice board are
both handled the same way once download_and_extract{,_image} determine that's what they have --
OCR via ocr.py, off by default (OCR_ENABLED) since it needs the tesseract binary as a real system
dependency, unlike pypdf's pure-Python text extraction. See ocr.py's module docstring.
"""
import hashlib
import os
import urllib.error
import urllib.parse
import urllib.request
from io import BytesIO

import pypdf

from . import net, ocr
from .parsing import LinkTextParser, clean, first_date

PDF_MAGIC = b"%PDF-"
# Checked against actual downloaded bytes, not the URL's extension -- same anti-spoofing posture as
# PDF_MAGIC above (a URL ending in .jpg proves nothing about what a server actually serves).
# WEBP's real signature is the 4-byte "RIFF" container plus a "WEBP" tag at offset 8, checked
# separately in _sniff_image_content_type rather than as one fixed prefix here.
IMAGE_MAGIC = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
)
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp")
DEADLINE_KEYWORDS = ("submission deadline", "last date", "closing date", "deadline",
                     "म्याद", "अन्तिम मिति", "अन्तिम दिन")

DOCUMENT_TYPE_KEYWORDS = (
    ("boq", ("boq", "bill of quantity", "bill of quantities")),
    ("addendum", ("addendum",)),
    ("corrigendum", ("corrigendum",)),
    ("technical_specification", ("specification", "spec")),
    ("eoi", ("eoi", "expression of interest")),
    ("rfp", ("rfp", "request for proposal")),
    ("rfq", ("rfq", "request for quotation")),
)


def classify_document_type(link_text):
    lower = (link_text or "").lower()
    for doc_type, keywords in DOCUMENT_TYPE_KEYWORDS:
        if any(k in lower for k in keywords): return doc_type
    return "tender_notice"


def discover_pdf_links(page_html, base_url):
    """Every <a> tag in page_html whose href points to a .pdf, as (absolute_url, link_text)."""
    parser = LinkTextParser(); parser.feed(page_html)
    found = []
    for href, text in parser.links:
        if href.lower().split("?", 1)[0].endswith(".pdf"):
            found.append((urllib.parse.urljoin(base_url, href), text))
    return found


def discover_image_links(page_html, base_url):
    """Every <a> tag in page_html whose href points to an image file, as (absolute_url,
    link_text) -- the photo-notice counterpart to discover_pdf_links above, same scoping choice:
    only explicit attachment links, not every decorative <img> tag on the page (a notice board
    photo embedded inline with no wrapping <a> isn't picked up by this pass)."""
    parser = LinkTextParser(); parser.feed(page_html)
    found = []
    for href, text in parser.links:
        if href.lower().split("?", 1)[0].endswith(IMAGE_EXTENSIONS):
            found.append((urllib.parse.urljoin(base_url, href), text))
    return found


def _sniff_image_content_type(data):
    """The real image format from magic bytes, or None if `data` isn't a recognized image at all
    -- never trusts a URL's extension (see IMAGE_MAGIC's comment)."""
    for magic, content_type in IMAGE_MAGIC:
        if data.startswith(magic): return content_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP": return "image/webp"
    return None


def _download(url, max_size, timeout):
    """Shared safe-download core for download_and_extract (PDF) and download_and_extract_image:
    SSRF-checked, streamed with a hard size cap (aborts mid-download rather than reading an
    oversized body into memory first). Returns (data, None) on success or (None, status) naming
    why it failed, so each caller builds its own typed result dict around a status vocabulary
    specific to its content type."""
    if not net.is_safe_public_url(url): return None, "rejected_unsafe_url"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": net.USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            chunks = []; total = 0
            while True:
                chunk = response.read(65536)
                if not chunk: break
                total += len(chunk)
                if total > max_size: return None, "rejected_too_large"
                chunks.append(chunk)
            return b"".join(chunks), None
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None, "download_failed"


def _ocr_scanned_pdf(reader):
    """OCR fallback for a PDF whose text layer came back empty (download_and_extract below), only
    ever reached once ocr.available() is already true. Pulls each page's embedded raster image(s)
    via pypdf's own page.images -- no poppler/PyMuPDF page-rendering dependency needed, since a
    scanned PDF is almost always one full-page image per page -- and OCRs each. Bounded by
    OCR_MAX_PAGES since OCR is CPU-heavy; best-effort like everything else here: a page pypdf can't
    pull images from, or an image with no image_file.image, just contributes no text, not an
    error."""
    max_pages = max(0, int(os.getenv("OCR_MAX_PAGES", "5")))
    parts = []
    for i, page in enumerate(reader.pages):
        if i >= max_pages: break
        try:
            images = page.images
        except Exception:
            continue
        for image_file in images:
            text = ocr.pil_image_to_text(image_file.image)
            if text: parts.append(text)
    return clean(" ".join(parts))


def download_and_extract(url):
    """Download a PDF (SSRF-checked, size-capped, magic-byte-verified) and extract its text. Never
    raises -- always returns a dict describing the outcome, even on failure, so one bad document
    can't take down a whole source's collection cycle (same principle as collector.collect_one)."""
    result = {"url": url, "sha256": None, "size_bytes": None, "content_type": "application/pdf",
              "extracted_text": None, "extraction_status": "failed"}
    max_size = int(os.getenv("DOCUMENT_MAX_SIZE_BYTES", str(15 * 1024 * 1024)))
    timeout = int(os.getenv("DOCUMENT_DOWNLOAD_TIMEOUT_SECONDS", "20"))
    data, error = _download(url, max_size, timeout)
    if error:
        result["extraction_status"] = error
        return result
    result["size_bytes"] = len(data)
    result["sha256"] = hashlib.sha256(data).hexdigest()
    if not data.startswith(PDF_MAGIC):
        result["extraction_status"] = "not_a_pdf"
        return result
    try:
        reader = pypdf.PdfReader(BytesIO(data))
        parts = []
        for i, page in enumerate(reader.pages):
            if i >= 50: break  # bound extraction time on unusually large documents
            parts.append(page.extract_text() or "")
        text = clean(" ".join(parts))
        if text.strip():
            result["extracted_text"] = text[:20000]  # bound stored size
            result["extraction_status"] = "ok"
            return result
        # An empty text layer usually means a scanned/image-only PDF. OCR it if available (see
        # ocr.py) rather than immediately giving up -- but with OCR unavailable/disabled, this is
        # still a legitimate, honestly-labeled outcome (empty_text_likely_scanned), not a bug.
        if ocr.available():
            ocr_text = _ocr_scanned_pdf(reader)
            if ocr_text:
                result["extracted_text"] = ocr_text[:20000]
                result["extraction_status"] = "ok_ocr"
                return result
            result["extraction_status"] = "empty_after_ocr"
            return result
        result["extraction_status"] = "empty_text_likely_scanned"
        return result
    except Exception:
        result["extraction_status"] = "parse_failed"
        return result


def download_and_extract_image(url):
    """Download an image (SSRF-checked, size-capped, magic-byte-verified) and OCR it -- the
    photo-notice counterpart to download_and_extract's PDF path. Never raises. OCR is gated by
    OCR_ENABLED (see ocr.py); with it off (or the tesseract binary missing), the image is still
    downloaded/verified/hashed for the documents table -- so it shows up as evidence, same as a
    PDF that failed to parse would -- just with no extracted_text and an honest "ocr_disabled"
    status rather than pretending extraction was attempted."""
    result = {"url": url, "sha256": None, "size_bytes": None, "content_type": None,
              "extracted_text": None, "extraction_status": "failed"}
    max_size = int(os.getenv("DOCUMENT_MAX_SIZE_BYTES", str(15 * 1024 * 1024)))
    timeout = int(os.getenv("DOCUMENT_DOWNLOAD_TIMEOUT_SECONDS", "20"))
    data, error = _download(url, max_size, timeout)
    if error:
        result["extraction_status"] = error
        return result
    result["size_bytes"] = len(data)
    result["sha256"] = hashlib.sha256(data).hexdigest()
    content_type = _sniff_image_content_type(data)
    if not content_type:
        result["extraction_status"] = "not_an_image"
        return result
    result["content_type"] = content_type
    if not ocr.available():
        result["extraction_status"] = "ocr_disabled"
        return result
    text = ocr.image_bytes_to_text(data)
    if text:
        result["extracted_text"] = text[:20000]
        result["extraction_status"] = "ok_ocr"
        return result
    result["extraction_status"] = "empty_after_ocr"
    return result


def extract_submission_deadline(text):
    """A date is only trusted as a deadline when it appears near an explicit deadline-indicating
    keyword -- not just any date found anywhere in the document (never fabricate). Known
    limitation, same class as published_date()'s existing text-proximity heuristic: this has no
    real sentence-level understanding, so a negating phrase like "no deadline change in this
    addendum" still matches on the keyword. Best-effort, not authoritative -- same caveat that has
    applied to published_at in production all along."""
    if not text: return None
    lower = text.lower()
    for keyword in DEADLINE_KEYWORDS:
        idx = lower.find(keyword.lower())
        if idx == -1: continue
        date = first_date(text[max(0, idx - 50):idx + 150])
        if date: return date
    return None
