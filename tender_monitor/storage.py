"""SQLite schema/connection, and the JSON-file-backed source/watchlist registries."""
import hashlib
import ipaddress
import json
import sqlite3
import threading
import urllib.parse

from .config import DB, SOURCES, WATCHLISTS
from .parsing import clean

# SQLite allows only one writer at a time; under WAL, concurrent writers past that point block on
# the busy handler and can still hit "database is locked" once enough threads are writing at once
# (this happened in production once COLLECTOR_WORKERS was raised). Funnel every write through this
# lock instead of relying solely on busy_timeout, so writers queue in-process rather than erroring.
DB_WRITE_LOCK = threading.Lock()


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
    columns = {row[1] for row in db.execute("pragma table_info(notices)")}
    if "seen_at" not in columns: db.execute("alter table notices add column seen_at text")
    if "published_at" not in columns: db.execute("alter table notices add column published_at text")
    db.execute("create table if not exists runs (source_id text, ran_at text, status text, detail text)")
    db.execute("""create table if not exists deliveries (
        notice_id text, delivered_at text, status text, detail text
    )""")
    db.execute("""create table if not exists source_health (
        source_id text primary key, last_status text, last_detail text,
        last_run_at text, last_success_at text, consecutive_failures integer not null default 0
    )""")
    return db


def source_id(name): return "sp-" + hashlib.sha1(name.encode()).hexdigest()[:12]
def normalized_host(url): return urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")


def save_json(path, payload):
    temporary=path.with_name(path.name+".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n")
    temporary.replace(path)


def sources(): return json.loads(SOURCES.read_text())
def save_sources(items): save_json(SOURCES, items)


def watchlists():
    if not WATCHLISTS.exists(): return []
    try: return json.loads(WATCHLISTS.read_text())
    except json.JSONDecodeError: return []
def save_watchlists(items): save_json(WATCHLISTS, items)


def validate_watchlist(payload, current=None):
    name=clean(str(payload.get("name", current.get("name", "") if current else "")))
    source_ids=payload.get("source_ids", current.get("source_ids", []) if current else [])
    if not name: raise ValueError("A watchlist name is required.")
    if not isinstance(source_ids, list): raise ValueError("Source selections must be a list.")
    valid_ids={item["id"] for item in sources()}
    selected=[item for item in dict.fromkeys(str(value) for value in source_ids) if item in valid_ids]
    return {"id":current["id"] if current else "wl-"+hashlib.sha1(name.encode()).hexdigest()[:12], "name":name, "source_ids":selected}


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
