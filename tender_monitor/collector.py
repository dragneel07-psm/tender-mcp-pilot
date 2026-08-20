"""The collection pipeline: collect_one fetches and stores one source's notices; collect_all
orchestrates a full sweep across every source (skip/cooldown-aware, concurrent)."""
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import adapters, alerts, documents, health, net, parsing, storage


def _classify_change(snippet, current_published_at):
    """Milestone 6: best-effort classification of a detected listing-entry change (collect_one
    calls this only once a re-scrape's content_hash has actually diverged from what's stored).
    Reuses the same keyword/date primitives already trusted elsewhere in the codebase rather than
    inventing new ones: parsing.classify_notice_type's cancellation/corrigendum keyword scan (same
    imprecision it already carries for title text -- a ~1.6KB snippet window can contain unrelated
    text, so this is a signal, not a certainty) and documents.extract_submission_deadline's
    keyword-gated date extraction (a date is only trusted as a deadline near an explicit
    deadline-indicating keyword -- never fabricate).

    Checked in priority order, most consequential/actionable first: a cancellation notice matters
    more to a reader than a coincidentally-also-present date. Returns
    (change_type, new_value, new_status) -- new_value/new_status are None when not applicable.
    Falls back to "listing_changed" (recorded for the audit trail, never alerted -- see
    collect_one) rather than guessing at one of the three named types without real evidence."""
    signal = parsing.classify_notice_type(snippet)
    if signal == "cancellation":
        return "TENDER_CANCELLED", None, "cancelled"
    deadline = documents.extract_submission_deadline(snippet)
    # Requires a real prior date to differ *from* -- a null-to-value transition is a first
    # capture, not a change (same "never fabricate a change that isn't one" reasoning as the
    # content_hash null-check above).
    if deadline and current_published_at and deadline != current_published_at:
        return "DEADLINE_CHANGED", deadline, None
    if signal == "corrigendum":
        return "CORRIGENDUM", None, None
    return "listing_changed", None, None


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
        added=0; new_notices=[]; changed_notices=[]
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
                        # notice still active". published_at only fills in if a prior cycle never
                        # found one; it doesn't overwrite an already-known value here (that's what
                        # DEADLINE_CHANGED below is for -- an overwrite backed by real evidence).
                        db.execute("update notices set last_seen=? where id=?", (now,c["id"]))
                        if c["published"]:
                            db.execute("update notices set published_at=coalesce(published_at,?) where id=?", (c["published"],c["id"]))
                        stored=db.execute("select content_hash, published_at from notices where id=?", (c["id"],)).fetchone()
                        # Milestone 6: a re-scrape's listing entry actually changed since we last
                        # saw it -- only meaningful once there's a real prior hash to compare
                        # against (a null->value transition is a first capture, not a change).
                        if stored["content_hash"] and c["content_hash"] and c["content_hash"] != stored["content_hash"] and c["context_snippet"]:
                            change_type, new_value, new_status = _classify_change(c["context_snippet"], stored["published_at"])
                            change_id=hashlib.sha256((c["id"]+change_type+now).encode()).hexdigest()
                            db.execute("""insert or ignore into notice_changes
                                (id,notice_id,change_type,previous_value,new_value,detail,detected_at) values (?,?,?,?,?,?,?)""",
                                (change_id, c["id"], change_type, stored["published_at"] if change_type=="DEADLINE_CHANGED" else None,
                                 new_value, f"Detected from a changed listing entry on {source['name']}.", now))
                            if new_status: db.execute("update notices set status=? where id=?", (new_status,c["id"]))
                            if change_type=="DEADLINE_CHANGED": db.execute("update notices set published_at=? where id=?", (new_value,c["id"]))
                            db.execute("update notices set content_hash=? where id=?", (c["content_hash"],c["id"]))
                            # The unclassified fallback is recorded for the audit trail but not
                            # alerted -- reusing the fixed WhatsApp template for every ambiguous
                            # text tweak on a listing page would be noise, not signal.
                            if change_type != "listing_changed": changed_notices.append((c, change_type))
                        elif c["content_hash"]:
                            db.execute("update notices set content_hash=coalesce(content_hash,?) where id=?", (c["content_hash"],c["id"]))
                    else:
                        for category, confidence in c["categories"]:
                            db.execute("insert or ignore into notice_categories values (?,?,?)", (c["id"],category,confidence))
                    added += inserted
                    if inserted: new_notices.append(c)
                db.execute("insert into runs values (?,?,?,?)", (source["id"],now,"ok",f"{added} new notices"))
                health.record_health(db, source["id"], True, f"{added} new notices", now)
                db.commit()
            finally: db.close()
        for notice in new_notices:
            status, detail = alerts.send_alert(notice, reason="new_notice")  # network call; deliberately outside the write lock
            with storage.DB_WRITE_LOCK:
                db=storage.conn()
                try:
                    db.execute("insert into deliveries (notice_id,delivered_at,status,detail,reason) values (?,?,?,?,?)",
                               (notice["id"], now, status, detail, "new_notice")); db.commit()
                finally: db.close()
        for notice, change_type in changed_notices:
            # Milestone 7: alerts.send_alert now owns how a `reason` maps onto WhatsApp's fixed
            # 3-parameter template (the title prefix) -- collector.py no longer needs to know that.
            status, detail = alerts.send_alert(notice, reason=change_type)
            with storage.DB_WRITE_LOCK:
                db=storage.conn()
                try:
                    db.execute("insert into deliveries (notice_id,delivered_at,status,detail,reason) values (?,?,?,?,?)",
                               (notice["id"], now, status, detail, change_type)); db.commit()
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
