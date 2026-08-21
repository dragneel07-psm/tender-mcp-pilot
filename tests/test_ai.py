"""Milestone 10: AI provider abstraction. No real network call is ever made here -- urlopen is
always mocked, and configured_provider() is exercised with real (but empty) os.environ state."""
import json
import os
import unittest
from unittest import mock

from tender_monitor import ai

AI_KEYS = ("ANTHROPIC_API_KEY", "AI_PROVIDER", "AI_MODEL")


class AiTestBase(unittest.TestCase):
    def setUp(self):
        self._orig_env = {k: os.environ.get(k) for k in AI_KEYS}
        for k in AI_KEYS: os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._orig_env.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v


class AnthropicProviderTests(AiTestBase):
    def test_unconfigured_without_api_key(self):
        self.assertFalse(ai.AnthropicProvider().is_configured())

    def test_configured_with_api_key(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-test"
        self.assertTrue(ai.AnthropicProvider().is_configured())

    def test_extract_without_api_key_is_not_configured(self):
        result = ai.AnthropicProvider().extract("some document text")
        self.assertEqual(result["status"], "not_configured")

    def _fake_response(self, text_payload):
        class FakeResponse:
            def read(self): return json.dumps({"content": [{"type": "text", "text": text_payload}]}).encode()
            def __enter__(self): return self
            def __exit__(self, *a): return False
        return FakeResponse()

    def test_extract_parses_well_formed_json_response(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-test"
        payload = json.dumps({"estimated_amount": "NPR 5,000,000", "bid_security_amount": "NPR 100,000", "eligibility_summary": "Registered contractors only."})
        with mock.patch("urllib.request.urlopen", return_value=self._fake_response(payload)):
            result = ai.AnthropicProvider().extract("Estimated cost is NPR 5,000,000...")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["estimated_amount"], "NPR 5,000,000")
        self.assertEqual(result["bid_security_amount"], "NPR 100,000")
        self.assertEqual(result["eligibility_summary"], "Registered contractors only.")

    def test_extract_nulls_are_preserved_not_fabricated(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-test"
        payload = json.dumps({"estimated_amount": None, "bid_security_amount": None, "eligibility_summary": None})
        with mock.patch("urllib.request.urlopen", return_value=self._fake_response(payload)):
            result = ai.AnthropicProvider().extract("A document with no stated amounts.")
        self.assertEqual(result["status"], "ok")
        self.assertIsNone(result["estimated_amount"])
        self.assertIsNone(result["bid_security_amount"])
        self.assertIsNone(result["eligibility_summary"])

    def test_extract_malformed_json_is_parse_failed_not_a_crash(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-test"
        with mock.patch("urllib.request.urlopen", return_value=self._fake_response("not json at all")):
            result = ai.AnthropicProvider().extract("some text")
        self.assertEqual(result["status"], "parse_failed")

    def test_extract_non_object_json_is_parse_failed(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-test"
        with mock.patch("urllib.request.urlopen", return_value=self._fake_response("[1,2,3]")):
            result = ai.AnthropicProvider().extract("some text")
        self.assertEqual(result["status"], "parse_failed")

    def test_extract_network_error_is_error_not_a_crash(self):
        import urllib.error
        os.environ["ANTHROPIC_API_KEY"] = "sk-test"
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")):
            result = ai.AnthropicProvider().extract("some text")
        self.assertEqual(result["status"], "error")

    def test_extract_http_error_is_error_not_a_crash(self):
        import io
        import urllib.error
        os.environ["ANTHROPIC_API_KEY"] = "sk-test"
        exc = urllib.error.HTTPError("url", 401, "unauthorized", {}, io.BytesIO(b'{"error":"bad key"}'))
        with mock.patch("urllib.request.urlopen", side_effect=exc):
            result = ai.AnthropicProvider().extract("some text")
        self.assertEqual(result["status"], "error")

    def test_overlong_field_is_truncated_not_fabricated_longer(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-test"
        long_value = "x" * 5000
        payload = json.dumps({"estimated_amount": long_value, "bid_security_amount": None, "eligibility_summary": None})
        with mock.patch("urllib.request.urlopen", return_value=self._fake_response(payload)):
            result = ai.AnthropicProvider().extract("some text")
        self.assertEqual(len(result["estimated_amount"]), ai.MAX_FIELD_CHARS)


class ConfiguredProviderTests(AiTestBase):
    def test_no_provider_configured_returns_none(self):
        self.assertIsNone(ai.configured_provider())

    def test_unknown_provider_name_returns_none(self):
        os.environ["AI_PROVIDER"] = "not-a-real-provider"
        os.environ["ANTHROPIC_API_KEY"] = "sk-test"
        self.assertIsNone(ai.configured_provider())

    def test_named_provider_without_api_key_returns_none(self):
        os.environ["AI_PROVIDER"] = "anthropic"
        self.assertIsNone(ai.configured_provider())

    def test_named_and_configured_provider_is_returned(self):
        os.environ["AI_PROVIDER"] = "anthropic"
        os.environ["ANTHROPIC_API_KEY"] = "sk-test"
        provider = ai.configured_provider()
        self.assertIsInstance(provider, ai.AnthropicProvider)

    def test_provider_name_is_case_insensitive(self):
        os.environ["AI_PROVIDER"] = "Anthropic"
        os.environ["ANTHROPIC_API_KEY"] = "sk-test"
        self.assertIsInstance(ai.configured_provider(), ai.AnthropicProvider)


if __name__ == "__main__":
    unittest.main()
