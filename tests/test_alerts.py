"""Milestone 7: AlertProvider abstraction. WhatsApp is never actually contacted here -- every test
either leaves it unconfigured (exercising the "skipped" path) or mocks urllib.request.urlopen."""
import os
import unittest
from unittest import mock

from tender_monitor import alerts

WHATSAPP_KEYS = ("WHATSAPP_API_URL", "WHATSAPP_ACCESS_TOKEN", "WHATSAPP_RECIPIENT", "WHATSAPP_TEMPLATE_NAME")


class AlertProviderTests(unittest.TestCase):
    def setUp(self):
        self._orig_env = {k: os.environ.get(k) for k in WHATSAPP_KEYS}
        for k in WHATSAPP_KEYS: os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._orig_env.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v

    def notice(self):
        return {"authority": "Test Municipality", "title": "Road construction bolpatra notice", "url": "https://x/1"}

    def test_unconfigured_provider_skips_without_raising(self):
        status, detail = alerts.send_alert(self.notice())
        self.assertEqual(status, "skipped")
        self.assertIn("not configured", detail)

    def test_is_configured_false_without_env(self):
        self.assertFalse(alerts.WhatsAppAlertProvider().is_configured())

    def test_is_configured_true_with_full_env(self):
        for k in WHATSAPP_KEYS: os.environ[k] = "x"
        self.assertTrue(alerts.WhatsAppAlertProvider().is_configured())

    def test_new_notice_reason_has_no_title_prefix(self):
        for k in WHATSAPP_KEYS: os.environ[k] = "x"
        os.environ["WHATSAPP_API_URL"] = "https://example.invalid/send"
        captured = {}
        def fake_urlopen(request, timeout=None):
            captured["payload"] = request.data
            class FakeResponse:
                def read(self): return b"ok"
                def __enter__(self): return self
                def __exit__(self, *a): return False
            return FakeResponse()
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            status, detail = alerts.send_alert(self.notice(), reason="new_notice")
        self.assertEqual(status, "sent")
        self.assertIn(b'"text": "Road construction bolpatra notice"', captured["payload"])

    def test_change_reason_prefixes_title_without_mutating_input(self):
        for k in WHATSAPP_KEYS: os.environ[k] = "x"
        os.environ["WHATSAPP_API_URL"] = "https://example.invalid/send"
        notice = self.notice()
        captured = {}
        def fake_urlopen(request, timeout=None):
            captured["payload"] = request.data
            class FakeResponse:
                def read(self): return b"ok"
                def __enter__(self): return self
                def __exit__(self, *a): return False
            return FakeResponse()
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            status, detail = alerts.send_alert(notice, reason="TENDER_CANCELLED")
        self.assertEqual(status, "sent")
        self.assertIn(b"[CANCELLED] Road construction bolpatra notice", captured["payload"])
        self.assertEqual(notice["title"], "Road construction bolpatra notice")  # input dict untouched

    def test_unrecognized_reason_falls_back_to_no_prefix(self):
        for k in WHATSAPP_KEYS: os.environ[k] = "x"
        os.environ["WHATSAPP_API_URL"] = "https://example.invalid/send"
        captured = {}
        def fake_urlopen(request, timeout=None):
            captured["payload"] = request.data
            class FakeResponse:
                def read(self): return b"ok"
                def __enter__(self): return self
                def __exit__(self, *a): return False
            return FakeResponse()
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            alerts.send_alert(self.notice(), reason="some_future_reason_nobody_registered_yet")
        self.assertIn(b'"text": "Road construction bolpatra notice"', captured["payload"])


if __name__ == "__main__":
    unittest.main()
