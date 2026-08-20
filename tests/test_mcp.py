"""Regression tests for the MCP JSON-RPC loop -- previously entirely untested (audit §16)."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tender_monitor import mcp_server, storage


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

    def test_tools_list_declares_all_three_tools(self):
        result = mcp_server.mcp_response({"method": "tools/list", "params": {}})
        names = {tool["name"] for tool in result["tools"]}
        self.assertEqual(names, {"search_tenders", "latest_tenders", "tender_details"})

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

    def test_unknown_tool_reports_error_in_content(self):
        result = mcp_server.mcp_response({"method": "tools/call", "params": {"name": "nonexistent", "arguments": {}}})
        payload = json.loads(result["content"][0]["text"])
        self.assertEqual(payload, {"error": "unknown tool"})

    def test_unknown_method_returns_none(self):
        self.assertIsNone(mcp_server.mcp_response({"method": "not/a/real/method", "params": {}}))


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
