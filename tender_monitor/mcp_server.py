"""The MCP stdio server: a minimal JSON-RPC loop over stdin/stdout."""
import json
import sys

from . import queries


def mcp_response(request):
    method=request.get("method"); params=request.get("params",{})
    if method == "initialize": return {"protocolVersion":params.get("protocolVersion","2025-03-26"),"capabilities":{"tools":{}},"serverInfo":{"name":"sudurpashchim-tender-monitor","version":"0.1.0"}}
    if method == "tools/list": return {"tools":[
        {"name":"search_tenders","description":"Search collected local-government tender notices.","inputSchema":{"type":"object","properties":{"query":{"type":"string"},"limit":{"type":"integer","default":20}}}},
        {"name":"latest_tenders","description":"Return the latest collected tender notices.","inputSchema":{"type":"object","properties":{"limit":{"type":"integer","default":20}}}},
        {"name":"tender_details","description":"Retrieve a tender notice by its identifier.","inputSchema":{"type":"object","properties":{"id":{"type":"string"}},"required":["id"]}}
    ]}
    if method == "tools/call":
        name=params.get("name"); args=params.get("arguments",{})
        result = queries.list_notices(args.get("query", ""), args.get("limit",20)) if name=="search_tenders" else queries.list_notices("",args.get("limit",20)) if name=="latest_tenders" else queries.details(args.get("id")) if name=="tender_details" else {"error":"unknown tool"}
        return {"content":[{"type":"text","text":json.dumps(result,ensure_ascii=False)}]}
    return None


def mcp():
    for line in sys.stdin:
        try:
            req=json.loads(line); result=mcp_response(req)
            if "id" in req and result is not None: print(json.dumps({"jsonrpc":"2.0","id":req["id"],"result":result},ensure_ascii=False),flush=True)
        except Exception as exc:
            print(json.dumps({"jsonrpc":"2.0","id":None,"error":{"code":-32603,"message":str(exc)}}),flush=True)
