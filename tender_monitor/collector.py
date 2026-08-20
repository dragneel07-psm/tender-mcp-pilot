"""The collection pipeline: collect_one fetches and stores one source's notices; collect_all
orchestrates a full sweep across every source (skip/cooldown-aware, concurrent)."""
import hashlib
import os
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import alerts, health, net, parsing, storage


def collect_one(source):
    """Fetch and parse a single source, then commit results. Never raises: a single source's
    failure (network, parsing, or database) must not be able to take down the whole scheduler."""
    now=datetime.now(timezone.utc).isoformat()
    try:
        body=net.fetch(source["notice_url"])
        parser=parsing.LinkTextParser(); parser.feed(body)
        candidates=[]
        for href, label in parser.links:
            title=parsing.clean(label)
            url=urllib.parse.urljoin(source["notice_url"], href)
            if len(title) < 8 or not parsing.relevant(title + " " + url, source): continue
            published=parsing.published_date(body, href, title)
            digest=hashlib.sha256((source["id"]+url+title).encode()).hexdigest()
            candidates.append({"id":digest, "authority":source["name"], "title":title, "url":url, "published":published})
        # Notices with no date on the listing page get a supplementary per-notice-page lookup.
        # This used to run one notice at a time with the full network-fetch retry budget each, so a
        # source with many such notices (or several slow/dead notice pages) could stall an entire
        # collection cycle by tens of minutes on its own. Cap how many are attempted per cycle and
        # run them a few at a time; any left over simply keep their "collected" date this cycle
        # instead of a "published" date -- a cosmetic fallback, not a correctness issue.
        if os.getenv("NOTICE_PAGE_DATE_LOOKUPS", "1") == "1":
            needs_lookup=[c for c in candidates if not c["published"]][:max(0, int(os.getenv("NOTICE_PAGE_LOOKUP_LIMIT", "15")))]
            if needs_lookup:
                lookup_workers=min(len(needs_lookup), max(1, int(os.getenv("NOTICE_PAGE_LOOKUP_WORKERS", "5"))))
                with ThreadPoolExecutor(max_workers=lookup_workers) as pool:
                    for c, published in zip(needs_lookup, pool.map(lambda c: net.linked_notice_date(c["url"]), needs_lookup)):
                        c["published"]=published
        # All the (fast, no-network) database writes for this source happen in one locked section,
        # so 40+ concurrent collector threads serialize on writes without racing SQLite's own locking.
        added=0; new_notices=[]
        with storage.DB_WRITE_LOCK:
            db=storage.conn()
            try:
                for c in candidates:
                    db.execute("""insert or ignore into notices
                        (id,source_id,authority,title,url,discovered_at,published_at,relevant,raw_text)
                        values (?,?,?,?,?,?,?,?,?)""", (c["id"],source["id"],source["name"],c["title"],c["url"],now,c["published"],1,c["title"]))
                    inserted = db.execute("select changes()").fetchone()[0]
                    if not inserted and c["published"]:
                        db.execute("update notices set published_at=coalesce(published_at,?) where id=?", (c["published"],c["id"]))
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
