import os
import tempfile
import threading
import time
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from tender_monitor import collector, documents, net, queries, storage


class CollectionHealthTests(unittest.TestCase):
    # config.load_dotenv() (triggered by importing tender_monitor.collector, above) loads this
    # repo's real .env -- including live WhatsApp Business credentials -- into os.environ for the
    # whole process. Several tests below insert a genuinely new notice through the real collect_one
    # path, which fires an alert for every new notice. Without clearing these, that alert send is
    # NOT a mock: it is a real HTTPS call to Meta's Graph API with this project's real access token,
    # capable of delivering a real WhatsApp message with fabricated test content to the real
    # configured recipient. Clearing them makes send_whatsapp_alert take its own designed
    # "not configured" no-op path (alerts.py) -- the same path production takes when unconfigured.
    WHATSAPP_KEYS = ("WHATSAPP_API_URL", "WHATSAPP_ACCESS_TOKEN", "WHATSAPP_RECIPIENT", "WHATSAPP_TEMPLATE_NAME")

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self._orig_db = storage.DB
        storage.DB = Path(self.tmpdir.name) / "test.db"
        os.environ["SOURCE_FAILURE_SKIP_THRESHOLD"] = "3"
        os.environ["SOURCE_FAILURE_SKIP_COOLDOWN_MINUTES"] = "360"
        self._orig_whatsapp_env = {k: os.environ.get(k) for k in self.WHATSAPP_KEYS}
        for k in self.WHATSAPP_KEYS: os.environ.pop(k, None)

    def tearDown(self):
        storage.DB = self._orig_db
        os.environ.pop("SOURCE_FAILURE_SKIP_THRESHOLD", None)
        os.environ.pop("SOURCE_FAILURE_SKIP_COOLDOWN_MINUTES", None)
        for k, v in self._orig_whatsapp_env.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v

    def source(self):
        return {"id": "test-source", "name": "Test Municipality", "url": "https://example.gov.np",
                "notice_url": "https://example.gov.np/notices", "keywords": []}

    def test_failure_increments_consecutive_streak(self):
        with mock.patch.object(net, "fetch", side_effect=urllib.error.URLError("boom")):
            collector.collect_one(self.source())
            collector.collect_one(self.source())
        db = storage.conn()
        row = db.execute("select consecutive_failures, last_status from source_health where source_id=?",
                          ("test-source",)).fetchone()
        db.close()
        self.assertEqual(row["consecutive_failures"], 2)
        self.assertEqual(row["last_status"], "error")

    def test_success_resets_streak(self):
        with mock.patch.object(net, "fetch", side_effect=urllib.error.URLError("boom")):
            collector.collect_one(self.source())
        with mock.patch.object(net, "fetch", return_value="<html></html>"):
            collector.collect_one(self.source())
        db = storage.conn()
        row = db.execute("select consecutive_failures, last_status from source_health where source_id=?",
                          ("test-source",)).fetchone()
        db.close()
        self.assertEqual(row["consecutive_failures"], 0)
        self.assertEqual(row["last_status"], "ok")

    def test_scheduled_sweep_skips_after_threshold(self):
        src = self.source()
        with mock.patch.object(storage, "sources", return_value=[src]):
            with mock.patch.object(net, "fetch", side_effect=urllib.error.URLError("boom")):
                for _ in range(3):
                    collector.collect_all()
            with mock.patch.object(net, "fetch", return_value="<html></html>") as fetch_mock:
                results = collector.collect_all()
                fetch_mock.assert_not_called()
        self.assertEqual(results[0]["status"], "skipped")

    def test_high_concurrency_does_not_crash_or_lock(self):
        """Regression test: raising COLLECTOR_WORKERS once caused concurrent sqlite writers to hit
        'database is locked', which escaped collect_one and killed the whole scheduler thread. Every
        source must finish with a clean 'ok' result under heavy concurrency, no exceptions surfaced."""
        sample_html = '<a href="/notices/1">Tender notice for road construction bolpatra</a>'
        sources_list = [{"id": f"src-{i}", "name": f"Source {i}", "url": "https://example.gov.np",
                          "notice_url": "https://example.gov.np/notices", "keywords": []} for i in range(60)]
        with mock.patch.object(net, "fetch", return_value=sample_html):
            with ThreadPoolExecutor(max_workers=40) as pool:
                results = list(pool.map(collector.collect_one, sources_list))
        self.assertEqual(len(results), 60)
        for result in results:
            self.assertEqual(result["status"], "ok")
        db = storage.conn()
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
            with mock.patch.object(net, "fetch", side_effect=fake_fetch):
                result = collector.collect_one(self.source())
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
            with mock.patch.object(net, "fetch", side_effect=fake_fetch):
                result = collector.collect_one(self.source())
            self.assertEqual(result["status"], "ok")
            self.assertGreater(state["max"], 1, "lookups ran one at a time instead of overlapping")
        finally:
            os.environ.pop("NOTICE_PAGE_LOOKUP_LIMIT", None)
            os.environ.pop("NOTICE_PAGE_LOOKUP_WORKERS", None)

    def test_short_titles_are_filtered_out_even_if_relevant(self):
        html = '<a href="/n/1">Bid now</a>'  # 7 chars: contains a tender word, but under the length floor
        with mock.patch.object(net, "fetch", return_value=html):
            result = collector.collect_one(self.source())
        self.assertEqual(result["new"], 0)

    def test_irrelevant_links_are_filtered_out(self):
        html = '<a href="/n/1">Staff holiday announcement today</a>'
        with mock.patch.object(net, "fetch", return_value=html):
            result = collector.collect_one(self.source())
        self.assertEqual(result["new"], 0)

    def test_relevant_notice_is_stored(self):
        html = '<a href="/n/1">Road construction bolpatra notice</a>'
        with mock.patch.object(net, "fetch", return_value=html):
            result = collector.collect_one(self.source())
        self.assertEqual(result["new"], 1)
        notices = queries.list_notices(source_id="test-source")
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["title"], "Road construction bolpatra notice")

    def test_new_notice_gets_full_milestone2_fields(self):
        html = '<a href="/n/1">Road construction bolpatra notice</a>'
        with mock.patch.object(net, "fetch", return_value=html):
            collector.collect_one(self.source())
        notice = queries.list_notices(source_id="test-source")[0]
        self.assertEqual(notice["organization"], "Test Municipality")
        self.assertIsNone(notice["province"])  # self.source() sets no province
        self.assertIsNone(notice["district"])  # never fabricated -- no data source for this yet
        self.assertEqual(notice["notice_type"], "tender_notice")
        self.assertEqual(notice["status"], "active")
        self.assertIsNotNone(notice["first_seen"])
        self.assertEqual(notice["first_seen"], notice["last_seen"])
        self.assertIsNotNone(notice["content_hash"])
        self.assertIn(notice["confidence_score"], (0.5, 0.7, 0.9))

    def test_new_notice_gets_categorized(self):
        html = '<a href="/n/1">Road construction bolpatra notice</a>'
        with mock.patch.object(net, "fetch", return_value=html):
            collector.collect_one(self.source())
        notice = queries.list_notices(source_id="test-source")[0]
        db = storage.conn()
        rows = db.execute("select category from notice_categories where notice_id=?", (notice["id"],)).fetchall()
        db.close()
        categories = {r["category"] for r in rows}
        self.assertEqual(categories, {"Road", "Civil Construction"})

    def test_province_is_stamped_from_source(self):
        src = self.source(); src["province"] = "Karnali"
        html = '<a href="/n/1">Road construction bolpatra notice</a>'
        with mock.patch.object(net, "fetch", return_value=html):
            collector.collect_one(src)
        notice = queries.list_notices(source_id="test-source")[0]
        self.assertEqual(notice["province"], "Karnali")

    def test_recollecting_updates_last_seen_but_not_first_seen(self):
        html = '<a href="/n/1">Road construction bolpatra notice</a>'
        with mock.patch.object(net, "fetch", return_value=html):
            collector.collect_one(self.source())
        first = queries.list_notices(source_id="test-source")[0]
        with mock.patch.object(net, "fetch", return_value=html):
            collector.collect_one(self.source())
        second = queries.list_notices(source_id="test-source")[0]
        self.assertEqual(second["first_seen"], first["first_seen"])  # immutable once set
        self.assertGreaterEqual(second["last_seen"], first["last_seen"])  # advances on every re-encounter

    def test_cancellation_keyword_sets_notice_type_and_status(self):
        html = '<a href="/n/1">Tender cancelled: road construction bolpatra</a>'
        with mock.patch.object(net, "fetch", return_value=html):
            collector.collect_one(self.source())
        notice = queries.list_notices(source_id="test-source")[0]
        self.assertEqual(notice["notice_type"], "cancellation")
        self.assertEqual(notice["status"], "cancelled")

    def test_recollecting_same_source_does_not_duplicate(self):
        html = '<a href="/n/1">Road construction bolpatra notice</a>'
        with mock.patch.object(net, "fetch", return_value=html):
            collector.collect_one(self.source())
            result = collector.collect_one(self.source())
        self.assertEqual(result["new"], 0)  # already stored; "insert or ignore" adds nothing new
        notices = queries.list_notices(source_id="test-source")
        self.assertEqual(len(notices), 1)

    # -- Milestone 6: change detection -------------------------------------------------------

    def test_first_capture_of_content_hash_is_not_a_change(self):
        html = '<a href="/n/1">Road construction bolpatra notice</a> Published on the notice board.'
        with mock.patch.object(net, "fetch", return_value=html):
            collector.collect_one(self.source())
        notice = queries.list_notices(source_id="test-source")[0]
        self.assertIsNotNone(notice["content_hash"])
        self.assertEqual(queries.notice_changes(notice["id"]), [])

    def test_identical_recollect_records_no_change(self):
        html = '<a href="/n/1">Road construction bolpatra notice</a> Published on the notice board.'
        with mock.patch.object(net, "fetch", return_value=html):
            collector.collect_one(self.source())
            collector.collect_one(self.source())
        notice = queries.list_notices(source_id="test-source")[0]
        self.assertEqual(queries.notice_changes(notice["id"]), [])

    def test_unrelated_snippet_edit_is_recorded_but_not_alerted(self):
        v1 = '<a href="/n/1">Road construction bolpatra notice</a> Published on the notice board.'
        v2 = '<a href="/n/1">Road construction bolpatra notice</a> Please visit the office for more information.'
        with mock.patch.object(net, "fetch", return_value=v1):
            collector.collect_one(self.source())
        with mock.patch.object(net, "fetch", return_value=v2):
            collector.collect_one(self.source())
        notice = queries.list_notices(source_id="test-source")[0]
        changes = queries.notice_changes(notice["id"])
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["change_type"], "listing_changed")
        self.assertEqual(notice["status"], "active")  # unaffected
        db = storage.conn()
        delivery_count = db.execute("select count(*) from deliveries where notice_id=?", (notice["id"],)).fetchone()[0]
        db.close()
        self.assertEqual(delivery_count, 1)  # only the original new-notice alert -- no alert for an unclassified edit

    def test_cancellation_keyword_in_snippet_marks_notice_cancelled_and_alerts(self):
        v1 = '<a href="/n/1">Road construction bolpatra notice</a> Open for bidding.'
        v2 = '<a href="/n/1">Road construction bolpatra notice</a> This tender has been cancelled.'
        with mock.patch.object(net, "fetch", return_value=v1):
            collector.collect_one(self.source())
        with mock.patch.object(net, "fetch", return_value=v2):
            collector.collect_one(self.source())
        notice = queries.list_notices(source_id="test-source")[0]
        self.assertEqual(notice["status"], "cancelled")
        changes = queries.notice_changes(notice["id"])
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["change_type"], "TENDER_CANCELLED")
        db = storage.conn()
        reasons = [r[0] for r in db.execute("select reason from deliveries where notice_id=? order by rowid", (notice["id"],)).fetchall()]
        db.close()
        self.assertEqual(reasons, ["new_notice", "TENDER_CANCELLED"])

    def test_corrigendum_keyword_in_snippet_is_recorded_and_alerts(self):
        v1 = '<a href="/n/1">Road construction bolpatra notice</a> Open for bidding.'
        v2 = '<a href="/n/1">Road construction bolpatra notice</a> Please see the corrigendum for updated details.'
        with mock.patch.object(net, "fetch", return_value=v1):
            collector.collect_one(self.source())
        with mock.patch.object(net, "fetch", return_value=v2):
            collector.collect_one(self.source())
        notice = queries.list_notices(source_id="test-source")[0]
        self.assertEqual(notice["status"], "active")  # corrigendum alone doesn't change status
        changes = queries.notice_changes(notice["id"])
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["change_type"], "CORRIGENDUM")

    def test_deadline_change_in_snippet_updates_published_at_and_alerts(self):
        v1 = '<a href="/n/1">Road construction bolpatra notice</a> Submission deadline: 2026-01-15 for this tender.'
        v2 = '<a href="/n/1">Road construction bolpatra notice</a> Submission deadline: 2026-03-01 for this tender.'
        with mock.patch.object(net, "fetch", return_value=v1):
            collector.collect_one(self.source())
        first = queries.list_notices(source_id="test-source")[0]
        self.assertEqual(first["published_at"], "2026-01-15")
        with mock.patch.object(net, "fetch", return_value=v2):
            collector.collect_one(self.source())
        second = queries.list_notices(source_id="test-source")[0]
        self.assertEqual(second["published_at"], "2026-03-01")
        changes = queries.notice_changes(second["id"])
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["change_type"], "DEADLINE_CHANGED")
        self.assertEqual(changes[0]["previous_value"], "2026-01-15")
        self.assertEqual(changes[0]["new_value"], "2026-03-01")

    def test_first_time_deadline_found_in_snippet_is_not_a_deadline_change(self):
        # No date at all in v1 -- published_at stays null after the first cycle. Finding one for
        # the first time in v2 is new information, not a *change* from a real prior value, so this
        # must not fire DEADLINE_CHANGED (there's nothing honest to compare it against).
        v1 = '<a href="/n/1">Road construction bolpatra notice</a> Open for bidding.'
        v2 = '<a href="/n/1">Road construction bolpatra notice</a> Submission deadline: 2026-03-01 for this tender.'
        with mock.patch.object(net, "fetch", return_value=v1):
            collector.collect_one(self.source())
        self.assertIsNone(queries.list_notices(source_id="test-source")[0]["published_at"])
        with mock.patch.object(net, "fetch", return_value=v2):
            collector.collect_one(self.source())
        notice = queries.list_notices(source_id="test-source")[0]
        changes = queries.notice_changes(notice["id"])
        self.assertNotIn("DEADLINE_CHANGED", [c["change_type"] for c in changes])

    def test_document_processing_is_off_by_default(self):
        html = '<a href="/n/1">Road construction bolpatra notice</a>'
        with mock.patch.object(net, "fetch", return_value=html), \
             mock.patch.object(documents, "download_and_extract") as extract_mock:
            collector.collect_one(self.source())
        extract_mock.assert_not_called()

    def test_document_processing_wires_documents_table_and_deadline_when_enabled(self):
        os.environ["DOCUMENT_PROCESSING_ENABLED"] = "1"
        try:
            listing_html = '<a href="/n/1">Road construction bolpatra notice</a>'
            notice_page_html = '<a href="/docs/notice.pdf">Tender Notice</a>'
            def fake_fetch(url, timeout=None, retries=None):
                if url == "https://example.gov.np/notices": return listing_html
                return notice_page_html
            fake_doc = {"url": "https://example.gov.np/docs/notice.pdf", "sha256": "abc123",
                        "size_bytes": 100, "content_type": "application/pdf",
                        "extracted_text": "Please note the submission deadline: 2026-09-15 for this tender.",
                        "extraction_status": "ok"}
            with mock.patch.object(net, "fetch", side_effect=fake_fetch), \
                 mock.patch.object(documents, "download_and_extract", return_value=dict(fake_doc)):
                result = collector.collect_one(self.source())
            self.assertEqual(result["status"], "ok")
            notice = queries.list_notices(source_id="test-source")[0]
            self.assertEqual(notice["submission_deadline"], "2026-09-15")
            docs = queries.notice_documents(notice["id"])
            self.assertEqual(len(docs), 1)
            self.assertEqual(docs[0]["extraction_status"], "ok")
            self.assertEqual(docs[0]["document_type"], "tender_notice")
        finally:
            os.environ.pop("DOCUMENT_PROCESSING_ENABLED", None)

    def test_list_notices_category_filter(self):
        with mock.patch.object(net, "fetch", return_value='<a href="/n/1">Road construction bolpatra notice</a>'):
            collector.collect_one(self.source())
        self.assertEqual(len(queries.list_notices(category="Road")), 1)
        self.assertEqual(len(queries.list_notices(category="Medical")), 0)

    def test_list_notices_pagination(self):
        for i in range(3):
            src = self.source(); src["id"] = f"test-source-{i}"
            with mock.patch.object(net, "fetch", return_value=f'<a href="/n/{i}">Road construction notice {i} bolpatra</a>'):
                collector.collect_one(src)
        page1 = queries.list_notices(limit=2, offset=0)
        page2 = queries.list_notices(limit=2, offset=2)
        self.assertEqual(len(page1), 2)
        self.assertEqual(len(page2), 1)
        self.assertEqual({n["id"] for n in page1} & {n["id"] for n in page2}, set())

    def test_list_notices_has_documents_filter(self):
        with mock.patch.object(net, "fetch", return_value='<a href="/n/1">Road construction bolpatra notice</a>'):
            collector.collect_one(self.source())
        self.assertEqual(len(queries.list_notices(has_documents=True)), 0)
        self.assertEqual(len(queries.list_notices(has_documents=False)), 1)

    def test_manual_single_source_collect_ignores_skip(self):
        src = self.source()
        with mock.patch.object(storage, "sources", return_value=[src]):
            with mock.patch.object(net, "fetch", side_effect=urllib.error.URLError("boom")):
                for _ in range(3):
                    collector.collect_all()
            with mock.patch.object(net, "fetch", return_value="<html></html>") as fetch_mock:
                results = collector.collect_all(source_id="test-source")
                fetch_mock.assert_called_once()
        self.assertEqual(results[0]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
