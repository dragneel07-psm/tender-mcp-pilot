"""The HTTP API and dashboard-serving handler."""
import base64
import json
import os
import re
import secrets
import threading
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler

from . import collector, queries, status, storage
from .config import ROOT


class Api(BaseHTTPRequestHandler):
    def security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
    def require_auth(self):
        if self.path.split("?",1)[0] == "/health": return True
        username=os.getenv("APP_USERNAME", ""); password=os.getenv("APP_PASSWORD", "")
        required=os.getenv("REQUIRE_AUTH", "1" if os.getenv("HOST") == "0.0.0.0" else "0") == "1"
        if not username and not password and not required: return True
        if not username or not password:
            self.send_response(503); self.security_headers(); self.end_headers(); return False
        header=self.headers.get("Authorization", "")
        expected=base64.b64encode(f"{username}:{password}".encode()).decode()
        if header.startswith("Basic ") and secrets.compare_digest(header[6:], expected): return True
        self.send_response(401); self.security_headers(); self.send_header("WWW-Authenticate", 'Basic realm="Notice Feed"'); self.end_headers()
        return False
    def json_body(self):
        length=int(self.headers.get("Content-Length", "0"))
        if length < 1 or length > 65536: raise ValueError("Request body must be between 1 and 65536 bytes.")
        return json.loads(self.rfile.read(length))
    def respond(self, payload, status_code=200):
        data=json.dumps(payload, ensure_ascii=False).encode(); self.send_response(status_code); self.security_headers(); self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Content-Length",str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        if not self.require_auth(): return
        path, _, qs=self.path.partition("?"); params=urllib.parse.parse_qs(qs)
        if path == "/" or re.fullmatch(r"/source/[a-z0-9-]+", path):
            data=(ROOT / "dashboard.html").read_bytes()
            self.send_response(200); self.security_headers(); self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'"); self.send_header("Content-Type","text/html; charset=utf-8"); self.send_header("Content-Length",str(len(data))); self.end_headers(); self.wfile.write(data); return
        if path == "/health": return self.respond({"status":"ok"})
        if path == "/sources": return self.respond(queries.source_summary())
        if path == "/watchlists": return self.respond(storage.watchlists())
        if path == "/alerts/status": return self.respond(queries.alert_summary())
        if path == "/collection/status": return self.respond(status.last_cycle)
        if path == "/notices":
            has_documents=params.get("has_documents",[""])[0]
            return self.respond(queries.list_notices(
                params.get("query",[""])[0], int(params.get("limit",[50])[0]), params.get("source",[""])[0],
                int(params.get("offset",[0])[0]), params.get("province",[""])[0], params.get("notice_type",[""])[0],
                params.get("status",[""])[0], params.get("category",[""])[0],
                params.get("discovered_after",[""])[0], params.get("discovered_before",[""])[0],
                {"true":True,"false":False}.get(has_documents.lower())))
        documents_match=re.fullmatch(r"/notices/([a-f0-9]{64})/documents", path)
        if documents_match:
            record=queries.details(documents_match.group(1))
            if not record: return self.respond({"error":"not found"},404)
            return self.respond(queries.notice_documents(documents_match.group(1)))
        if path.startswith("/notices/"):
            record=queries.details(path.rsplit("/",1)[1]); return self.respond(record or {"error":"not found"}, 200 if record else 404)
        self.respond({"error":"not found"},404)
    def do_POST(self):
        if not self.require_auth(): return
        if self.path == "/watchlists":
            try:
                payload=self.json_body()
                with storage.REGISTRY_WRITE_LOCK:
                    items=storage.watchlists(); item=storage.validate_watchlist(payload)
                    if any(existing["id"] == item["id"] for existing in items): return self.respond({"error":"A watchlist with this name already exists."},409)
                    items.append(item); storage.save_watchlists(items)
                return self.respond(item,201)
            except (ValueError, json.JSONDecodeError) as exc: return self.respond({"error":str(exc)},400)
        if self.path == "/sources":
            try:
                payload=self.json_body()
                with storage.REGISTRY_WRITE_LOCK:
                    items=storage.sources(); item=storage.validate_source(payload)
                    if any(existing["id"] == item["id"] for existing in items): return self.respond({"error":"A source with this name already exists."},409)
                    items.append(item); storage.save_sources(items)
                threading.Thread(target=collector.collect_one, args=(item,), daemon=True).start()
                return self.respond({**item,"collection":"started"},201)
            except (ValueError, json.JSONDecodeError) as exc: return self.respond({"error":str(exc)},400)
        notice_match=re.fullmatch(r"/notices/([a-f0-9]{64})/mark-seen", self.path)
        if notice_match:
            now=datetime.now(timezone.utc).isoformat()
            with storage.DB_WRITE_LOCK:
                db=storage.conn()
                db.execute("update notices set seen_at=? where id=? and seen_at is null", (now,notice_match.group(1))); count=db.execute("select changes()").fetchone()[0]; db.commit(); db.close()
            return self.respond({"marked_seen":count})
        match=re.fullmatch(r"/sources/([a-z0-9-]+)/mark-seen", self.path)
        if not match: return self.respond({"error":"not found"},404)
        now=datetime.now(timezone.utc).isoformat()
        with storage.DB_WRITE_LOCK:
            db=storage.conn()
            db.execute("update notices set seen_at=? where source_id=? and seen_at is null", (now,match.group(1))); count=db.execute("select changes()").fetchone()[0]; db.commit(); db.close()
        self.respond({"marked_seen":count})
    def do_PATCH(self):
        if not self.require_auth(): return
        watchlist_match=re.fullmatch(r"/watchlists/(wl-[a-f0-9]+)", self.path)
        if watchlist_match:
            try:
                payload=self.json_body()
                with storage.REGISTRY_WRITE_LOCK:
                    items=storage.watchlists(); index=next((i for i, item in enumerate(items) if item["id"]==watchlist_match.group(1)), None)
                    if index is None: return self.respond({"error":"not found"},404)
                    items[index]=storage.validate_watchlist(payload,items[index]); storage.save_watchlists(items)
                return self.respond(items[index])
            except (ValueError, json.JSONDecodeError) as exc: return self.respond({"error":str(exc)},400)
        match=re.fullmatch(r"/sources/([a-z0-9-]+)", self.path)
        if not match: return self.respond({"error":"not found"},404)
        try:
            payload=self.json_body()
            with storage.REGISTRY_WRITE_LOCK:
                items=storage.sources(); index=next((i for i, item in enumerate(items) if item["id"]==match.group(1)), None)
                if index is None: return self.respond({"error":"not found"},404)
                items[index]=storage.validate_source(payload,items[index]); storage.save_sources(items)
            self.respond(items[index])
        except (ValueError, json.JSONDecodeError) as exc: self.respond({"error":str(exc)},400)
    def do_DELETE(self):
        if not self.require_auth(): return
        watchlist_match=re.fullmatch(r"/watchlists/(wl-[a-f0-9]+)", self.path)
        if watchlist_match:
            with storage.REGISTRY_WRITE_LOCK:
                items=storage.watchlists(); remaining=[item for item in items if item["id"]!=watchlist_match.group(1)]
                if len(remaining)==len(items): return self.respond({"error":"not found"},404)
                storage.save_watchlists(remaining)
            return self.respond({"removed":watchlist_match.group(1)})
        match=re.fullmatch(r"/sources/([a-z0-9-]+)", self.path)
        if not match: return self.respond({"error":"not found"},404)
        with storage.REGISTRY_WRITE_LOCK:
            items=storage.sources(); remaining=[item for item in items if item["id"]!=match.group(1)]
            if len(remaining)==len(items): return self.respond({"error":"not found"},404)
            storage.save_sources(remaining)
            source_id=match.group(1); lists=storage.watchlists()
            for item in lists: item["source_ids"]=[value for value in item.get("source_ids",[]) if value!=source_id]
            storage.save_watchlists(lists)
        self.respond({"removed":source_id})
    def log_message(self, *_): pass
