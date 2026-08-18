#!/usr/bin/env python3
"""Sudurpashchim Tender Monitor: collector, HTTP API, and MCP stdio server."""
import base64, hashlib, html, ipaddress, json, os, re, secrets, sqlite3, sys, threading, time, urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB = DATA_DIR / "tenders.db"
SOURCES = DATA_DIR / "sources.json"
if not SOURCES.exists() and (ROOT / "sources.json").exists():
    SOURCES.write_text((ROOT / "sources.json").read_text())
WATCHLISTS = DATA_DIR / "watchlists.json"
if not WATCHLISTS.exists() and (ROOT / "watchlists.json").exists():
    WATCHLISTS.write_text((ROOT / "watchlists.json").read_text())
USER_AGENT = "SudurpashchimTenderMonitor/0.1 (company pilot; contact: admin@example.com)"
TENDER_WORDS = ("tender", "bid", "bidding", "procurement", "bolpatra", "बोलपत्र", "दरभाउ", "खरिद", "आशय")

def load_dotenv():
    env_file = ROOT / ".env"
    if not env_file.exists(): return
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())
load_dotenv()

class LinkTextParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.links=[]; self._href=None; self._parts=[]
    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href"); self._parts=[]
    def handle_data(self, data):
        if self._href: self._parts.append(data)
    def handle_endtag(self, tag):
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join(self._parts).strip()))
            self._href=None; self._parts=[]

class OfficialDirectoryParser(HTMLParser):
    """Extract local-level rows and website links from the official Ministry directory."""
    def __init__(self):
        super().__init__(); self.rows=[]; self._in_row=False; self._in_cell=False; self._cells=[]; self._cell_parts=[]; self._links=[]; self._link=None
    def handle_starttag(self, tag, attrs):
        if tag == "tr": self._in_row=True; self._cells=[]; self._links=[]
        elif self._in_row and tag in ("td", "th"): self._in_cell=True; self._cell_parts=[]
        elif self._in_row and tag == "a": self._link=dict(attrs).get("href")
    def handle_data(self, data):
        if self._in_cell: self._cell_parts.append(data)
    def handle_endtag(self, tag):
        if tag == "a":
            if self._link: self._links.append(self._link)
            self._link=None
        elif self._in_row and tag in ("td", "th"):
            self._cells.append(clean(" ".join(self._cell_parts))); self._in_cell=False
        elif tag == "tr" and self._in_row:
            if self._cells: self.rows.append((self._cells, self._links))
            self._in_row=False

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

PROVINCES={"1":"Koshi","2":"Madhesh","3":"Bagmati","4":"Gandaki","5":"Lumbini","6":"Karnali","7":"Sudurpashchim"}
def sources(): return json.loads(SOURCES.read_text())
def source_id(name): return "sp-" + hashlib.sha1(name.encode()).hexdigest()[:12]
def normalized_host(url): return urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")
def official_directory_sources(province_code):
    if province_code not in PROVINCES: raise ValueError("Province must be a code from 1 to 7.")
    province=PROVINCES[province_code]
    pages=[]
    for kind in ("mun", "village-mun"):
        base=f"https://mofaga.gov.np/local-contact/{kind}-prov-{province_code}"
        pages.append(base)
        pages.extend(f"{base}?page={page}" for page in range(1,6))
    found=[]; errors=[]
    for page in pages:
        try:
            parser=OfficialDirectoryParser(); parser.feed(fetch(page))
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            errors.append({"page":page,"detail":str(exc)})
            continue
        for cells, links in parser.rows:
            website=next((urllib.parse.urljoin(page, link) for link in links if ".gov.np" in link), None)
            if not website or len(cells) < 4: continue
            name=cells[2]
            if not name or "स्थानीय तहको नाम" in name: continue
            found.append({"id":source_id(name),"name":name,"url":website,"notice_url":website,"keywords":["tender","bid","bolpatra","बोलपत्र","दरभाउ","खरिद"],"province":province})
    return found, errors
def tag_existing_sources(items):
    changed=False
    for item in items:
        if "province" not in item:
            item["province"]="National / other" if "jobsnepal" in normalized_host(item["url"]) else "Sudurpashchim"; changed=True
    return changed
def bootstrap_province(province_code):
    existing=sources(); by_host={normalized_host(s["url"]):s for s in existing}; by_name={s["name"]:s for s in existing}
    changed=tag_existing_sources(existing)
    imported, errors=official_directory_sources(province_code)
    for source in imported:
        if normalized_host(source["url"]) not in by_host and source["name"] not in by_name:
            existing.append(source); by_host[normalized_host(source["url"])]=source; by_name[source["name"]]=source
            changed=True
    if changed: save_sources(existing)
    return {"province":PROVINCES[province_code],"sources":len(existing),"imported":len(imported),"directory_errors":errors,"file":str(SOURCES)}
def bootstrap_sudurpashchim(): return bootstrap_province("7")
def bootstrap_karnali(): return bootstrap_province("6")
def bootstrap_lumbini(): return bootstrap_province("5")
def sync_all_local_levels():
    results=[]
    for province_code in PROVINCES:
        try: results.append(bootstrap_province(province_code))
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            results.append({"province":PROVINCES[province_code],"error":str(exc)})
    return {"results":results,"sources":len(sources()),"file":str(SOURCES)}
def save_json(path, payload):
    temporary=path.with_name(path.name+".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n")
    temporary.replace(path)
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
def fetch(url):
    req=urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language":"en,ne;q=0.8"})
    timeout = int(os.getenv("SOURCE_TIMEOUT_SECONDS", "45"))
    retries = max(1, int(os.getenv("SOURCE_RETRIES", "2")))
    last_error = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode(r.headers.get_content_charset() or "utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt < retries - 1: time.sleep(3 * (attempt + 1))
    raise last_error
def clean(text): return re.sub(r"\s+", " ", html.unescape(text)).strip()
DATE_PATTERNS = (
    r"\b\d{4}[/-]\d{1,2}[/-]\d{1,2}\b",
    r"\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b",
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{1,2},?\s+\d{4}\b",
    r"\b\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{4}\b",
    r"[०-९]{4}[/-][०-९]{1,2}[/-][०-९]{1,2}",
)
def first_date(text):
    for pattern in DATE_PATTERNS:
        match=re.search(pattern, text, re.IGNORECASE)
        if match: return match.group(0)
    return None
def published_date(body, href, title):
    """Return a published-date string only when the source page exposes one near its link."""
    candidates=[title]
    position=body.find(href)
    if position >= 0:
        candidates.append(clean(re.sub(r"<[^>]+>", " ", body[max(0,position-700):position+900])))
    for candidate in candidates:
        date=first_date(candidate)
        if date: return date
    return None
def linked_notice_date(url):
    """Check an individual HTML notice page when the listing itself has no date."""
    if url.lower().split("?",1)[0].endswith(".pdf"): return None
    try:
        page=fetch(url)
        return first_date(clean(re.sub(r"<[^>]+>", " ", page)))
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None
def relevant(text, source):
    lower=text.lower()
    return any(word.lower() in lower for word in TENDER_WORDS + tuple(source.get("keywords", [])))

def record_health(db, source_id, ok, detail, now):
    """Upsert a source's fetch health. Success resets the failure streak; failure extends it."""
    if ok:
        db.execute("""insert into source_health (source_id,last_status,last_detail,last_run_at,last_success_at,consecutive_failures)
            values (?,'ok',?,?,?,0)
            on conflict(source_id) do update set last_status='ok', last_detail=excluded.last_detail,
                last_run_at=excluded.last_run_at, last_success_at=excluded.last_success_at, consecutive_failures=0""",
            (source_id, detail[:500], now, now))
    else:
        db.execute("""insert into source_health (source_id,last_status,last_detail,last_run_at,last_success_at,consecutive_failures)
            values (?,'error',?,?,null,1)
            on conflict(source_id) do update set last_status='error', last_detail=excluded.last_detail,
                last_run_at=excluded.last_run_at, consecutive_failures=source_health.consecutive_failures+1""",
            (source_id, detail[:500], now))

def health_skip_settings():
    threshold=max(1, int(os.getenv("SOURCE_FAILURE_SKIP_THRESHOLD", "5")))
    cooldown_minutes=max(0, int(os.getenv("SOURCE_FAILURE_SKIP_COOLDOWN_MINUTES", "360")))
    return threshold, cooldown_minutes
def should_skip(db, source_id, threshold, cooldown_minutes):
    """True when a source has failed threshold+ times in a row and its cooldown hasn't elapsed yet."""
    row=db.execute("select consecutive_failures, last_run_at from source_health where source_id=?", (source_id,)).fetchone()
    if not row or row["consecutive_failures"] < threshold or not row["last_run_at"]: return False
    elapsed=datetime.now(timezone.utc) - datetime.fromisoformat(row["last_run_at"])
    return elapsed.total_seconds() < cooldown_minutes * 60

def send_whatsapp_alert(notice):
    required = ("WHATSAPP_API_URL", "WHATSAPP_ACCESS_TOKEN", "WHATSAPP_RECIPIENT", "WHATSAPP_TEMPLATE_NAME")
    if not all(os.getenv(key) for key in required):
        return "skipped", "WhatsApp is not configured"
    payload = {
        "messaging_product": "whatsapp", "to": os.environ["WHATSAPP_RECIPIENT"], "type": "template",
        "template": {
            "name": os.environ["WHATSAPP_TEMPLATE_NAME"],
            "language": {"code": os.getenv("WHATSAPP_TEMPLATE_LANGUAGE", "en_US")},
            "components": [{"type": "body", "parameters": [
                {"type": "text", "text": notice["authority"]},
                {"type": "text", "text": notice["title"]},
                {"type": "text", "text": notice["url"]}
            ]}]
        }
    }
    request = urllib.request.Request(
        os.environ["WHATSAPP_API_URL"], data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": "Bearer " + os.environ["WHATSAPP_ACCESS_TOKEN"], "Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return "sent", response.read().decode(errors="replace")[:500]
    except urllib.error.HTTPError as exc:
        return "error", exc.read().decode(errors="replace")[:500]
    except urllib.error.URLError as exc:
        return "error", str(exc)

def collect_one(source):
    now=datetime.now(timezone.utc).isoformat()
    db=conn()
    try:
        body=fetch(source["notice_url"])
        parser=LinkTextParser(); parser.feed(body)
        added=0; new_notices=[]
        for href, label in parser.links:
            title=clean(label)
            url=urllib.parse.urljoin(source["notice_url"], href)
            if len(title) < 8 or not relevant(title + " " + url, source): continue
            published=published_date(body, href, title)
            if not published and os.getenv("NOTICE_PAGE_DATE_LOOKUPS", "1") == "1":
                published=linked_notice_date(url)
            digest=hashlib.sha256((source["id"]+url+title).encode()).hexdigest()
            db.execute("""insert or ignore into notices
                (id,source_id,authority,title,url,discovered_at,published_at,relevant,raw_text)
                values (?,?,?,?,?,?,?,?,?)""", (digest,source["id"],source["name"],title,url,now,published,1,title))
            inserted = db.execute("select changes()").fetchone()[0]
            if not inserted and published:
                db.execute("update notices set published_at=coalesce(published_at,?) where id=?", (published,digest))
            added += inserted
            if inserted: new_notices.append({"id":digest, "authority":source["name"], "title":title, "url":url})
        for notice in new_notices:
            status, detail = send_whatsapp_alert(notice)
            db.execute("insert into deliveries values (?,?,?,?)", (notice["id"], now, status, detail))
        db.execute("insert into runs values (?,?,?,?)", (source["id"],now,"ok",f"{added} new notices"))
        record_health(db, source["id"], True, f"{added} new notices", now)
        db.commit()
        return {"source":source["name"],"status":"ok","new":added}
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        db.execute("insert into runs values (?,?,?,?)", (source["id"],now,"error",str(exc)))
        record_health(db, source["id"], False, str(exc), now)
        db.commit()
        return {"source":source["name"],"status":"error","detail":str(exc)}
    finally: db.close()

def collect_all(source_id=None):
    selected = [s for s in sources() if not source_id or s["id"] == source_id]
    if source_id and not selected:
        raise ValueError(f"Unknown source: {source_id}")
    if not selected: return []
    to_run, skipped = selected, []
    if not source_id:
        # Scheduled sweeps skip sources with a long failure streak until their cooldown elapses,
        # so chronically dead sites stop eating a full timeout*retries budget every cycle.
        # A manually requested single-source collection (source_id set) always runs regardless.
        threshold, cooldown_minutes = health_skip_settings()
        db=conn()
        try:
            to_run, skipped = [], []
            for s in selected:
                if should_skip(db, s["id"], threshold, cooldown_minutes):
                    skipped.append({"source":s["name"],"status":"skipped","detail":f"{threshold}+ consecutive failures; retrying after cooldown"})
                else:
                    to_run.append(s)
        finally: db.close()
    if not to_run: return skipped
    workers=min(len(to_run), max(1, int(os.getenv("COLLECTOR_WORKERS", "8"))))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(collect_one, to_run)) + skipped
def list_notices(query="", limit=50, source_id=""):
    limit=max(1, min(int(limit), 100))
    db=conn(); sql="select * from notices"; args=[]; conditions=[]
    if query:
        conditions.append("(lower(title) like ? or lower(authority) like ?)"); args.extend([f"%{query.lower()}%"]*2)
    if source_id:
        conditions.append("source_id = ?"); args.append(source_id)
    if conditions: sql += " where " + " and ".join(conditions)
    rows=[dict(r) for r in db.execute(sql+" order by discovered_at desc limit ?", args+[limit])]; db.close(); return rows
def source_summary():
    db=conn(); cutoff=datetime.now(timezone.utc).timestamp() - 86400; recent_cutoff=datetime.now(timezone.utc).timestamp() - 172800; result=[]
    threshold, cooldown_minutes = health_skip_settings()
    for source in sources():
        rows=db.execute("select discovered_at from notices where source_id=?", (source["id"],)).fetchall()
        new=sum(1 for r in rows if datetime.fromisoformat(r["discovered_at"]).timestamp() >= cutoff)
        recent=sum(1 for r in rows if datetime.fromisoformat(r["discovered_at"]).timestamp() >= recent_cutoff)
        unread=db.execute("select count(*) from notices where source_id=? and seen_at is null", (source["id"],)).fetchone()[0]
        health=db.execute("select last_status, last_detail, last_run_at, last_success_at, consecutive_failures from source_health where source_id=?", (source["id"],)).fetchone()
        result.append({"id":source["id"],"name":source["name"],"url":source["url"],"province":source.get("province","National / other"),"notice_count":len(rows),"new_count":new,"recent_count_48h":recent,"unread_count":unread,"favorite":source.get("favorite",False),
            "last_status":health["last_status"] if health else None,
            "last_error":health["last_detail"] if health and health["last_status"]=="error" else None,
            "last_run_at":health["last_run_at"] if health else None,
            "last_success_at":health["last_success_at"] if health else None,
            "consecutive_failures":health["consecutive_failures"] if health else 0,
            "skipped":should_skip(db, source["id"], threshold, cooldown_minutes)})
    db.close(); return result
def alert_summary():
    configured = all(os.getenv(key) for key in ("WHATSAPP_API_URL", "WHATSAPP_ACCESS_TOKEN", "WHATSAPP_RECIPIENT", "WHATSAPP_TEMPLATE_NAME"))
    db=conn(); rows=[dict(r) for r in db.execute("select notice_id, delivered_at, status, detail from deliveries order by rowid desc limit 8")]; db.close()
    return {"configured": configured, "deliveries": rows}
def details(notice_id):
    db=conn(); row=db.execute("select * from notices where id=?",(notice_id,)).fetchone(); db.close(); return dict(row) if row else None

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
    def respond(self, payload, status=200):
        data=json.dumps(payload, ensure_ascii=False).encode(); self.send_response(status); self.security_headers(); self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Content-Length",str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        if not self.require_auth(): return
        path, _, qs=self.path.partition("?"); params=urllib.parse.parse_qs(qs)
        if path == "/" or re.fullmatch(r"/source/[a-z0-9-]+", path):
            data=(ROOT / "dashboard.html").read_bytes()
            self.send_response(200); self.security_headers(); self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'"); self.send_header("Content-Type","text/html; charset=utf-8"); self.send_header("Content-Length",str(len(data))); self.end_headers(); self.wfile.write(data); return
        if path == "/":
            page = """<!doctype html><html><head><meta charset='utf-8'><title>Tender Monitor</title>
<style>body{font-family:system-ui,sans-serif;background:#f4f7fb;color:#14213d;margin:0}main{max-width:920px;margin:48px auto;padding:0 20px}h1{margin-bottom:4px}.sub{color:#586174}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px;margin:22px 0}.source{background:white;border:1px solid #dde4ee;border-radius:12px;padding:16px;text-align:left;position:relative;color:#14213d}.source.active{outline:3px solid #8dc8ff}.badge{background:#c82929;border-radius:99px;color:white;font-size:.75rem;padding:4px 7px;position:absolute;right:12px;top:12px}.card{background:white;border:1px solid #dde4ee;border-radius:12px;padding:18px;margin:14px 0;box-shadow:0 2px 7px #15233a0d}.meta{color:#586174;font-size:.9rem;margin:8px 0}a{color:#075cb5;text-decoration:none}.empty{padding:24px;text-align:center;color:#586174}button{background:#075cb5;border:0;border-radius:7px;color:white;padding:9px 13px;font-weight:600;cursor:pointer}.source{cursor:pointer}.count{color:#586174;font-size:.85rem;margin-top:10px}.notice-actions{margin-top:12px;display:flex;gap:14px;align-items:center}.read{background:#e8eef5;color:#14213d}</style></head><body><main>
<h1>Sudurpashchim Tender Monitor</h1><p class='sub'>Choose a local government to view its official procurement notices.</p><button onclick='load()'>Refresh notices</button><section id='alert' class='card'></section><section id='sources' class='grid'></section><h2 id='heading'>All notices</h2><section id='list' class='empty'>Loading notices…</section>
<script>let selected='';async function load(){try{const [sourceResponse,noticeResponse,alertResponse]=await Promise.all([fetch('/sources'),fetch('/notices?limit=50'+(selected?'&source='+encodeURIComponent(selected):'')),fetch('/alerts/status')]);const sources=await sourceResponse.json(),items=await noticeResponse.json(),alerts=await alertResponse.json();document.getElementById('alert').innerHTML=`<strong>WhatsApp alerts: ${alerts.configured?'ready':'setup required'}</strong><div class="meta">${alerts.configured?'New tenders will be sent automatically.':'Add your private WhatsApp Business settings to .env, then restart the server.'}</div>${alerts.deliveries.length?'<div class="meta">Recent delivery: '+alerts.deliveries[0].status+'</div>':''}`;document.getElementById('sources').innerHTML=sources.map(s=>`<button class="source ${s.id===selected?'active':''}" onclick="choose('${s.id}','${s.name.replace(/'/g,"\\'")}')"><strong>${s.name}</strong>${s.unread_count?`<span class="badge">${s.unread_count} unread</span>`:''}<div class="count">${s.notice_count} stored notices${s.new_count?' · '+s.new_count+' today':''}</div></button>`).join('');const box=document.getElementById('list');if(!items.length){box.className='empty';box.textContent='No notices have been collected for this local government yet.';return}box.className='';box.innerHTML=items.map(n=>`<article class="card"><strong>${n.title}</strong><div class="meta">${n.authority} · ${n.seen_at?'read':'unread'} · collected ${new Date(n.discovered_at).toLocaleString()}</div><div class="notice-actions"><a href="${n.url}" target="_blank" rel="noopener">Open the original official notice →</a>${n.seen_at?'':`<button class="read" onclick="markRead('${n.id}')">Mark as read</button>`}</div></article>`).join('')}catch(e){document.getElementById('list').textContent='The service is unavailable. Keep the server terminal running and refresh.'}}async function choose(id,name){selected=id;document.getElementById('heading').textContent=name+' notices';load()}async function markRead(id){await fetch('/notices/'+encodeURIComponent(id)+'/mark-seen',{method:'POST'});load()}load()</script>
</main></body></html>"""
            data=page.encode(); self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8"); self.send_header("Content-Length",str(len(data))); self.end_headers(); self.wfile.write(data); return
        if path == "/health": return self.respond({"status":"ok"})
        if path == "/sources": return self.respond(source_summary())
        if path == "/watchlists": return self.respond(watchlists())
        if path == "/alerts/status": return self.respond(alert_summary())
        if path == "/notices": return self.respond(list_notices(params.get("query",[""])[0], int(params.get("limit",[50])[0]), params.get("source",[""])[0]))
        if path.startswith("/notices/"):
            record=details(path.rsplit("/",1)[1]); return self.respond(record or {"error":"not found"}, 200 if record else 404)
        self.respond({"error":"not found"},404)
    def do_POST(self):
        if not self.require_auth(): return
        if self.path == "/watchlists":
            try:
                payload=self.json_body()
                items=watchlists(); item=validate_watchlist(payload)
                if any(existing["id"] == item["id"] for existing in items): return self.respond({"error":"A watchlist with this name already exists."},409)
                items.append(item); save_watchlists(items); return self.respond(item,201)
            except (ValueError, json.JSONDecodeError) as exc: return self.respond({"error":str(exc)},400)
        if self.path == "/sources":
            try:
                payload=self.json_body()
                items=sources(); item=validate_source(payload)
                if any(existing["id"] == item["id"] for existing in items): return self.respond({"error":"A source with this name already exists."},409)
                items.append(item); save_sources(items)
                threading.Thread(target=collect_one, args=(item,), daemon=True).start()
                return self.respond({**item,"collection":"started"},201)
            except (ValueError, json.JSONDecodeError) as exc: return self.respond({"error":str(exc)},400)
        notice_match=re.fullmatch(r"/notices/([a-f0-9]{64})/mark-seen", self.path)
        if notice_match:
            db=conn(); now=datetime.now(timezone.utc).isoformat()
            db.execute("update notices set seen_at=? where id=? and seen_at is null", (now,notice_match.group(1))); count=db.execute("select changes()").fetchone()[0]; db.commit(); db.close()
            return self.respond({"marked_seen":count})
        match=re.fullmatch(r"/sources/([a-z0-9-]+)/mark-seen", self.path)
        if not match: return self.respond({"error":"not found"},404)
        db=conn(); now=datetime.now(timezone.utc).isoformat()
        db.execute("update notices set seen_at=? where source_id=? and seen_at is null", (now,match.group(1))); count=db.execute("select changes()").fetchone()[0]; db.commit(); db.close()
        self.respond({"marked_seen":count})
    def do_PATCH(self):
        if not self.require_auth(): return
        watchlist_match=re.fullmatch(r"/watchlists/(wl-[a-f0-9]+)", self.path)
        if watchlist_match:
            try:
                payload=self.json_body()
                items=watchlists(); index=next((i for i, item in enumerate(items) if item["id"]==watchlist_match.group(1)), None)
                if index is None: return self.respond({"error":"not found"},404)
                items[index]=validate_watchlist(payload,items[index]); save_watchlists(items); return self.respond(items[index])
            except (ValueError, json.JSONDecodeError) as exc: return self.respond({"error":str(exc)},400)
        match=re.fullmatch(r"/sources/([a-z0-9-]+)", self.path)
        if not match: return self.respond({"error":"not found"},404)
        try:
            payload=self.json_body()
            items=sources(); index=next((i for i, item in enumerate(items) if item["id"]==match.group(1)), None)
            if index is None: return self.respond({"error":"not found"},404)
            items[index]=validate_source(payload,items[index]); save_sources(items); self.respond(items[index])
        except (ValueError, json.JSONDecodeError) as exc: self.respond({"error":str(exc)},400)
    def do_DELETE(self):
        if not self.require_auth(): return
        watchlist_match=re.fullmatch(r"/watchlists/(wl-[a-f0-9]+)", self.path)
        if watchlist_match:
            items=watchlists(); remaining=[item for item in items if item["id"]!=watchlist_match.group(1)]
            if len(remaining)==len(items): return self.respond({"error":"not found"},404)
            save_watchlists(remaining); return self.respond({"removed":watchlist_match.group(1)})
        match=re.fullmatch(r"/sources/([a-z0-9-]+)", self.path)
        if not match: return self.respond({"error":"not found"},404)
        items=sources(); remaining=[item for item in items if item["id"]!=match.group(1)]
        if len(remaining)==len(items): return self.respond({"error":"not found"},404)
        save_sources(remaining)
        source_id=match.group(1); lists=watchlists()
        for item in lists: item["source_ids"]=[value for value in item.get("source_ids",[]) if value!=source_id]
        save_watchlists(lists); self.respond({"removed":source_id})
    def log_message(self, *_): pass

def serve():
    interval_minutes = int(os.getenv("AUTO_COLLECT_INTERVAL_MINUTES", "60"))
    def scheduled_collection():
        while True:
            started=time.monotonic()
            print("Automatic collection started", flush=True)
            results=collect_all()
            counts={}
            for result in results:
                counts[result["status"]]=counts.get(result["status"],0)+1
                if result["status"]=="error": print(json.dumps(result, ensure_ascii=False), flush=True)
            elapsed=round(time.monotonic()-started,1)
            print(f"Automatic collection finished in {elapsed}s: {json.dumps(counts, ensure_ascii=False)}", flush=True)
            time.sleep(max(interval_minutes, 5) * 60)
    threading.Thread(target=scheduled_collection, daemon=True).start()
    host=os.getenv("HOST", "127.0.0.1"); port=int(os.getenv("PORT","8787"))
    server=ThreadingHTTPServer((host, port), Api)
    print(f"Tender Monitor dashboard listening on http://{host}:{port} (automatic collection every {interval_minutes} minutes)", flush=True); server.serve_forever()

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
        result = list_notices(args.get("query", ""), args.get("limit",20)) if name=="search_tenders" else list_notices("",args.get("limit",20)) if name=="latest_tenders" else details(args.get("id")) if name=="tender_details" else {"error":"unknown tool"}
        return {"content":[{"type":"text","text":json.dumps(result,ensure_ascii=False)}]}
    return None

def mcp():
    for line in sys.stdin:
        try:
            req=json.loads(line); result=mcp_response(req)
            if "id" in req and result is not None: print(json.dumps({"jsonrpc":"2.0","id":req["id"],"result":result},ensure_ascii=False),flush=True)
        except Exception as exc:
            print(json.dumps({"jsonrpc":"2.0","id":None,"error":{"code":-32603,"message":str(exc)}}),flush=True)

def alert():
    notices=list_notices("",10)
    message="No newly collected tender notices." if not notices else "Latest tender notices:\n" + "\n".join(f"• {n['authority']}: {n['title']}\n{n['url']}" for n in notices[:3])
    if not all(os.getenv(k) for k in ("WHATSAPP_ACCESS_TOKEN","WHATSAPP_PHONE_NUMBER_ID","WHATSAPP_RECIPIENT")):
        print("TEST ALERT (not sent)\n"+message); return
    raise SystemExit("WhatsApp credentials are present. Configure an approved template payload before enabling production sends.")

def test_whatsapp():
    status, detail = send_whatsapp_alert({
        "authority":"Notice Feed test",
        "title":"WhatsApp alert connection test",
        "url":"http://127.0.0.1:8787/",
    })
    print(json.dumps({"status":status,"detail":detail},ensure_ascii=False,indent=2))

if __name__ == "__main__":
    command=sys.argv[1] if len(sys.argv)>1 else "help"
    if command=="collect": print(json.dumps(collect_all(sys.argv[2] if len(sys.argv)>2 else None),ensure_ascii=False,indent=2))
    elif command=="serve": serve()
    elif command=="mcp": mcp()
    elif command=="alert": alert()
    elif command=="test-whatsapp": test_whatsapp()
    elif command=="bootstrap-sudurpashchim": print(json.dumps(bootstrap_sudurpashchim(),ensure_ascii=False,indent=2))
    elif command=="bootstrap-karnali": print(json.dumps(bootstrap_karnali(),ensure_ascii=False,indent=2))
    elif command=="bootstrap-lumbini": print(json.dumps(bootstrap_lumbini(),ensure_ascii=False,indent=2))
    elif command=="sync-all-local-levels": print(json.dumps(sync_all_local_levels(),ensure_ascii=False,indent=2))
    else: print("Usage: app.py [sync-all-local-levels|bootstrap-sudurpashchim|bootstrap-karnali|bootstrap-lumbini|collect [source-id]|serve|mcp|alert|test-whatsapp]")
