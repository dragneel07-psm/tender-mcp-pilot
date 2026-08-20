import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import app


class CollectionHealthTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self._orig_db = app.DB
        app.DB = Path(self.tmpdir.name) / "test.db"
        os.environ["SOURCE_FAILURE_SKIP_THRESHOLD"] = "3"
        os.environ["SOURCE_FAILURE_SKIP_COOLDOWN_MINUTES"] = "360"

    def tearDown(self):
        app.DB = self._orig_db
        os.environ.pop("SOURCE_FAILURE_SKIP_THRESHOLD", None)
        os.environ.pop("SOURCE_FAILURE_SKIP_COOLDOWN_MINUTES", None)

    def source(self):
        return {"id": "test-source", "name": "Test Municipality", "url": "https://example.gov.np",
                "notice_url": "https://example.gov.np/notices", "keywords": []}

    def test_failure_increments_consecutive_streak(self):
        with mock.patch.object(app, "fetch", side_effect=app.urllib.error.URLError("boom")):
            app.collect_one(self.source())
            app.collect_one(self.source())
        db = app.conn()
        row = db.execute("select consecutive_failures, last_status from source_health where source_id=?",
                          ("test-source",)).fetchone()
        db.close()
        self.assertEqual(row["consecutive_failures"], 2)
        self.assertEqual(row["last_status"], "error")

    def test_success_resets_streak(self):
        with mock.patch.object(app, "fetch", side_effect=app.urllib.error.URLError("boom")):
            app.collect_one(self.source())
        with mock.patch.object(app, "fetch", return_value="<html></html>"):
            app.collect_one(self.source())
        db = app.conn()
        row = db.execute("select consecutive_failures, last_status from source_health where source_id=?",
                          ("test-source",)).fetchone()
        db.close()
        self.assertEqual(row["consecutive_failures"], 0)
        self.assertEqual(row["last_status"], "ok")

    def test_scheduled_sweep_skips_after_threshold(self):
        src = self.source()
        with mock.patch.object(app, "sources", return_value=[src]):
            with mock.patch.object(app, "fetch", side_effect=app.urllib.error.URLError("boom")):
                for _ in range(3):
                    app.collect_all()
            with mock.patch.object(app, "fetch", return_value="<html></html>") as fetch_mock:
                results = app.collect_all()
                fetch_mock.assert_not_called()
        self.assertEqual(results[0]["status"], "skipped")

    def test_high_concurrency_does_not_crash_or_lock(self):
        """Regression test: raising COLLECTOR_WORKERS once caused concurrent sqlite writers to hit
        'database is locked', which escaped collect_one and killed the whole scheduler thread. Every
        source must finish with a clean 'ok' result under heavy concurrency, no exceptions surfaced."""
        sample_html = '<a href="/notices/1">Tender notice for road construction bolpatra</a>'
        sources_list = [{"id": f"src-{i}", "name": f"Source {i}", "url": "https://example.gov.np",
                          "notice_url": "https://example.gov.np/notices", "keywords": []} for i in range(60)]
        with mock.patch.object(app, "fetch", return_value=sample_html):
            with ThreadPoolExecutor(max_workers=40) as pool:
                results = list(pool.map(app.collect_one, sources_list))
        self.assertEqual(len(results), 60)
        for result in results:
            self.assertEqual(result["status"], "ok")
        db = app.conn()
        total = db.execute("select count(*) from notices").fetchone()[0]
        db.close()
        self.assertEqual(total, 60)  # each source's notice has a distinct digest (source id is part of it)

    def test_per_notice_lookups_are_capped(self):
        """Regression test: one source with many notices lacking a listing-page date used to run a
        per-notice-page lookup for every single one, serially, each with the full network retry
        budget -- a handful of slow notice pages could stall a whole collection cycle for tens of
        minutes. Only NOTICE_PAGE_LOOKUP_LIMIT of them should be attempted per cycle."""
        os.environ["NOTICE_PAGE_LOOKUP_LIMIT"] = "5"
        try:
            links = "".join(f'<a href="/notices/{i}">Tender bolpatra notice {i}</a>' for i in range(20))
            call_log = []
            def fake_fetch(url, timeout=None, retries=None):
                call_log.append(url)
                if url == "https://example.gov.np/notices": return links
                return "<html>no date on this page</html>"
            with mock.patch.object(app, "fetch", side_effect=fake_fetch):
                result = app.collect_one(self.source())
            self.assertEqual(result["status"], "ok")
            per_notice_calls = [c for c in call_log if c != "https://example.gov.np/notices"]
            self.assertLessEqual(len(per_notice_calls), 5)
        finally:
            os.environ.pop("NOTICE_PAGE_LOOKUP_LIMIT", None)

    def test_per_notice_lookups_run_concurrently(self):
        # Track how many lookups are ever in flight at once, rather than asserting on wall-clock
        # time (flaky under thread-scheduling jitter) -- directly proves the lookups overlap.
        os.environ["NOTICE_PAGE_LOOKUP_LIMIT"] = "10"
        os.environ["NOTICE_PAGE_LOOKUP_WORKERS"] = "5"
        try:
            links = "".join(f'<a href="/notices/{i}">Tender bolpatra notice {i}</a>' for i in range(10))
            lock = threading.Lock(); state = {"current": 0, "max": 0}
            def fake_fetch(url, timeout=None, retries=None):
                if url == "https://example.gov.np/notices": return links
                with lock:
                    state["current"] += 1; state["max"] = max(state["max"], state["current"])
                time.sleep(0.05)
                with lock: state["current"] -= 1
                return "<html>no date on this page</html>"
            with mock.patch.object(app, "fetch", side_effect=fake_fetch):
                result = app.collect_one(self.source())
            self.assertEqual(result["status"], "ok")
            self.assertGreater(state["max"], 1, "lookups ran one at a time instead of overlapping")
        finally:
            os.environ.pop("NOTICE_PAGE_LOOKUP_LIMIT", None)
            os.environ.pop("NOTICE_PAGE_LOOKUP_WORKERS", None)

    def test_manual_single_source_collect_ignores_skip(self):
        src = self.source()
        with mock.patch.object(app, "sources", return_value=[src]):
            with mock.patch.object(app, "fetch", side_effect=app.urllib.error.URLError("boom")):
                for _ in range(3):
                    app.collect_all()
            with mock.patch.object(app, "fetch", return_value="<html></html>") as fetch_mock:
                results = app.collect_all(source_id="test-source")
                fetch_mock.assert_called_once()
        self.assertEqual(results[0]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
