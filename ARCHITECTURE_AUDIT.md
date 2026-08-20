# Architecture Audit — Sudurpashchim Tender Monitor

Audited: 2026-08-20. Codebase: `app.py` (606 lines), `dashboard.html` (58 lines, minified inline JS/CSS), `tests/` (37 tests, all passing), stdlib-only (no third-party dependencies), deployed on Railway (single instance, SQLite on a persistent volume, GitHub-connected auto-deploy).

This audit is evidence-based: every claim below is anchored to a specific line range in the current `app.py`, not general impressions.

## 1. Architecture map

The whole application is one Python file with no internal package structure. It has five entry points selected by `sys.argv[1]` (`app.py:596-606`): `collect`, `serve`, `mcp`, `alert`, `test-whatsapp`, plus four `bootstrap-*`/`sync-all-local-levels` source-import commands. In production, Railway runs `python3 app.py serve` (`railway.toml`, `Procfile`).

`serve()` (`app.py:527-556`) does two things in one process: starts a daemon thread running an infinite collect-then-sleep loop, and starts a blocking `ThreadingHTTPServer` on the main thread. There is no process/worker separation — collection, HTTP serving, and (when invoked) MCP stdio serving are all the same codebase with different entry functions, never composed together in one run.

## 2. Major functions/classes, by responsibility

| Responsibility | Symbols | Lines |
|---|---|---|
| HTML parsing | `LinkTextParser`, `OfficialDirectoryParser` | 32-63 |
| Storage/schema | `conn()` | 74-95 |
| Source registry (file-backed) | `sources()`, `save_sources()`, `source_id()`, `normalized_host()`, `validate_source()` | 97-100, 153, 167-187 |
| Source discovery/import | `official_directory_sources()`, `tag_existing_sources()`, `bootstrap_province()` and its 3 wrappers, `sync_all_local_levels()` | 101-148 |
| Watchlists (file-backed) | `watchlists()`, `save_watchlists()`, `validate_watchlist()` | 154-166 |
| Networking | `fetch()` | 188-200 |
| Text/date utilities | `clean()`, `DATE_PATTERNS`, `first_date()`, `published_date()`, `linked_notice_date()`, `relevant()` | 201-240 |
| Source health tracking | `record_health()`, `health_skip_settings()`, `should_skip()` | 242-266 |
| Alert delivery | `send_whatsapp_alert()` | 268-294 |
| Collection pipeline | `collect_one()`, `collect_all()` | 296-387 |
| Read models for API/MCP | `list_notices()`, `source_summary()`, `alert_summary()`, `details()` | 388-419 |
| HTTP API | `Api(BaseHTTPRequestHandler)` | 421-525 |
| Scheduler | `serve()` | 527-556 |
| MCP server | `mcp_response()`, `mcp()` | 558-578 |
| CLI-only alert helpers | `alert()`, `test_whatsapp()` | 580-593 |

## 3. Collection pipeline (as it exists today)

`collect_all()` (364-387) → for each source not currently in cooldown (see §6) → `collect_one()` (296-362), run concurrently via `ThreadPoolExecutor(max_workers=COLLECTOR_WORKERS)`:

1. `fetch(source["notice_url"])` — one HTTP GET of the listing page.
2. `LinkTextParser` extracts every `(href, text)` pair from `<a>` tags.
3. Per link: title-length floor (`len(title) < 8`) and keyword relevance filter (`relevant()`) — anything not matching is dropped, permanently (not stored anywhere, not counted).
4. `published_date()` looks for a date pattern in the title or in ~1.6KB of HTML around the link on the listing page.
5. For candidates still missing a date, `linked_notice_date()` fetches the individual notice page (capped, parallelized — see §12 for why).
6. A single locked section (`DB_WRITE_LOCK`) inserts all candidates with `insert or ignore` keyed by `sha256(source_id + url + title)`, backfills `published_at` on existing rows, writes a `runs` row, and upserts `source_health`.
7. For genuinely new notices, `send_whatsapp_alert()` fires (network call, outside the lock) and a `deliveries` row is written.

There is no document download step, no OCR, no classification, no normalization beyond `clean()`, and no schema beyond "title + URL + authority + a best-effort date". This is a link-scraper, not a document-intelligence pipeline.

## 4. Database/storage architecture

Single SQLite file (`tenders.db`, WAL mode, `busy_timeout=20000`) on a Railway volume mounted at `/app/data`. Five tables, all created idempotently in `conn()` (74-95):

- `notices` — the only "domain" table. 9 columns: `id` (the dedup digest), `source_id`, `authority`, `title`, `url`, `discovered_at`, `relevant`, `raw_text` (duplicate of `title`), `seen_at`, `published_at`. No organization/category/deadline/amount/status fields exist.
- `runs` — one row per collection attempt per source (append-only log, no schema versioning, unindexed).
- `deliveries` — one row per WhatsApp send attempt.
- `source_health` — one row per source, upserted (added this session for skip/cooldown logic).

No indexes exist beyond the `notices.id` primary key. `list_notices()` filters/sorts on `source_id` and `discovered_at` with no supporting index (391-396).

Configuration that is logically part of the domain (the source registry, watchlists) lives outside SQLite entirely, in two flat JSON files (`sources.json`, `watchlists.json`) rewritten in full via `save_json()`'s write-temp-then-rename pattern (149-152) on every mutation — safe against partial writes, **not** safe against two concurrent mutations (no locking around the read-modify-write cycle, unlike the DB path which now has `DB_WRITE_LOCK`).

## 5. Source discovery

Two mechanisms, never unified:

- **Manual**: `POST /sources` / dashboard "Add source" form → `validate_source()` (167-187, includes SSRF guards) → appended to `sources.json`.
- **Bulk import**: `bootstrap_province(code)` (129-138) scrapes the Ministry of Federal Affairs' official local-government directory (`mofaga.gov.np`) with `OfficialDirectoryParser`, dedupes against existing sources by hostname/name, and appends. `sync_all_local_levels()` (142-148) runs this for all 7 provinces. This is how `sources.json` grew from the original "four Kailali-area governments" to 662 sources across all of Nepal (discovered and discussed earlier in this session).

There is no concept of federal ministries, departments, public enterprises, universities, hospitals, or PPMO/e-GP sources — only local-government (municipality/rural municipality) entries from the MOFAGA directory. The administrative hierarchy exists only as a flat `province` string field on each source; there is no district or organization-type dimension at all.

## 6. Tender deduplication

`id = sha256(source_id + url + title)` (309), inserted with `insert or ignore` (331-333). This is **exact-match dedup only**:

- If a government site changes a title by one character (extra whitespace after `clean()`, a re-published listing with a slightly different label) or changes the URL (query string reordering, a redirect target, a CMS re-slug), the same real-world notice is treated as brand-new — a duplicate row, a duplicate WhatsApp alert.
- There is no fuzzy/semantic matching, no cross-source matching (the same national tender re-posted on two sites is two separate rows), and no notion of a notice being *updated* (deadline extended, BOQ changed) — a re-scrape either produces a new row or is silently ignored; the original row's content is never touched except a one-time `published_at` backfill (335-336).

## 7. Scheduling

A single daemon thread (`scheduled_collection()`, 529-552) loops: run `collect_all()` for every source, then `time.sleep(max(interval_minutes, 5) * 60)`. There is no cron, no job queue, no per-source scheduling — every source is swept every cycle, cycle cadence = collection duration + configured interval. As of this session's fixes, a full 662-source sweep takes ~8-9 minutes (down from 62-174 minutes before the concurrency and per-notice-lookup fixes applied earlier today). The loop is wrapped in `try/except` (538-551) specifically so no single unexpected error can kill the thread — this was a real production incident earlier in this session (see git log `d5a70bf`).

## 8. MCP tools

Three tools, declared in `mcp_response()` (558-570): `search_tenders`, `latest_tenders`, `tender_details`. All three are thin wrappers over `list_notices()`/`details()` — no pagination beyond `limit`, no structured error objects (a failure anywhere in `mcp()`'s loop becomes a generic JSON-RPC `-32603` with the raw exception string, 572-578), no tool for source health, collection status, organizations, or analytics despite those read-models (`source_summary()`, `last_cycle`) already existing and being served over HTTP.

## 9. Alert delivery

WhatsApp Business Cloud API only (`send_whatsapp_alert()`, 268-294), one hardcoded recipient (`WHATSAPP_RECIPIENT`, a single value, not a list), fired once per newly-inserted notice, from inside `collect_one()`. No provider abstraction, no email/Telegram/SMS, no per-watchlist or per-user targeting, no alert *types* (everything is "new tender" — there is no deadline-reminder, change-detection, or source-failure alert, though the ingredients for a source-failure alert now exist via `source_health`). Delivery is logged to `deliveries` but never retried on failure.

## 10. Dashboard/API architecture

`Api(BaseHTTPRequestHandler)` (421-525) hand-routes on `self.path` string matching — no framework, no versioning (`/sources`, not `/api/v1/sources`), no OpenAPI/schema. Endpoints: `GET /`, `/source/{id}` (serves `dashboard.html` as a static SPA shell), `/health` (unauthenticated, for Railway's healthcheck), `/sources`, `/watchlists`, `/alerts/status`, `/collection/status`, `/notices`, `/notices/{id}`; `POST/PATCH/DELETE` on `/sources`, `/watchlists`, plus mark-seen endpoints. Auth is HTTP Basic against a single username/password pair from env vars (427-438), gated by `REQUIRE_AUTH`. `dashboard.html` is a single static file with all JS/CSS inlined and minified by hand — no build step, no component structure, and it fetches the *entire* `/sources` (662 rows) and up to 100 `/notices` on every load with no pagination.

## 11. Technical debt

- Everything in one 606-line file: scraping, parsing, persistence, HTTP routing, scheduling, and MCP protocol handling are interleaved with no module boundaries. This is the primary blocker for every feature in the target spec — there is nowhere to put a second source adapter, a document pipeline, or an AI provider without it becoming more entangled.
- Config-in-JSON-files vs. domain-in-SQLite is a split-brain persistence model with different consistency guarantees (file writes have no concurrency lock; DB writes now do).
- `raw_text` in `notices` duplicates `title` — vestigial, never used differently.
- `alert()` (CLI-only helper, 580-585) checks for `WHATSAPP_PHONE_NUMBER_ID`, an env var that **does not exist anywhere else in the codebase** (the real config is `WHATSAPP_API_URL`, which embeds the phone number ID in the URL path). This function is effectively dead/broken and would misreport "not configured" even when WhatsApp alerts are actively working. Confirmed as a genuine bug, not intentional.
- No dependency-injection points: `fetch()`, `send_whatsapp_alert()`, and `conn()` are called as bare module-level functions everywhere, which is why tests resort to `mock.patch.object(app, "fetch", ...)` rather than constructor injection — workable at this size, will not scale to multiple pluggable adapters/providers.

## 12. Scalability bottlenecks

- `sources.json` (currently ~250KB, 662 entries) is read and fully JSON-parsed on nearly every mutating request and on every collection cycle (`sources()`, called from `collect_all()`, `source_summary()`, `validate_watchlist()`, etc.) — fine at hundreds of sources, will not scale to the thousands implied by "all of Nepal: federal ministries, departments, public enterprises, universities, hospitals, schools" from the target spec.
- No index on `notices.source_id` or `notices.discovered_at` — `list_notices()` and `source_summary()`'s per-source `select discovered_at from notices where source_id=?` (401) will degrade from full scans as row count grows past current ~7,000.
- `/notices` has a `limit` but no offset/cursor — cannot page.
- The dashboard loads all 662 sources into the browser on every page load with client-side filtering (`dashboard.html`) — already borderline for a human dashboard, would not survive a 10x source count.
- One `DB_WRITE_LOCK` in one process serializes all writes; correct for the current single-instance deployment, would need rethinking (row-level locking, or moving to a database that handles concurrent writers) before running multiple replicas.
- `linked_notice_date()` lookups are capped/parallelized *per source* (317-323) but there is no cross-source concurrency budget — 40 sources each independently spinning up their own 5-worker pool for notice-page lookups can transiently spike thread count well past `COLLECTOR_WORKERS`.

## 13. Security

Reasonably careful for its scope:
- `validate_source()` (167-187) rejects localhost/`.local`/private IPs for source URLs — real SSRF protection on user-supplied source URLs, verified present and tested (`tests/test_security.py`).
- Secrets (`APP_PASSWORD`, WhatsApp tokens) come from env vars only, never committed (`.env` is gitignored, confirmed no secrets in git history from this session's work).
- HTTP Basic Auth with `secrets.compare_digest` (436) — timing-safe comparison, correct.
- Security headers set on every response (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, CSP on the dashboard route) (422-426, 450).
- `/health` deliberately bypasses auth (428) — necessary for Railway's healthcheck, correctly scoped to only that path.

Gaps:
- Single shared username/password for the whole operator team — no per-user accounts, no audit trail of who changed a source or removed a watchlist.
- No rate limiting anywhere — a leaked password allows unlimited API hammering.
- `load_dotenv()` (23-30) is a hand-rolled `.env` parser with no quoting/escaping support — fine for the current simple values, would silently mis-parse a value containing `=` or wrapped in quotes.
- No CSRF concern in the traditional sense (Basic Auth header, not cookies) but also no origin checking on state-changing requests.
- Document/file handling (downloads, ZIP extraction, OCR) does not exist yet — when it's added (target spec §4, §22), it introduces a new attack surface (zip bombs, path traversal, malicious PDFs) that has zero existing mitigation to build on.

## 14. Reliability

Two significant production incidents were found and fixed earlier in this same session (both visible in git log): a concurrent-write crash that silently killed the entire background scheduler thread while `/health` kept reporting healthy, and a collection-cycle duration blowup (up to ~174 minutes against a 60-minute target) caused by serial, full-retry-budget per-notice-page lookups. Both are now fixed, tested (regression tests added), and verified against ~15+ hours and dozens of cycles of live production behavior. Current reliability posture is solid: per-source failure isolation (`collect_one` never raises), a failure-streak skip/cooldown mechanism, a last-resort scheduler-level try/except, and a `/collection/status` + dashboard surface for cycle health. Remaining reliability gap: the file-backed `sources.json`/`watchlists.json` read-modify-write race described in §11/§4 is real but low-probability at current usage (one operator).

## 15. Parsing weaknesses

- `LinkTextParser` only looks at `<a>` tag text — a listing page whose notices are rendered via JavaScript, or where the meaningful text is in a sibling element rather than inside the anchor, produces zero results with no error signal (the source just looks "healthy but empty" forever).
- Date extraction (`DATE_PATTERNS`, 202-208) is regex-based pattern matching over nearby text, not tied to any DOM/semantic structure — it will happily match an unrelated date elsewhere on the page (e.g. a footer copyright year formatted as a date-like string) if it happens to fall within the ~1.6KB window around the link.
- No BS (Bikram Sambat)/AD conversion at all — Nepali government sites frequently publish dates in BS; `first_date()` has a Nepali-digit pattern (207) but no BS→AD conversion, so a BS date is stored as an opaque string with no way to compute "days until deadline" reliably.
- No handling of PDF/DOC content — if a listing page's "notice" is just a bare link to a PDF with no surrounding text, `title` may be a filename and nothing else is ever extracted.

## 16. Missing tests

Before this session, only 4 tests existed (`tests/test_security.py`, URL validation only). This session added 33 more (37 total, all passing) covering: source-health failure tracking and skip/cooldown, high-concurrency write safety, per-notice-lookup capping and concurrency, and core parsing primitives (`LinkTextParser`, `OfficialDirectoryParser`, `clean`, `first_date`, `published_date`, `relevant`). Still entirely untested:

- `Api` HTTP routing — every endpoint, every verb, auth gating, error responses. No test issues an actual HTTP request against the server.
- `mcp()`/`mcp_response()` — the MCP JSON-RPC loop itself (only the underlying `list_notices`/`details` are exercised, indirectly, via other tests).
- `bootstrap_province()`/`official_directory_sources()`/`sync_all_local_levels()` — the Ministry-directory import path, including `OfficialDirectoryParser`'s row-filtering logic in that specific context (dedup-by-hostname, header-row skipping).
- `validate_source()`/`validate_watchlist()` beyond the security-focused cases already covered.
- `save_json()`'s atomic-write behavior, and the concurrent-write race identified in §4/§11.
- The CLI entry points (`alert()`, `test_whatsapp()`, argv dispatch in `__main__`).
- No fixture-based (saved HTML snapshot) tests exist for any real government site's markup — every current parsing test uses hand-written minimal HTML, not a captured real-world page, so a real site's markup quirks are untested.

## 17. What should NOT be changed yet

- **HTTP response shapes.** `dashboard.html` is tightly coupled to the exact JSON fields returned by `/sources`, `/notices`, `/watchlists`, `/alerts/status`, `/collection/status` — renaming or restructuring these breaks the live dashboard immediately, with no build step to catch it.
- **MCP tool names/schemas** (`search_tenders`, `latest_tenders`, `tender_details`) — an external MCP client may already be configured against these exact names; breaking changes here are invisible until someone's client fails.
- **The `notices` table and its existing ~7,000+ collected rows** — this is real, already-alerted-on production data; any schema change must be additive (new nullable columns) or an explicit, tested migration, never a drop/recreate.
- **WhatsApp alert behavior and the `WHATSAPP_RECIPIENT`/template config** — this sends real messages to a real business phone number today; changing the payload shape or firing conditions without care risks spamming or silently breaking a live business-critical integration.
- **The Railway deployment surface** — `railway.toml`, the `/app/data` volume mount, `HOST=0.0.0.0`, `REQUIRE_AUTH=1`, and all the tuning env vars (`COLLECTOR_WORKERS`, `SOURCE_FAILURE_SKIP_*`, `NOTICE_PAGE_LOOKUP_*`) were specifically calibrated against real production behavior this session — do not reset these to framework defaults during a refactor.
- **The stdlib-only dependency policy**, until a milestone explicitly requires otherwise (document text extraction, AI providers). Introducing a web framework or ORM as part of "just cleaning up the architecture" would be scope creep against the audit's own finding that the current bottleneck is *module organization*, not *technology choice*.
- **`sources.json`/`watchlists.json` file formats** — the dashboard's source-editor form and `validate_source()`/`validate_watchlist()` both assume the current flat-list-of-dicts shape; changing it is a data migration, not a refactor.
