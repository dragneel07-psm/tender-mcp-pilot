"""Document discovery, safe download, and text extraction (Milestone 3).

Deliberately narrow scope: PDF only (the dominant format observed on these sites so far), no ZIP/
DOC/DOCX/XLS yet, no raw-bytes persistence (text + metadata only), and only submission_deadline
gets back-populated onto notices -- estimated_amount/bid_security_amount/eligibility are NOT
extracted here. A tender document routinely states several monetary figures (document fee, bid
security, estimated cost); picking the right one via regex without real NLP is unreliable, and a
wrong-but-confident number is worse than none (never fabricate). Deferred to Milestone 10 (AI) or
a dedicated pass once there's a way to attach a real confidence/provenance story to it.
"""
import hashlib
import os
import urllib.error
import urllib.parse
import urllib.request
from io import BytesIO

import pypdf

from . import net
from .parsing import LinkTextParser, clean, first_date

PDF_MAGIC = b"%PDF-"
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


def download_and_extract(url):
    """Download a PDF (SSRF-checked, size-capped, magic-byte-verified) and extract its text. Never
    raises -- always returns a dict describing the outcome, even on failure, so one bad document
    can't take down a whole source's collection cycle (same principle as collector.collect_one)."""
    result = {"url": url, "sha256": None, "size_bytes": None, "content_type": "application/pdf",
              "extracted_text": None, "extraction_status": "failed"}
    if not net.is_safe_public_url(url):
        result["extraction_status"] = "rejected_unsafe_url"
        return result
    max_size = int(os.getenv("DOCUMENT_MAX_SIZE_BYTES", str(15 * 1024 * 1024)))
    timeout = int(os.getenv("DOCUMENT_DOWNLOAD_TIMEOUT_SECONDS", "20"))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": net.USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            chunks = []; total = 0
            while True:
                chunk = response.read(65536)
                if not chunk: break
                total += len(chunk)
                if total > max_size:
                    result["extraction_status"] = "rejected_too_large"
                    return result
                chunks.append(chunk)
            data = b"".join(chunks)
    except (urllib.error.URLError, TimeoutError, ValueError):
        result["extraction_status"] = "download_failed"
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
        result["extracted_text"] = text[:20000]  # bound stored size
        # An empty result after a successful parse usually means a scanned/image-only PDF -- we
        # don't OCR (see module docstring), so this is a legitimate outcome, not necessarily a bug.
        result["extraction_status"] = "ok" if text.strip() else "empty_text_likely_scanned"
    except Exception:
        result["extraction_status"] = "parse_failed"
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
