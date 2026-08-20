"""The MCP stdio server: a minimal JSON-RPC loop over stdin/stdout.

Milestone 8 (MCP 2.0): expanded from the original 3 tools now that source health, collection
status, matching, watchlists, and change detection all exist as real backing functions (Milestones
2-7) rather than needing to be stubbed. Every tool follows the same two conventions:
  - pagination (`limit`/`offset`) on every list-shaped tool, delegated to the same clamping
    (1-100) the HTTP API's list_notices()/matches_for_company() already apply -- no new limits
    invented here.
  - structured error objects (`{"error": {"code": ..., "message": ...}}`) instead of a bare string,
    so a client can branch on `code` rather than string-matching a message (audit §8).
"""
import json
import sys

from . import queries, status, storage


def _error(code, message):
    return {"error": {"code": code, "message": message}}


def _paginate(args, default_limit=20):
    return args.get("limit", default_limit), args.get("offset", 0)


def _tool_search_tenders(args):
    limit, offset = _paginate(args)
    return queries.list_notices(
        query=args.get("query", ""), limit=limit, offset=offset,
        province=args.get("province", ""), notice_type=args.get("notice_type", ""),
        status=args.get("status", ""), category=args.get("category", ""))


def _tool_latest_tenders(args):
    limit, offset = _paginate(args)
    return queries.list_notices(limit=limit, offset=offset)


def _tool_tender_details(args):
    notice_id = args.get("id")
    if not notice_id: return _error("invalid_argument", "id is required.")
    result = queries.details(notice_id)
    return result if result else _error("not_found", f"No tender notice with id {notice_id}.")


def _tool_tender_documents(args):
    notice_id = args.get("id")
    if not notice_id: return _error("invalid_argument", "id is required.")
    if not queries.details(notice_id): return _error("not_found", f"No tender notice with id {notice_id}.")
    return queries.notice_documents(notice_id)


def _tool_tender_changes(args):
    notice_id = args.get("id")
    if not notice_id: return _error("invalid_argument", "id is required.")
    if not queries.details(notice_id): return _error("not_found", f"No tender notice with id {notice_id}.")
    return queries.notice_changes(notice_id)


def _tool_source_health(args):
    return queries.source_summary()


def _tool_collection_status(args):
    return status.last_cycle


def _tool_list_watchlists(args):
    return storage.watchlists()


def _tool_watchlist_notices(args):
    watchlist_id = args.get("id")
    if not watchlist_id: return _error("invalid_argument", "id is required.")
    limit, offset = _paginate(args, default_limit=50)
    result = queries.notices_for_watchlist(watchlist_id, limit, offset)
    return result if result is not None else _error("not_found", f"No watchlist with id {watchlist_id}.")


def _tool_list_company_profiles(args):
    return storage.company_profiles()


def _tool_match_tenders_to_company(args):
    profile_id = args.get("company_profile_id")
    if not profile_id: return _error("invalid_argument", "company_profile_id is required.")
    limit, offset = _paginate(args)
    result = queries.matches_for_company(profile_id, limit, offset, args.get("min_score", 0.0))
    return result if result is not None else _error("not_found", f"No company profile with id {profile_id}.")


TOOLS = [
    {"name": "search_tenders", "description": "Search collected tender notices by keyword and filters.",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "Keyword to match against title/authority."},
         "province": {"type": "string"}, "notice_type": {"type": "string"}, "status": {"type": "string"}, "category": {"type": "string"},
         "limit": {"type": "integer", "default": 20}, "offset": {"type": "integer", "default": 0}}}},
    {"name": "latest_tenders", "description": "Return the most recently discovered tender notices.",
     "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 20}, "offset": {"type": "integer", "default": 0}}}},
    {"name": "tender_details", "description": "Retrieve one tender notice by its id, including its categories.",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    {"name": "tender_documents", "description": "List documents discovered for one tender notice.",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    {"name": "tender_changes", "description": "Version history of a tender notice (cancellations, deadline changes, corrigenda).",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    {"name": "source_health", "description": "Health/status summary for every configured source.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "collection_status", "description": "The most recent (or currently running) collection cycle's status.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_watchlists", "description": "List saved watchlists (saved searches).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "watchlist_notices", "description": "Run a saved watchlist's filters and return the notices currently matching it.",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}, "limit": {"type": "integer", "default": 50}, "offset": {"type": "integer", "default": 0}}, "required": ["id"]}},
    {"name": "list_company_profiles", "description": "List saved company matching profiles.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "match_tenders_to_company", "description": "Rank open tenders against a company profile with an explainable per-dimension score.",
     "inputSchema": {"type": "object", "properties": {
         "company_profile_id": {"type": "string"}, "limit": {"type": "integer", "default": 20}, "offset": {"type": "integer", "default": 0},
         "min_score": {"type": "number", "default": 0.0}}, "required": ["company_profile_id"]}},
]

TOOL_HANDLERS = {
    "search_tenders": _tool_search_tenders, "latest_tenders": _tool_latest_tenders,
    "tender_details": _tool_tender_details, "tender_documents": _tool_tender_documents,
    "tender_changes": _tool_tender_changes, "source_health": _tool_source_health,
    "collection_status": _tool_collection_status, "list_watchlists": _tool_list_watchlists,
    "watchlist_notices": _tool_watchlist_notices, "list_company_profiles": _tool_list_company_profiles,
    "match_tenders_to_company": _tool_match_tenders_to_company,
}


def mcp_response(request):
    method=request.get("method"); params=request.get("params",{})
    if method == "initialize": return {"protocolVersion":params.get("protocolVersion","2025-03-26"),"capabilities":{"tools":{}},"serverInfo":{"name":"sudurpashchim-tender-monitor","version":"0.2.0"}}
    if method == "tools/list": return {"tools": TOOLS}
    if method == "tools/call":
        name=params.get("name"); args=params.get("arguments",{}) or {}
        handler=TOOL_HANDLERS.get(name)
        if handler is None:
            result=_error("unknown_tool", f"No such tool: {name}")
        else:
            try:
                result=handler(args)
            except Exception as exc:
                # A bug in one tool's handler must not crash the whole stdio loop -- same
                # per-call isolation principle collect_one already applies per-source.
                result=_error("internal_error", str(exc))
        return {"content":[{"type":"text","text":json.dumps(result,ensure_ascii=False)}]}
    return None


def mcp():
    for line in sys.stdin:
        try:
            req=json.loads(line); result=mcp_response(req)
            if "id" in req and result is not None: print(json.dumps({"jsonrpc":"2.0","id":req["id"],"result":result},ensure_ascii=False),flush=True)
        except Exception as exc:
            print(json.dumps({"jsonrpc":"2.0","id":None,"error":{"code":-32603,"message":str(exc)}}),flush=True)
