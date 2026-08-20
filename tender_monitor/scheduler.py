"""The background collection loop and the HTTP server that serves alongside it."""
import json
import os
import threading
import time
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer

from . import collector, reminders, status
from .api import Api


def serve():
    interval_minutes = int(os.getenv("AUTO_COLLECT_INTERVAL_MINUTES", "60"))
    def scheduled_collection():
        # This loop must never die: it's the only thing driving continuous collection. collect_one
        # already isolates per-source failures, but this try/except is a last-resort safety net so
        # nothing (collect_all's own bookkeeping, an unforeseen bug) can silently kill the thread and
        # leave the app looking healthy (server still answers /health) while collection has stopped.
        while True:
            started_at=datetime.now(timezone.utc).isoformat()
            status.last_cycle={"phase":"running","started_at":started_at,"finished_at":None,"duration_seconds":None,"counts":{}}
            try:
                started=time.monotonic()
                print("Automatic collection started", flush=True)
                results=collector.collect_all()
                counts={}
                for result in results:
                    counts[result["status"]]=counts.get(result["status"],0)+1
                    if result["status"]=="error": print(json.dumps(result, ensure_ascii=False), flush=True)
                elapsed=round(time.monotonic()-started,1)
                # Isolated from collect_all's own try/except: a bug in the reminder pass must not
                # mark an otherwise-successful collection cycle as "crashed" (same isolation
                # principle collect_one already applies per-source).
                try:
                    reminders_sent=reminders.send_due_reminders()
                except Exception as exc:
                    reminders_sent=0
                    print(f"Deadline reminder pass failed (collection itself succeeded): {exc}", flush=True)
                status.last_cycle={"phase":"idle","started_at":started_at,"finished_at":datetime.now(timezone.utc).isoformat(),"duration_seconds":elapsed,"counts":counts,"reminders_sent":reminders_sent}
                print(f"Automatic collection finished in {elapsed}s: {json.dumps(counts, ensure_ascii=False)} ({reminders_sent} deadline reminder(s) sent)", flush=True)
            except Exception as exc:
                status.last_cycle={"phase":"crashed","started_at":started_at,"finished_at":datetime.now(timezone.utc).isoformat(),"duration_seconds":None,"counts":{},"error":str(exc)}
                print(f"Automatic collection cycle crashed, will retry next cycle: {exc}", flush=True)
            time.sleep(max(interval_minutes, 5) * 60)
    threading.Thread(target=scheduled_collection, daemon=True).start()
    host=os.getenv("HOST", "127.0.0.1"); port=int(os.getenv("PORT","8787"))
    server=ThreadingHTTPServer((host, port), Api)
    print(f"Tender Monitor dashboard listening on http://{host}:{port} (automatic collection every {interval_minutes} minutes)", flush=True); server.serve_forever()
