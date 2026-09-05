"""Optional OCR fallback for scanned PDFs and photographed/scanned notice images (Milestone 15).

Off by default (OCR_ENABLED) -- unlike pypdf's pure-Python text extraction (documents.py), OCR
needs the `tesseract` binary installed on the host: a real system dependency this project
specifically avoided adding at Milestone 3 (see documents.py's module docstring and CHANGELOG.md)
until there was a documented reason to pay for it. Every entry point here is best-effort and never
raises: a missing tesseract binary, a corrupt image, or an image Pillow/Tesseract can't read all
degrade to "no OCR text" rather than crashing a collection cycle -- documents.download_and_extract's
own "never raise" contract applies just as much to its OCR fallback.
"""
import os


def enabled():
    return os.getenv("OCR_ENABLED", "0") == "1"


# Whether available() has already probed for a real tesseract binary this process, and what it
# found. Cached rather than re-checked per call: get_tesseract_version() shells out, and
# download_and_extract/_image can be called many times per collection cycle.
_checked = False
_usable = False


def available():
    """True once OCR_ENABLED=1 AND pytesseract/Pillow import cleanly AND the tesseract binary is
    actually callable. Checked lazily, not at module import time, so a deployment that never sets
    OCR_ENABLED never imports pytesseract/Pillow or shells out to probe for a tesseract binary it
    has no use for -- same "gate on the env flag first" posture as ai.configured_provider(), though
    unlike that function this result is cached per process: confirming a real tesseract binary
    means shelling out (get_tesseract_version()), and this can be called many times per cycle."""
    global _checked, _usable
    if not enabled(): return False
    if _checked: return _usable
    _checked = True
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        _usable = True
    except Exception:
        _usable = False
    return _usable


def pil_image_to_text(image):
    """OCR'd text from an already-decoded PIL image (e.g. pypdf's ImageFile.image for one page of
    a scanned PDF), or "" if OCR isn't available/enabled or extraction fails. Never raises."""
    if not available() or image is None: return ""
    try:
        import pytesseract
        # OCR_LANGUAGES lets a deployment that has installed Nepali tessdata (nep, or nep+eng for
        # mixed-script notices) opt into it; the nixpacks.toml tesseract package this project ships
        # only bundles English out of the box, so "eng" is the honest, always-available default.
        return pytesseract.image_to_string(image, lang=os.getenv("OCR_LANGUAGES", "eng")).strip()
    except Exception:
        return ""


def image_bytes_to_text(data):
    """OCR'd text from raw, not-yet-decoded image bytes (a downloaded photo notice). Decodes with
    Pillow first, then delegates to pil_image_to_text. Never raises -- an undecodable/corrupt image
    just yields no text, same as any other best-effort step in this pipeline."""
    if not available() or not data: return ""
    try:
        from io import BytesIO
        from PIL import Image
        with Image.open(BytesIO(data)) as image:
            image.load()  # force full decode now, inside this function's try/except
            return pil_image_to_text(image)
    except Exception:
        return ""
