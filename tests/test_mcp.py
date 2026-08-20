"""Regression tests for the MCP JSON-RPC loop -- previously entirely untested (audit §16)."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tender_monitor import mcp_server, queries, storage


class McpResponseTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self._orig_db = storage.DB
        storage.DB = Path(self.tmpdir.name) / "test.db"

    def tearDown(self):
        storage.DB = self._orig_db

    def test_initialize_reports_server_info(self):
        result = mcp_server.mcp_response({"method": "initialize", "params": {}})
        self.assertEqual(result["serverInfo"]["name"], "sudurpashchim-tender-monitor")
        self.assertIn("tools", result["capabilities"])

    def test_tools_list_declares_every_registered_tool(self):
        result = mcp_server.mcp_response({"method": "tools/list", "params": {}})
        names = {tool["name"] for tool in result["tools"]}
        self.assertEqual(names, set(mcp_server.TOOL_HANDLERS.keys()))
        self.assertGreaterEqual(len(names), 10)  # Milestone 8: expanded well past the original 3

    def test_search_tenders_returns_json_encoded_content(self):
        db = storage.conn()
        db.execute("""insert into notices (id,source_id,authority,title,url,discovered_at,relevant,raw_text)
                   values (?,?,?,?,?,?,?,?)""",
                   ("d1", "src", "Authority", "Road tender", "https://x/1", "2026-01-01T00:00:00+00:00", 1, "Road tender"))
        db.commit(); db.close()
        result = mcp_server.mcp_response({"method": "tools/call", "params": {"name": "search_tenders", "arguments": {"query": "road"}}})
        payload = json.loads(result["content"][0]["text"])
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["title"], "Road tender")

    def test_unknown_tool_reports_structured_error_in_content(self):
        result = mcp_server.mcp_response({"method": "tools/call", "params": {"name": "nonexistent", "arguments": {}}})
        payload = json.loads(result["content"][0]["text"])
        self.assertEqual(payload["error"]["code"], "unknown_tool")

    def test_unknown_method_returns_none(self):
        self.assertIsNone(mcp_server.mcp_response({"method": "not/a/real/method", "params": {}}))

    def _seed_notice(self, notice_id="d1"*32):
        db = storage.conn()
        db.execute("""insert into notices (id,source_id,authority,title,url,discovered_at,relevant,raw_text)
                   values (?,?,?,?,?,?,?,?)""",
                   (notice_id, "src", "Authority", "Road tender", "https://x/1", "2026-01-01T00:00:00+00:00", 1, "Road tender"))
        db.commit(); db.close()
        return notice_id

    def _call(self, name, arguments=None):
        result = mcp_server.mcp_response({"method": "tools/call", "params": {"name": name, "arguments": arguments or {}}})
        return json.loads(result["content"][0]["text"])

    def test_tender_details_not_found_is_a_structured_error(self):
        payload = self._call("tender_details", {"id": "f"*64})
        self.assertEqual(payload["error"]["code"], "not_found")

    def test_tender_details_missing_id_is_invalid_argument(self):
        payload = self._call("tender_details", {})
        self.assertEqual(payload["error"]["code"], "invalid_argument")

    def test_tender_documents_for_known_notice(self):
        notice_id = self._seed_notice()
        payload = self._call("tender_documents", {"id": notice_id})
        self.assertEqual(payload, [])

    def test_tender_documents_unknown_notice_is_not_found(self):
        payload = self._call("tender_documents", {"id": "f"*64})
        self.assertEqual(payload["error"]["code"], "not_found")

    def test_tender_changes_for_known_notice(self):
        notice_id = self._seed_notice()
        payload = self._call("tender_changes", {"id": notice_id})
        self.assertEqual(payload, [])

    def test_source_health_returns_a_list(self):
        with mock.patch.object(storage, "sources", return_value=[]):
            payload = self._call("source_health")
        self.assertEqual(payload, [])

    def test_collection_status_returns_a_phase(self):
        payload = self._call("collection_status")
        self.assertIn("phase", payload)

    def test_list_watchlists_returns_a_list(self):
        with mock.patch.object(storage, "watchlists", return_value=[]):
            payload = self._call("list_watchlists")
        self.assertEqual(payload, [])

    def test_watchlist_notices_unknown_id_is_not_found(self):
        with mock.patch.object(storage, "watchlists", return_value=[]):
            payload = self._call("watchlist_notices", {"id": "wl-unknown"})
        self.assertEqual(payload["error"]["code"], "not_found")

    def test_list_company_profiles_returns_a_list(self):
        with mock.patch.object(storage, "company_profiles", return_value=[]):
            payload = self._call("list_company_profiles")
        self.assertEqual(payload, [])

    def test_match_tenders_to_company_missing_id_is_invalid_argument(self):
        payload = self._call("match_tenders_to_company", {})
        self.assertEqual(payload["error"]["code"], "invalid_argument")

    def test_match_tenders_to_company_unknown_profile_is_not_found(self):
        with mock.patch.object(storage, "company_profiles", return_value=[]):
            payload = self._call("match_tenders_to_company", {"company_profile_id": "cp-unknown"})
        self.assertEqual(payload["error"]["code"], "not_found")

    def test_handler_exception_becomes_internal_error_not_a_crash(self):
        with mock.patch.object(queries, "list_notices", side_effect=RuntimeError("boom")):
            payload = self._call("search_tenders", {"query": "road"})
        self.assertEqual(payload["error"]["code"], "internal_error")


class McpStdioLoopTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self._orig_db = storage.DB
        storage.DB = Path(self.tmpdir.name) / "test.db"

    def tearDown(self):
        storage.DB = self._orig_db

    def test_valid_request_writes_jsonrpc_result_to_stdout(self):
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        with mock.patch("sys.stdin", io.StringIO(request + "\n")), mock.patch("sys.stdout", io.StringIO()) as out:
            mcp_server.mcp()
        response = json.loads(out.getvalue())
        self.assertEqual(response["id"], 1)
        self.assertIn("result", response)

    def test_malformed_json_line_reports_jsonrpc_error_not_a_crash(self):
        with mock.patch("sys.stdin", io.StringIO("not json at all\n")), mock.patch("sys.stdout", io.StringIO()) as out:
            mcp_server.mcp()  # must not raise
        response = json.loads(out.getvalue())
        self.assertEqual(response["error"]["code"], -32603)

    def test_notification_without_id_produces_no_output(self):
        request = json.dumps({"method": "initialize", "params": {}})  # no "id" -> a notification
        with mock.patch("sys.stdin", io.StringIO(request + "\n")), mock.patch("sys.stdout", io.StringIO()) as out:
            mcp_server.mcp()
        self.assertEqual(out.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
