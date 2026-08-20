"""argv dispatch for the app.py entrypoint, and small CLI-only helpers."""
import json
import os
import sys

from . import alerts, collector, discovery, mcp_server, queries, scheduler


def alert():
    notices=queries.list_notices("",10)
    message="No newly collected tender notices." if not notices else "Latest tender notices:\n" + "\n".join(f"• {n['authority']}: {n['title']}\n{n['url']}" for n in notices[:3])
    if not all(os.getenv(k) for k in ("WHATSAPP_ACCESS_TOKEN","WHATSAPP_PHONE_NUMBER_ID","WHATSAPP_RECIPIENT")):
        print("TEST ALERT (not sent)\n"+message); return
    raise SystemExit("WhatsApp credentials are present. Configure an approved template payload before enabling production sends.")


def test_whatsapp():
    status, detail = alerts.send_whatsapp_alert({
        "authority":"Notice Feed test",
        "title":"WhatsApp alert connection test",
        "url":"http://127.0.0.1:8787/",
    })
    print(json.dumps({"status":status,"detail":detail},ensure_ascii=False,indent=2))


def main():
    command=sys.argv[1] if len(sys.argv)>1 else "help"
    if command=="collect": print(json.dumps(collector.collect_all(sys.argv[2] if len(sys.argv)>2 else None),ensure_ascii=False,indent=2))
    elif command=="serve": scheduler.serve()
    elif command=="mcp": mcp_server.mcp()
    elif command=="alert": alert()
    elif command=="test-whatsapp": test_whatsapp()
    elif command=="bootstrap-sudurpashchim": print(json.dumps(discovery.bootstrap_sudurpashchim(),ensure_ascii=False,indent=2))
    elif command=="bootstrap-karnali": print(json.dumps(discovery.bootstrap_karnali(),ensure_ascii=False,indent=2))
    elif command=="bootstrap-lumbini": print(json.dumps(discovery.bootstrap_lumbini(),ensure_ascii=False,indent=2))
    elif command=="sync-all-local-levels": print(json.dumps(discovery.sync_all_local_levels(),ensure_ascii=False,indent=2))
    else: print("Usage: app.py [sync-all-local-levels|bootstrap-sudurpashchim|bootstrap-karnali|bootstrap-lumbini|collect [source-id]|serve|mcp|alert|test-whatsapp]")
