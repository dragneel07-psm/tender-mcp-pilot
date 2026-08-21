"""SQLite schema/connection, and the JSON-file-backed source/watchlist registries."""
import hashlib
import ipaddress
import json
import os
import sqlite3
import threading
import urllib.parse

from .config import COMPANY_PROFILES, DB, SOURCES, WATCHLISTS
from .parsing import classify_categories, classify_notice_type, clean, status_for_notice_type

# SQLite allows only one writer at a time; under WAL, concurrent writers past that point block on
# the busy handler and can still hit "database is locked" once enough threads are writing at once
# (this happened in production once COLLECTOR_WORKERS was raised). Funnel every write through this
# lock instead of relying solely on busy_timeout, so writers queue in-process rather than erroring.
DB_WRITE_LOCK = threading.Lock()

# A separate lock from DB_WRITE_LOCK, deliberately: collector.py calls conn() from inside a
# `with DB_WRITE_LOCK:` block, and threading.Lock is not reentrant -- a thread already holding
# DB_WRITE_LOCK would deadlock against itself if conn()'s own schema check tried to acquire the
# same lock. Guards the one-time (per fresh database) schema migration below.
SCHEMA_MIGRATION_LOCK = threading.Lock()

# Every read-modify-write cycle against sources.json/watchlists.json/company_profiles.json (add/
# edit/delete a source, watchlist, or company profile, or the bootstrap-* import) must hold this
# for its whole read...mutate...save span, not just the save -- save_json()'s temp-file-then-rename
# is atomic per write, but two concurrent mutations can still race on the read they both start from
# and the second one's write clobbers the first's change. All three files share one lock because
# deleting a source also mutates watchlists (removing it from every watchlist's source_ids) in the
# same logical operation; company_profiles.json doesn't cross-reference the others today but there
# is no benefit to a fourth lock for one more small JSON file.
REGISTRY_WRITE_LOCK = threading.Lock()

# Additive Milestone 2 columns. organization/province/notice_type/status/first_seen/last_seen are
# backfilled once for rows that predate this migration (see _backfill_notices_schema). district and
# content_hash are NOT backfilled -- there is no honest way to derive them for already-collected
# rows (never fabricate data), so they stay null until a notice is re-seen (content_hash) or a real
# district data source exists (Milestone 3+).
NOTICES_MIGRATION_COLUMNS = (
    ("seen_at", "text"), ("published_at", "text"),
    ("organization", "text"), ("province", "text"), ("district", "text"),
    ("notice_type", "text"), ("status", "text"),
    ("first_seen", "text"), ("last_seen", "text"),
    ("content_hash", "text"), ("confidence_score", "real"),
    # Milestone 3: not backfilled -- deriving it for old rows means re-fetching and re-parsing
    # every document they ever linked to, out of scope for a schema migration step.
    ("submission_deadline", "text"),
    # Milestone 10: AI-derived fields, never backfilled -- same reasoning as submission_deadline,
    # plus a real per-call cost this time (an LLM API call per notice), so a schema migration must
    # never trigger one implicitly. These columns have no other writer anywhere in this codebase
    # (see ai.py's module docstring), so their mere presence is the provenance tag: non-null here
    # unambiguously means an AI model produced it, never a rule-based or source-derived value.
    ("estimated_amount", "text"), ("bid_security_amount", "text"), ("eligibility_summary", "text"),
    ("ai_provider", "text"), ("ai_extraction_status", "text"), ("ai_extracted_at", "text"),
)
BACKFILLABLE_COLUMNS = ("organization", "province", "notice_type", "status", "first_seen", "last_seen")

# Whether the one-time notice_categories backfill has been checked yet in this process. Checked at
# most once per process lifetime (not per conn() call) since count(*) over a 6,000+ row table on
# every single connection would add real, cumulative overhead -- conn() is called extremely often.
_categories_backfill_checked = False


def conn():
    db = sqlite3.connect(DB, timeout=20)
    db.row_factory = sqlite3.Row
    db.execute("pragma journal_mode=WAL")
    db.execute("pragma busy_timeout=20000")
    db.execute("""create table if not exists notices (
        id text primary key, source_id text not null, authority text not null,
        title text not null, url text not null, discovered_at text not null,
        relevant integer not null default 0, raw_text text not null
    )""")
    # Milestone 4: a notice can carry several categories, each with its own confidence -- a
    # separate table, not a column, since "one or more categories per notice" doesn't fit a single
    # SQL column without either denormalizing or fabricating a canonical "the" category.
    db.execute("""create table if not exists notice_categories (
        notice_id text not null, category text not null, confidence_score real not null,
        primary key (notice_id, category)
    )""")
    _ensure_notices_schema(db)
    db.execute("create table if not exists runs (source_id text, ran_at text, status text, detail text)")
    db.execute("""create table if not exists deliveries (
        notice_id text, delivered_at text, status text, detail text
    )""")
    _ensure_deliveries_schema(db)
    db.execute("""create table if not exists source_health (
        source_id text primary key, last_status text, last_detail text,
        last_run_at text, last_success_at text, consecutive_failures integer not null default 0
    )""")
    # Milestone 3: discovered documents (PDF only today). No raw bytes stored -- text + metadata
    # only, capped in length (see documents.py) -- to avoid unbounded volume growth.
    db.execute("""create table if not exists documents (
        id text primary key, notice_id text not null, url text not null, sha256 text,
        size_bytes integer, content_type text, document_type text, extracted_text text,
        extraction_status text not null, discovered_at text not null
    )""")
    # Milestone 4: notices.source_id/discovered_at were unindexed (audit §12) -- list_notices()'s
    # filters and sort would otherwise degrade to full scans as the table grows past its current
    # ~7,000 rows. "if not exists" makes this a cheap no-op check on every call once created.
    db.execute("create index if not exists idx_notices_source_id on notices(source_id)")
    db.execute("create index if not exists idx_notices_discovered_at on notices(discovered_at)")
    db.execute("create index if not exists idx_notice_categories_category on notice_categories(category)")
    # Milestone 6: version history. One row per detected change to an already-stored notice (see
    # collector.py's diff step) -- an append-only log rather than full-row snapshots, since in
    # practice only a handful of fields can meaningfully change post-insert (content_hash,
    # published_at, status). change_type is one of TENDER_CANCELLED/DEADLINE_CHANGED/CORRIGENDUM
    # (the three the roadmap names, each of which also fires an alert) or the unclassified
    # fallback "listing_changed" (recorded for the audit trail, never alerted -- see
    # collector._classify_change).
    db.execute("""create table if not exists notice_changes (
        id text primary key, notice_id text not null, change_type text not null,
        previous_value text, new_value text, detail text, detected_at text not null
    )""")
    db.execute("create index if not exists idx_notice_changes_notice_id on notice_changes(notice_id)")
    return db


def _ensure_deliveries_schema(db):
    """Milestone 6 additive column: which change (if any) triggered this alert -- 'new_notice' for
    the original alert path, or a notice_changes.change_type for a change-triggered one. Null for
    every row that predates this column (never fabricate a reason for history that has none)."""
    columns = {row[1] for row in db.execute("pragma table_info(deliveries)")}
    if "reason" in columns: return
    with SCHEMA_MIGRATION_LOCK:
        # Re-check under the lock, same double-checked-locking reason as _ensure_notices_schema.
        columns = {row[1] for row in db.execute("pragma table_info(deliveries)")}
        if "reason" not in columns:
            db.execute("alter table deliveries add column reason text")
        db.commit()


def _ensure_notices_schema(db):
    global _categories_backfill_checked
    columns = {row[1] for row in db.execute("pragma table_info(notices)")}
    missing = [name for name, _ in NOTICES_MIGRATION_COLUMNS if name not in columns]
    if not missing and _categories_backfill_checked: return
    with SCHEMA_MIGRATION_LOCK:
        # Re-check under the lock: with up to dozens of collector threads able to call conn()
        # concurrently right after a fresh deploy, another thread may have already migrated the
        # schema between our unlocked check above and acquiring this lock.
        columns = {row[1] for row in db.execute("pragma table_info(notices)")}
        missing = [name for name, ddl in NOTICES_MIGRATION_COLUMNS if name not in columns]
        if missing:
            for name, ddl in NOTICES_MIGRATION_COLUMNS:
                if name in missing:
                    db.execute(f"alter table notices add column {name} {ddl}")
            backfillable = [name for name in missing if name in BACKFILLABLE_COLUMNS]
            if backfillable:
                _backfill_notices_schema(db, backfillable)
        if not _categories_backfill_checked:
            if db.execute("select count(*) from notice_categories").fetchone()[0] == 0:
                if db.execute("select count(*) from notices").fetchone()[0] > 0:
                    _backfill_notice_categories(db)
            _categories_backfill_checked = True
        db.commit()


def _backfill_notice_categories(db):
    """One-time classification of every existing notice's title. Only reached from
    _ensure_notices_schema while holding SCHEMA_MIGRATION_LOCK; guarded by
    _categories_backfill_checked so it runs once per process, not once per connection."""
    for row in db.execute("select id, title from notices").fetchall():
        for category, confidence in classify_categories(row["title"]):
            db.execute("insert or ignore into notice_categories values (?,?,?)", (row["id"], category, confidence))


def _backfill_notices_schema(db, backfillable_columns):
    """One-time backfill for rows that existed before their column was added. Only reached from
    _ensure_notices_schema while holding SCHEMA_MIGRATION_LOCK, so this runs exactly once per
    database, not on every connection."""
    if "organization" in backfillable_columns:
        db.execute("update notices set organization = authority where organization is null")
    if "first_seen" in backfillable_columns:
        db.execute("update notices set first_seen = discovered_at where first_seen is null")
    if "last_seen" in backfillable_columns:
        db.execute("update notices set last_seen = discovered_at where last_seen is null")
    if "province" in backfillable_columns:
        try:
            province_by_source = {s["id"]: s.get("province") for s in sources()}
        except (FileNotFoundError, json.JSONDecodeError):
            province_by_source = {}
        for source_id_value, province in province_by_source.items():
            if province:
                db.execute("update notices set province = ? where source_id = ? and province is null", (province, source_id_value))
    if "notice_type" in backfillable_columns or "status" in backfillable_columns:
        for row in db.execute("select id, title from notices where notice_type is null or status is null").fetchall():
            notice_type = classify_notice_type(row["title"])
            db.execute("update notices set notice_type = coalesce(notice_type, ?), status = coalesce(status, ?) where id = ?",
                       (notice_type, status_for_notice_type(notice_type), row["id"]))


def source_id(name): return "sp-" + hashlib.sha1(name.encode()).hexdigest()[:12]
def normalized_host(url): return urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")


def save_json(path, payload):
    # The temp filename is unique per (process, thread): a shared fixed ".tmp" name meant two
    # concurrent callers could write over *each other's* temp file before either replace()d it,
    # corrupting or crashing both writes -- confirmed happening in practice (FileNotFoundError /
    # JSONDecodeError) while building the REGISTRY_WRITE_LOCK regression test below. The lock is
    # the primary fix (it serializes the whole read-modify-write cycle); this is defense in depth
    # so save_json() itself can't corrupt data even if some future caller forgets to hold it.
    temporary=path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n")
    temporary.replace(path)


def sources(): return json.loads(SOURCES.read_text())
def save_sources(items): save_json(SOURCES, items)


def watchlists():
    if not WATCHLISTS.exists(): return []
    try: return json.loads(WATCHLISTS.read_text())
    except json.JSONDecodeError: return []
def save_watchlists(items): save_json(WATCHLISTS, items)


def company_profiles():
    if not COMPANY_PROFILES.exists(): return []
    try: return json.loads(COMPANY_PROFILES.read_text())
    except json.JSONDecodeError: return []
def save_company_profiles(items): save_json(COMPANY_PROFILES, items)


def _string_list(value):
    if isinstance(value, str): value = [item.strip() for item in value.split(",") if item.strip()]
    if not isinstance(value, list): raise ValueError("Categories, provinces, and keywords must each be a list.")
    return [clean(str(item)) for item in value if clean(str(item))]


def validate_company_profile(payload, current=None):
    """Milestone 5: a company's matching preferences, in the same shape/validation style as
    validate_watchlist/validate_source above. Deliberately no min/max budget field yet --
    estimated_amount stays null until Milestone 10 (see documents.py), and a matching dimension
    over a field that's always null would be dead code, not a real preference."""
    name=clean(str(payload.get("name", current.get("name", "") if current else "")))
    categories=_string_list(payload.get("categories", current.get("categories", []) if current else []))
    provinces=_string_list(payload.get("provinces", current.get("provinces", []) if current else []))
    keywords=_string_list(payload.get("keywords", current.get("keywords", []) if current else []))
    if not name: raise ValueError("A company name is required.")
    if not categories and not provinces and not keywords:
        raise ValueError("At least one of categories, provinces, or keywords is required to match against.")
    return {"id": current["id"] if current else "cp-"+hashlib.sha1(name.encode()).hexdigest()[:12],
            "name": name, "categories": categories, "provinces": provinces, "keywords": keywords}


# Milestone 7: the filter fields a watchlist can save, beyond source_ids -- exactly
# queries.list_notices()'s own filter set (minus query/limit/offset), so a watchlist is a genuine
# saved search rather than a second, parallel filter vocabulary. Existing watchlists (e.g. the
# checked-in "Important" entry, which predates this) simply lack these keys -- storage.watchlists()
# returns them as-is, and every reader below defaults a missing key to "no filter", so old entries
# keep working unchanged until someone edits and re-saves them.
WATCHLIST_TEXT_FILTERS = ("query", "province", "notice_type", "status", "category", "discovered_after", "discovered_before")


def validate_watchlist(payload, current=None):
    name=clean(str(payload.get("name", current.get("name", "") if current else "")))
    source_ids=payload.get("source_ids", current.get("source_ids", []) if current else [])
    if not name: raise ValueError("A watchlist name is required.")
    if not isinstance(source_ids, list): raise ValueError("Source selections must be a list.")
    valid_ids={item["id"] for item in sources()}
    selected=[item for item in dict.fromkeys(str(value) for value in source_ids) if item in valid_ids]
    result={"id":current["id"] if current else "wl-"+hashlib.sha1(name.encode()).hexdigest()[:12], "name":name, "source_ids":selected}
    for key in WATCHLIST_TEXT_FILTERS:
        value=payload.get(key, current.get(key, "") if current else "")
        result[key]=clean(str(value)) if value else ""
    has_documents=payload.get("has_documents", current.get("has_documents") if current else None)
    if isinstance(has_documents, str): has_documents={"true":True,"false":False}.get(has_documents.lower())
    if has_documents is not None and not isinstance(has_documents, bool):
        raise ValueError("has_documents must be true, false, or omitted.")
    result["has_documents"]=has_documents
    return result


def validate_source(payload, current=None):
    name=clean(str(payload.get("name", current.get("name", "") if current else "")))
    url=clean(str(payload.get("url", current.get("url", "") if current else "")))
    notice_url=clean(str(payload.get("notice_url", current.get("notice_url", url) if current else url)))
    keywords=payload.get("keywords", current.get("keywords", []) if current else [])
    favorite=payload.get("favorite", current.get("favorite", False) if current else False)
    province=clean(str(payload.get("province", current.get("province", "National / other") if current else "National / other")))
    if isinstance(favorite, str): favorite=favorite.lower() in ("1", "true", "yes", "on")
    if isinstance(keywords, str): keywords=[value.strip() for value in keywords.split(",") if value.strip()]
    if not name or not url.startswith(("https://", "http://")) or not notice_url.startswith(("https://", "http://")):
        raise ValueError("Name, website URL, and notice URL are required.")
    for candidate in (url, notice_url):
        host=urllib.parse.urlparse(candidate).hostname
        if not host or host.lower() in ("localhost", "localhost.localdomain") or host.lower().endswith(".local"):
            raise ValueError("Source URLs must use a public website host.")
        try:
            if not ipaddress.ip_address(host).is_global:
                raise ValueError("Source URLs cannot use a private or local IP address.")
        except ValueError as exc:
            if "private or local" in str(exc): raise
    return {"id": current["id"] if current else source_id(name), "name":name, "url":url, "notice_url":notice_url, "keywords":keywords, "favorite":bool(favorite), "province":province or "National / other"}
