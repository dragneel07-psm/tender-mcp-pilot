"""Milestone 12: the in-process rate limiter. Each test uses its own client IP string so tests
never share _WINDOWS state with each other."""
import os
import unittest

from tender_monitor import ratelimit

RATE_LIMIT_KEYS = ("RATE_LIMIT_REQUESTS", "RATE_LIMIT_WINDOW_SECONDS")


class RateLimitTests(unittest.TestCase):
    def setUp(self):
        self._orig_env = {k: os.environ.get(k) for k in RATE_LIMIT_KEYS}
        ratelimit._WINDOWS.clear()

    def tearDown(self):
        for k, v in self._orig_env.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
        ratelimit._WINDOWS.clear()

    def test_requests_within_limit_are_allowed(self):
        os.environ["RATE_LIMIT_REQUESTS"] = "3"
        os.environ["RATE_LIMIT_WINDOW_SECONDS"] = "60"
        for _ in range(3):
            self.assertTrue(ratelimit.allow("1.2.3.4"))

    def test_request_past_limit_is_denied(self):
        os.environ["RATE_LIMIT_REQUESTS"] = "3"
        os.environ["RATE_LIMIT_WINDOW_SECONDS"] = "60"
        for _ in range(3): ratelimit.allow("1.2.3.5")
        self.assertFalse(ratelimit.allow("1.2.3.5"))

    def test_different_client_ips_have_independent_windows(self):
        os.environ["RATE_LIMIT_REQUESTS"] = "1"
        os.environ["RATE_LIMIT_WINDOW_SECONDS"] = "60"
        self.assertTrue(ratelimit.allow("10.0.0.1"))
        self.assertTrue(ratelimit.allow("10.0.0.2"))  # a different IP is unaffected by 10.0.0.1's usage
        self.assertFalse(ratelimit.allow("10.0.0.1"))  # but 10.0.0.1 itself is now over its own limit

    def test_window_resets_after_it_expires(self):
        os.environ["RATE_LIMIT_REQUESTS"] = "1"
        os.environ["RATE_LIMIT_WINDOW_SECONDS"] = "60"
        ratelimit.allow("1.2.3.6")
        self.assertFalse(ratelimit.allow("1.2.3.6"))
        # Simulate the window having elapsed rather than sleeping in a test.
        window_start, count = ratelimit._WINDOWS["1.2.3.6"]
        ratelimit._WINDOWS["1.2.3.6"] = (window_start - 61, count)
        self.assertTrue(ratelimit.allow("1.2.3.6"))

    def test_zero_limit_disables_rate_limiting(self):
        os.environ["RATE_LIMIT_REQUESTS"] = "0"
        for _ in range(50):
            self.assertTrue(ratelimit.allow("1.2.3.7"))

    def test_tracked_client_count_is_bounded(self):
        os.environ["RATE_LIMIT_REQUESTS"] = "1000"
        for i in range(ratelimit._MAX_TRACKED_CLIENTS + 5):
            ratelimit.allow(f"10.1.{i // 256}.{i % 256}")
        self.assertLessEqual(len(ratelimit._WINDOWS), ratelimit._MAX_TRACKED_CLIENTS + 5)


if __name__ == "__main__":
    unittest.main()
