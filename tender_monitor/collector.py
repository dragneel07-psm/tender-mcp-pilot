"""The collection pipeline: collect_one fetches and stores one source's notices; collect_all
orchestrates a full sweep across every source (skip/cooldown-aware, concurrent)."""
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import adapters, alerts, documents, health, net, storage


def _discover_notice_documents(notice):
    """Find and download this notice's PDF document(s). Best-effort: never raises -- a document
    failure must not affect the notice, which is already inserted by the time this runs."""
    try:
        url = notice["url"]
        if url.lower().split("?", 1)[0].endswith(".pdf"):
            links = [(url, notice["title"])]
        else:
            timeout = int(os.getenv("DOCUMENT_DOWNLOAD_TIMEOUT_SECONDS", "20"))
            page = net.fetch(url, timeout=timeout, retries=1)
            links = documents.discover_pdf_links(page, url)
        results = []
        for link_url, link_text in links[:3]:  # cap documents per notice, independent of the per-source cap below
            extracted = documents.download_and_extract(link_url)
            extracted["document_type"] = documents.classify_document_type(link_text)
            results.append(extracted)
        return results
    except Exception:
        return []


def collect_one(source):
    """Fetch and parse a single source, then commit results. Never raises: a single source's
    failure (network, parsing, or database) must not be able to take down the whole scheduler."""
    now=datetime.now(timezone.utc).isoformat()
    try:
        candidates=adapters.DEFAULT_ADAPTER.discover_notices(source)
        # All the (fast, no-network) database writes for this source happen in one locked section,
        # so 40+ concurrent collector threads serialize on writes without racing SQLite's own locking.
        added=0; new_notices=[]
        with storage.DB_WRITE_LOCK:
            db=storage.conn()
            try:
                for c in candidates:
                    db.execute("""insert or ignore into notices
                        (id,source_id,authority,title,url,discovered_at,published_at,relevant,raw_text,
                         organization,province,notice_type,status,first_seen,last_seen,content_hash,confidence_score)
                        values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (c["id"],source["id"],source["name"],c["title"],c["url"],now,c["published"],1,c["title"],
                         c["organization"],c["province"],c["notice_type"],c["status"],now,now,c["content_hash"],c["confidence_score"]))
                    inserted = db.execute("select changes()").fetchone()[0]
                    if not inserted:
                        # Already on file from an earlier cycle: it's still listed on the source's
                        # page, so record that -- this is the first real signal toward "is this
                        # notice still active" (Milestone 6 builds real change detection on top of
                        # it). published_at/content_hash only fill in if a prior cycle never found
                        # one; they don't overwrite an already-known value.
                        db.execute("update notices set last_seen=? where id=?", (now,c["id"]))
                        if c["published"]:
                            db.execute("update notices set published_at=coalesce(published_at,?) where id=?", (c["published"],c["id"]))
                        if c["content_hash"]:
                            db.execute("update notices set content_hash=coalesce(content_hash,?) where id=?", (c["content_hash"],c["id"]))
                    added += inserted
                    if inserted: new_notices.append(c)
                db.execute("insert into runs values (?,?,?,?)", (source["id"],now,"ok",f"{added} new notices"))
                health.record_health(db, source["id"], True, f"{added} new notices", now)
                db.commit()
            finally: db.close()
        for notice in new_notices:
            status, detail = alerts.send_whatsapp_alert(notice)  # network call; deliberately outside the write lock
            with storage.DB_WRITE_LOCK:
                db=storage.conn()
                try:
                    db.execute("insert into deliveries values (?,?,?,?)", (notice["id"], now, status, detail)); db.commit()
                finally: db.close()
        # Milestone 3, off by default (DOCUMENT_PROCESSING_ENABLED) and bounded even when on: only
        # genuinely new notices this cycle get document discovery, and only up to
        # DOCUMENT_DOWNLOAD_LIMIT of those, so a source with a burst of new notices (e.g. the first
        # time it's added) can't blow up this cycle's duration. A notice only goes through this
        # once in its life -- it's never "new" again once inserted.
        if new_notices and os.getenv("DOCUMENT_PROCESSING_ENABLED", "0") == "1":
            doc_limit=max(0, int(os.getenv("DOCUMENT_DOWNLOAD_LIMIT", "3")))
            to_process=new_notices[:doc_limit]
            if to_process:
                doc_workers=min(len(to_process), max(1, int(os.getenv("DOCUMENT_DOWNLOAD_WORKERS", "3"))))
                with ThreadPoolExecutor(max_workers=doc_workers) as pool:
                    doc_results=list(pool.map(_discover_notice_documents, to_process))
                with storage.DB_WRITE_LOCK:
                    db=storage.conn()
                    try:
                        for notice, docs in zip(to_process, doc_results):
                            for doc in docs:
                                doc_id=hashlib.sha256((notice["id"]+doc["url"]).encode()).hexdigest()
                                db.execute("""insert or ignore into documents
                                    (id,notice_id,url,sha256,size_bytes,content_type,document_type,extracted_text,extraction_status,discovered_at)
                                    values (?,?,?,?,?,?,?,?,?,?)""",
                                    (doc_id,notice["id"],doc["url"],doc["sha256"],doc["size_bytes"],doc["content_type"],
                                     doc["document_type"],doc["extracted_text"],doc["extraction_status"],now))
                                if doc["extracted_text"]:
                                    deadline=documents.extract_submission_deadline(doc["extracted_text"])
                                    if deadline:
                                        db.execute("update notices set submission_deadline=coalesce(submission_deadline,?) where id=?", (deadline,notice["id"]))
                        db.commit()
                    finally: db.close()
        return {"source":source["name"],"status":"ok","new":added}
    except Exception as exc:
        try:
            with storage.DB_WRITE_LOCK:
                db=storage.conn()
                try:
                    db.execute("insert into runs values (?,?,?,?)", (source["id"],now,"error",str(exc)))
                    health.record_health(db, source["id"], False, str(exc), now)
                    db.commit()
                finally: db.close()
        except Exception:
            pass  # even health/run bookkeeping must not be able to crash the scheduler
        return {"source":source["name"],"status":"error","detail":str(exc)}


def collect_all(source_id=None):
    selected = [s for s in storage.sources() if not source_id or s["id"] == source_id]
    if source_id and not selected:
        raise ValueError(f"Unknown source: {source_id}")
    if not selected: return []
    to_run, skipped = selected, []
    if not source_id:
        # Scheduled sweeps skip sources with a long failure streak until their cooldown elapses,
        # so chronically dead sites stop eating a full timeout*retries budget every cycle.
        # A manually requested single-source collection (source_id set) always runs regardless.
        threshold, cooldown_minutes = health.health_skip_settings()
        db=storage.conn()
        try:
            to_run, skipped = [], []
            for s in selected:
                if health.should_skip(db, s["id"], threshold, cooldown_minutes):
                    skipped.append({"source":s["name"],"status":"skipped","detail":f"{threshold}+ consecutive failures; retrying after cooldown"})
                else:
                    to_run.append(s)
        finally: db.close()
    if not to_run: return skipped
    workers=min(len(to_run), max(1, int(os.getenv("COLLECTOR_WORKERS", "8"))))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(collect_one, to_run)) + skipped
