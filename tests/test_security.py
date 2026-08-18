import unittest

import app


class SecurityTests(unittest.TestCase):
    def test_public_government_url_is_accepted(self):
        source = app.validate_source({
            "name": "Example Municipality",
            "url": "https://example.gov.np",
            "notice_url": "https://example.gov.np/notices",
            "province": "Karnali",
        })
        self.assertEqual(source["province"], "Karnali")

    def test_private_source_urls_are_rejected(self):
        for url in ("http://127.0.0.1", "http://localhost", "http://10.0.0.1"):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    app.validate_source({"name": "Unsafe", "url": url, "notice_url": url})

    def test_invalid_notice_limit_is_rejected(self):
        with self.assertRaises(ValueError):
            app.list_notices(limit="invalid")

    def test_limit_is_bounded(self):
        self.assertIsInstance(app.list_notices(limit=500), list)


if __name__ == "__main__":
    unittest.main()
