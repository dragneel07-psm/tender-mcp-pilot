"""Tests for the Milestone 15 OCR fallback. Mocked throughout (no real tesseract binary required)
except OcrIntegrationTests, which is skipped unless one is actually installed on this machine --
see documents.py's module docstring for why OCR isn't a hard dependency of this project."""
import os
import shutil
import unittest
from unittest import mock

from tender_monitor import ocr


class OcrTestBase(unittest.TestCase):
    """available()'s result is cached in module globals across calls (see ocr.py) -- reset that
    cache and OCR_ENABLED around every test so none of these can leak state into another."""
    def setUp(self):
        self._orig_checked, self._orig_usable = ocr._checked, ocr._usable
        self._orig_env = os.environ.get("OCR_ENABLED")
        ocr._checked = False; ocr._usable = False
        os.environ.pop("OCR_ENABLED", None)

    def tearDown(self):
        ocr._checked, ocr._usable = self._orig_checked, self._orig_usable
        if self._orig_env is None: os.environ.pop("OCR_ENABLED", None)
        else: os.environ["OCR_ENABLED"] = self._orig_env


class EnabledAndAvailableTests(OcrTestBase):
    def test_disabled_by_default(self):
        self.assertFalse(ocr.enabled())
        self.assertFalse(ocr.available())

    def test_available_is_false_without_ever_probing_tesseract_when_disabled(self):
        with mock.patch("pytesseract.get_tesseract_version") as probe:
            self.assertFalse(ocr.available())
        probe.assert_not_called()

    def test_enabled_but_tesseract_binary_missing(self):
        os.environ["OCR_ENABLED"] = "1"
        with mock.patch("pytesseract.get_tesseract_version", side_effect=FileNotFoundError("no tesseract")):
            self.assertFalse(ocr.available())

    def test_enabled_and_tesseract_present(self):
        os.environ["OCR_ENABLED"] = "1"
        with mock.patch("pytesseract.get_tesseract_version", return_value="5.3.4"):
            self.assertTrue(ocr.available())

    def test_availability_check_is_cached_not_reprobed_every_call(self):
        os.environ["OCR_ENABLED"] = "1"
        with mock.patch("pytesseract.get_tesseract_version", return_value="5.3.4") as probe:
            ocr.available(); ocr.available(); ocr.available()
        probe.assert_called_once()


class PilImageToTextTests(OcrTestBase):
    def test_returns_empty_string_when_ocr_unavailable(self):
        self.assertEqual(ocr.pil_image_to_text(mock.Mock()), "")

    def test_returns_empty_string_for_none_image(self):
        os.environ["OCR_ENABLED"] = "1"
        with mock.patch("pytesseract.get_tesseract_version", return_value="5.3.4"):
            self.assertEqual(ocr.pil_image_to_text(None), "")

    def test_strips_and_returns_recognized_text(self):
        os.environ["OCR_ENABLED"] = "1"
        with mock.patch("pytesseract.get_tesseract_version", return_value="5.3.4"), \
             mock.patch("pytesseract.image_to_string", return_value="  CCTV camera notice \n\n") as ocr_call:
            self.assertEqual(ocr.pil_image_to_text(mock.Mock()), "CCTV camera notice")
        self.assertEqual(ocr_call.call_args.kwargs.get("lang"), "eng")

    def test_respects_ocr_languages_env_var(self):
        os.environ["OCR_ENABLED"] = "1"; os.environ["OCR_LANGUAGES"] = "eng+nep"
        try:
            with mock.patch("pytesseract.get_tesseract_version", return_value="5.3.4"), \
                 mock.patch("pytesseract.image_to_string", return_value="text") as ocr_call:
                ocr.pil_image_to_text(mock.Mock())
            self.assertEqual(ocr_call.call_args.kwargs.get("lang"), "eng+nep")
        finally:
            os.environ.pop("OCR_LANGUAGES", None)

    def test_a_tesseract_exception_is_swallowed_not_raised(self):
        os.environ["OCR_ENABLED"] = "1"
        with mock.patch("pytesseract.get_tesseract_version", return_value="5.3.4"), \
             mock.patch("pytesseract.image_to_string", side_effect=RuntimeError("boom")):
            self.assertEqual(ocr.pil_image_to_text(mock.Mock()), "")


class ImageBytesToTextTests(OcrTestBase):
    def _png_bytes(self):
        from io import BytesIO
        from PIL import Image
        buf = BytesIO()
        Image.new("RGB", (10, 10), "white").save(buf, format="PNG")
        return buf.getvalue()

    def test_returns_empty_string_when_ocr_unavailable(self):
        self.assertEqual(ocr.image_bytes_to_text(self._png_bytes()), "")

    def test_returns_empty_string_for_empty_bytes(self):
        os.environ["OCR_ENABLED"] = "1"
        with mock.patch("pytesseract.get_tesseract_version", return_value="5.3.4"):
            self.assertEqual(ocr.image_bytes_to_text(b""), "")

    def test_decodes_real_image_bytes_and_delegates_to_tesseract(self):
        os.environ["OCR_ENABLED"] = "1"
        with mock.patch("pytesseract.get_tesseract_version", return_value="5.3.4"), \
             mock.patch("pytesseract.image_to_string", return_value="recognized text") as ocr_call:
            result = ocr.image_bytes_to_text(self._png_bytes())
        self.assertEqual(result, "recognized text")
        ocr_call.assert_called_once()

    def test_corrupt_image_bytes_do_not_raise(self):
        os.environ["OCR_ENABLED"] = "1"
        with mock.patch("pytesseract.get_tesseract_version", return_value="5.3.4"):
            self.assertEqual(ocr.image_bytes_to_text(b"not an image at all"), "")


@unittest.skipUnless(shutil.which("tesseract"), "tesseract binary not installed on this machine")
class OcrIntegrationTests(OcrTestBase):
    """Exercises the real tesseract binary end to end -- skipped in any environment (e.g. CI)
    without one installed, same as this project runs with OCR_ENABLED=0 in production until the
    binary is deployed (see nixpacks.toml)."""
    def test_recognizes_text_rendered_into_a_real_image(self):
        from io import BytesIO
        from PIL import Image, ImageDraw, ImageFont
        os.environ["OCR_ENABLED"] = "1"
        image = Image.new("RGB", (900, 120), "white")
        # A large bitmap font, not the default (tiny, unreliable for OCR on a synthetic fixture
        # like this) -- Pillow's built-in default font, scaled up, needs no font file on disk.
        font = ImageFont.load_default(size=40)
        ImageDraw.Draw(image).text((20, 20), "CAMERA NOTICE", fill="black", font=font)
        buf = BytesIO(); image.save(buf, format="PNG")
        text = ocr.image_bytes_to_text(buf.getvalue()).upper()
        self.assertIn("CAMERA", text)
        self.assertIn("NOTICE", text)


if __name__ == "__main__":
    unittest.main()
