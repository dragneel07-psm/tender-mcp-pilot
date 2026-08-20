"""Regression tests for the HTTP API -- previously entirely untested (audit §16). Runs a real
ThreadingHTTPServer against an isolated data directory; no test here touches a live government site."""
import base64
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from tender_monitor import collector, storage
from tender_monitor.api import Api


class ApiTestBase(unittest.TestCase):
    """Boots a real server per test against a freshly isolated data dir, so tests can't leak
    state into each other (a source added in one test must not appear in another's /sources)."""
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self._orig_db, self._orig_sources, self._orig_watchlists, self._orig_company_profiles = \
            storage.DB, storage.SOURCES, storage.WATCHLISTS, storage.COMPANY_PROFILES
        storage.DB = Path(self.tmpdir.name) / "test.db"
        storage.SOURCES = Path(self.tmpdir.name) / "sources.json"; storage.SOURCES.write_text("[]")
        storage.WATCHLISTS = Path(self.tmpdir.name) / "watchlists.json"; storage.WATCHLISTS.write_text("[]")
        storage.COMPANY_PROFILES = Path(self.tmpdir.name) / "company_profiles.json"; storage.COMPANY_PROFILES.write_text("[]")
        # config.load_dotenv() already loaded this repo's real .env (including live WhatsApp
        # credentials) into os.environ by the time this process started -- clear everything each
        # test might be sensitive to so tests reflect a clean environment, not this machine's.
        env_keys = ("REQUIRE_AUTH", "APP_USERNAME", "APP_PASSWORD", "HOST",
                    "WHATSAPP_API_URL", "WHATSAPP_ACCESS_TOKEN", "WHATSAPP_RECIPIENT", "WHATSAPP_TEMPLATE_NAME")
        self._orig_env = {k: os.environ.get(k) for k in env_keys}
        os.environ["REQUIRE_AUTH"] = "0"
        for k in env_keys:
            if k != "REQUIRE_AUTH": os.environ.pop(k, None)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Api)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._shutdown)

    def _shutdown(self):
        self.server.shutdown(); self.server.server_close()
        storage.DB, storage.SOURCES, storage.WATCHLISTS, storage.COMPANY_PROFILES = \
            self._orig_db, self._orig_sources, self._orig_watchlists, self._orig_company_profiles
        for k, v in self._orig_env.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v

    def url(self, path): return f"http://127.0.0.1:{self.port}{path}"

    def request(self, method, path, body=None, headers=None, auth=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = dict(headers or {})
        if data is not None: headers["Content-Type"] = "application/json"
        if auth: headers["Authorization"] = "Basic " + base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        req = urllib.request.Request(self.url(path), data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode()) if resp.headers.get_content_type() == "application/json" else resp.read()
        except urllib.error.HTTPError as exc:
            body = exc.read()
            try: return exc.code, json.loads(body.decode())
            except json.JSONDecodeError: return exc.code, body


class HealthAndAuthTests(ApiTestBase):
    def test_health_is_always_reachable(self):
        status, payload = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"status": "ok"})

    def test_unknown_path_is_404(self):
        status, payload = self.request("GET", "/nonexistent")
        self.assertEqual(status, 404)
        self.assertEqual(payload, {"error": "not found"})

    def test_dashboard_root_serves_html(self):
        status, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"<!doctype html", body[:20].lower())

    def test_auth_required_rejects_missing_credentials(self):
        os.environ["REQUIRE_AUTH"] = "1"; os.environ["APP_USERNAME"] = "admin"; os.environ["APP_PASSWORD"] = "secret"
        status, _ = self.request("GET", "/sources")
        self.assertEqual(status, 401)

    def test_auth_required_accepts_correct_credentials(self):
        os.environ["REQUIRE_AUTH"] = "1"; os.environ["APP_USERNAME"] = "admin"; os.environ["APP_PASSWORD"] = "secret"
        status, payload = self.request("GET", "/sources", auth=("admin", "secret"))
        self.assertEqual(status, 200)
        self.assertEqual(payload, [])

    def test_auth_required_rejects_wrong_password(self):
        os.environ["REQUIRE_AUTH"] = "1"; os.environ["APP_USERNAME"] = "admin"; os.environ["APP_PASSWORD"] = "secret"
        status, _ = self.request("GET", "/sources", auth=("admin", "wrong"))
        self.assertEqual(status, 401)

    def test_health_bypasses_auth_even_when_required(self):
        os.environ["REQUIRE_AUTH"] = "1"; os.environ["APP_USERNAME"] = "admin"; os.environ["APP_PASSWORD"] = "secret"
        status, payload = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"status": "ok"})


class SourcesEndpointTests(ApiTestBase):
    def test_empty_sources_list(self):
        status, payload = self.request("GET", "/sources")
        self.assertEqual((status, payload), (200, []))

    def test_create_source_starts_collection_and_appears_in_list(self):
        with mock.patch.object(collector, "collect_one") as collect_mock:
            status, payload = self.request("POST", "/sources", {
                "name": "Example Municipality", "url": "https://example.gov.np",
                "notice_url": "https://example.gov.np/notices",
            })
            self.assertEqual(status, 201)
            self.assertEqual(payload["collection"], "started")
        status, sources = self.request("GET", "/sources")
        self.assertEqual(status, 200)
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["name"], "Example Municipality")

    def test_create_source_rejects_private_url(self):
        status, payload = self.request("POST", "/sources", {
            "name": "Bad", "url": "http://127.0.0.1", "notice_url": "http://127.0.0.1",
        })
        self.assertEqual(status, 400)
        self.assertIn("error", payload)

    def test_duplicate_source_name_is_rejected(self):
        with mock.patch.object(collector, "collect_one"):
            self.request("POST", "/sources", {"name": "Dup", "url": "https://dup.gov.np", "notice_url": "https://dup.gov.np"})
            status, payload = self.request("POST", "/sources", {"name": "Dup", "url": "https://dup.gov.np", "notice_url": "https://dup.gov.np"})
        self.assertEqual(status, 409)

    def test_patch_updates_source(self):
        with mock.patch.object(collector, "collect_one"):
            _, created = self.request("POST", "/sources", {"name": "Editable", "url": "https://ed.gov.np", "notice_url": "https://ed.gov.np"})
        status, updated = self.request("PATCH", f"/sources/{created['id']}", {"favorite": True})
        self.assertEqual(status, 200)
        self.assertTrue(updated["favorite"])

    def test_delete_removes_source(self):
        with mock.patch.object(collector, "collect_one"):
            _, created = self.request("POST", "/sources", {"name": "Removable", "url": "https://rm.gov.np", "notice_url": "https://rm.gov.np"})
        status, _ = self.request("DELETE", f"/sources/{created['id']}")
        self.assertEqual(status, 200)
        _, sources = self.request("GET", "/sources")
        self.assertEqual(sources, [])

    def test_delete_unknown_source_is_404(self):
        status, _ = self.request("DELETE", "/sources/sp-doesnotexist")
        self.assertEqual(status, 404)


class NoticesEndpointTests(ApiTestBase):
    def _seed_notice(self, notice_id="a"*64, source_id="src-1"):
        db = storage.conn()
        db.execute("""insert into notices (id,source_id,authority,title,url,discovered_at,relevant,raw_text)
                   values (?,?,?,?,?,?,?,?)""",
                   (notice_id, source_id, "Authority", "A tender notice", "https://x/1", "2026-01-01T00:00:00+00:00", 1, "A tender notice"))
        db.commit(); db.close()

    def test_list_notices_returns_seeded_row(self):
        self._seed_notice()
        status, payload = self.request("GET", "/notices")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["title"], "A tender notice")

    def test_notice_detail_by_id(self):
        self._seed_notice()
        status, payload = self.request("GET", f"/notices/{'a'*64}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["id"], "a"*64)

    def test_notice_detail_unknown_id_is_404(self):
        status, payload = self.request("GET", f"/notices/{'f'*64}")
        self.assertEqual(status, 404)

    def test_mark_seen_sets_seen_at(self):
        self._seed_notice()
        status, payload = self.request("POST", f"/notices/{'a'*64}/mark-seen")
        self.assertEqual(status, 200)
        self.assertEqual(payload["marked_seen"], 1)
        _, detail = self.request("GET", f"/notices/{'a'*64}")
        self.assertIsNotNone(detail["seen_at"])


class WatchlistsAndCollectionStatusTests(ApiTestBase):
    def test_create_and_delete_watchlist(self):
        status, created = self.request("POST", "/watchlists", {"name": "Priority", "source_ids": []})
        self.assertEqual(status, 201)
        status, listed = self.request("GET", "/watchlists")
        self.assertEqual(len(listed), 1)
        status, _ = self.request("DELETE", f"/watchlists/{created['id']}")
        self.assertEqual(status, 200)

    def test_collection_status_endpoint_returns_a_phase(self):
        status, payload = self.request("GET", "/collection/status")
        self.assertEqual(status, 200)
        self.assertIn("phase", payload)

    def test_alerts_status_reports_not_configured_without_env(self):
        status, payload = self.request("GET", "/alerts/status")
        self.assertEqual(status, 200)
        self.assertFalse(payload["configured"])


class CompanyProfilesAndMatchingTests(ApiTestBase):
    def _seed_categorized_notice(self, notice_id="a"*64, category="Road", province="Sudurpashchim", status="active"):
        db = storage.conn()
        db.execute("""insert into notices (id,source_id,authority,title,url,discovered_at,relevant,raw_text,province,status)
                   values (?,?,?,?,?,?,?,?,?,?)""",
                   (notice_id, "src-1", "Some Municipality", "Construction of rural road", "https://x/1",
                    "2026-01-01T00:00:00+00:00", 1, "Construction of rural road", province, status))
        db.execute("insert into notice_categories values (?,?,?)", (notice_id, category, 0.6))
        db.commit(); db.close()

    def test_create_requires_at_least_one_criterion(self):
        status, payload = self.request("POST", "/company-profiles", {"name": "Acme Corp"})
        self.assertEqual(status, 400)
        self.assertIn("error", payload)

    def test_create_update_delete_company_profile(self):
        status, created = self.request("POST", "/company-profiles", {"name": "Acme Corp", "categories": ["Road"]})
        self.assertEqual(status, 201)
        self.assertTrue(created["id"].startswith("cp-"))
        status, listed = self.request("GET", "/company-profiles")
        self.assertEqual(len(listed), 1)
        status, updated = self.request("PATCH", f"/company-profiles/{created['id']}", {"name": "Acme Corp", "categories": ["Solar"]})
        self.assertEqual(status, 200)
        self.assertEqual(updated["categories"], ["Solar"])
        status, _ = self.request("DELETE", f"/company-profiles/{created['id']}")
        self.assertEqual(status, 200)
        status, listed = self.request("GET", "/company-profiles")
        self.assertEqual(listed, [])

    def test_duplicate_company_profile_name_is_rejected(self):
        self.request("POST", "/company-profiles", {"name": "Acme Corp", "categories": ["Road"]})
        status, payload = self.request("POST", "/company-profiles", {"name": "Acme Corp", "categories": ["Road"]})
        self.assertEqual(status, 409)

    def test_matches_for_unknown_profile_is_404(self):
        status, payload = self.request("GET", "/company-profiles/cp-000000000000/matches")
        self.assertEqual(status, 404)

    def test_matches_ranks_and_explains(self):
        self._seed_categorized_notice()
        _, profile = self.request("POST", "/company-profiles", {"name": "Acme Corp", "categories": ["Road"], "provinces": ["Sudurpashchim"]})
        status, matches = self.request("GET", f"/company-profiles/{profile['id']}/matches")
        self.assertEqual(status, 200)
        self.assertEqual(len(matches), 1)
        self.assertGreater(matches[0]["match_score"], 0)
        dimension_names = {d["dimension"] for d in matches[0]["match_dimensions"]}
        self.assertEqual(dimension_names, {"category", "province"})

    def test_matches_excludes_non_actionable_notices(self):
        self._seed_categorized_notice(status="cancelled")
        _, profile = self.request("POST", "/company-profiles", {"name": "Acme Corp", "categories": ["Road"]})
        status, matches = self.request("GET", f"/company-profiles/{profile['id']}/matches")
        self.assertEqual(status, 200)
        self.assertEqual(matches, [])

    def test_matches_respects_min_score(self):
        self._seed_categorized_notice(category="Solar")  # won't match "Road"
        _, profile = self.request("POST", "/company-profiles", {"name": "Acme Corp", "categories": ["Road"]})
        status, matches = self.request("GET", f"/company-profiles/{profile['id']}/matches?min_score=0.1")
        self.assertEqual(status, 200)
        self.assertEqual(matches, [])


if __name__ == "__main__":
    unittest.main()
