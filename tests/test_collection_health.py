import os
import tempfile
import unittest
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
