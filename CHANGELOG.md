# Changelog

## Milestone 6 — Tender change detection

- Notices stop being effectively immutable (audit §6/§9): a re-scrape of an already-known notice
  now actually compares its listing entry against what's stored, instead of only bumping
  `last_seen`. Detection is gated on `content_hash` genuinely diverging from the stored value (a
  null→value transition is a first capture, not a change -- never fabricate a change that isn't
  one) and requires the adapter to have handed back the real snippet text this cycle, not just its
  hash (`adapters.py`'s candidate dict gains `context_snippet`, used transiently for one cycle and
  never persisted verbatim -- same no-raw-blob discipline as `documents.py`).
- New `collector._classify_change()` turns a detected change into one of the three types the
  roadmap named, reusing existing, already-trusted primitives rather than inventing new ones:
  `parsing.classify_notice_type`'s cancellation/corrigendum keyword scan (run against the new
  snippet instead of a title) and `documents.extract_submission_deadline`'s keyword-gated date
  extraction (a date only counts as a deadline change when it's found near an explicit
  deadline-indicating keyword *and* differs from a real prior `published_at` -- both conditions
  required, so "first time a date is found" is never misreported as "the date changed"). Anything
  that doesn't match one of the three falls into an unclassified `listing_changed` bucket --
  recorded for the audit trail, never alerted, rather than guessing.
- New `notice_changes` table: an append-only version-history log (`change_type`, `previous_value`,
  `new_value`, `detail`, `detected_at`) rather than full-row snapshots, since in practice only
  `content_hash`/`published_at`/`status` can meaningfully change post-insert today. New
  `GET /notices/{id}/changes` endpoint and `queries.notice_changes()`.
- `TENDER_CANCELLED` also flips the notice's `status` to `cancelled` -- the first time a notice's
  status can transition after insert (previously fixed forever at insert-time from the title
  alone). This automatically drops the notice out of Milestone 5's company-profile matches, since
  `matching.NON_ACTIONABLE_STATUSES` already excludes cancelled/awarded notices.
- All three named change types fire the existing `alerts.send_whatsapp_alert()` call site (the
  same fixed 3-parameter template as a new-notice alert) with the notice's title prefixed
  in-memory (e.g. `[DEADLINE CHANGED] ...`) so the recipient can tell it's a resend about an
  existing tender -- never written back to the stored title. A distinct alert shape per change
  type is real Milestone 7 scope (the `AlertProvider` abstraction); this milestone deliberately
  reuses the one call site that already exists rather than pre-building that abstraction early.
- `deliveries` gains an additive `reason` column (`new_notice`, or a `notice_changes.change_type`)
  so the existing alert history/audit trail can show *why* each alert fired -- null for every row
  that predates this column (never fabricate a reason for history that has none).
- Cross-notice linking (a separately-published "Corrigendum for XYZ Tender" notice referencing an
  original one) is deliberately out of scope this milestone -- reliable fuzzy title/authority
  matching across rows is a real capability, not a byproduct of same-row diffing, and doesn't fit
  this milestone's "pure diff step over what one adapter cycle already fetches" scope.
- Test suite: 138 → 147. New change-detection cases in `tests/test_collection_health.py`
  (first-capture-is-not-a-change, identical-recollect-is-a-no-op, unclassified-edit-recorded-not-
  alerted, cancellation/corrigendum/deadline-changed classification, first-time-date-found-is-not-
  a-deadline-change) plus `GET /notices/{id}/changes` coverage in `tests/test_api.py`.

## Milestone 5 — Company profiles + matching

- New `tender_monitor/matching.py`: `match_tender_to_company(notice, profile)`, pure business logic
  (no I/O) scoring one notice against one company profile across three explainable dimensions --
  category (weight 0.5, scored at the Milestone 4 classifier's own confidence for the best-matching
  category), province (weight 0.2, exact match against the source-stamped province), and keyword
  (weight 0.3, substring match against title/authority). A dimension the profile leaves unset (no
  categories/provinces/keywords configured) is *omitted* from the result, not scored 0 or 1 --
  fabricating a preference the company never stated would be worse than not scoring it, the same
  "never fabricate" rule Milestones 2/3 followed for fields with no honest data source. The overall
  score is the weighted average over only the active dimensions (weights renormalized), so a
  profile with just one dimension configured still produces a meaningful 0..1 score. Deliberately
  no budget/amount dimension yet: `estimated_amount` stays null until Milestone 10, and a dimension
  over an always-null field would be dead code, not a real preference.
- New `company_profiles.json` registry (name, categories, provinces, keywords), same JSON-file/
  `REGISTRY_WRITE_LOCK`/atomic-temp-file-write pattern `storage.py` already uses for
  `sources.json`/`watchlists.json` -- a saved company preference is the same shape of thing as a
  saved watchlist, so it gets the same storage treatment rather than a new database table.
- New `queries.matches_for_company()`: ranks every *actionable* notice (excludes `cancelled`/
  `awarded` -- `matching.NON_ACTIONABLE_STATUSES` -- as a hard filter, not a low score, since a
  "70% match" on an already-awarded tender would mislead) against a profile, highest score first.
  Scores the full notice set in Python rather than in SQL, deliberately: at this pilot's current
  scale (~7,000 notices) a full scan per request measured well under 10ms against the live
  database, and keeping scoring in one Python function keeps it unit-testable independent of SQL.
  Revisit only if volume reaches Milestone 11's PostgreSQL trigger conditions.
- New endpoints, mirroring the existing watchlist CRUD shape: `GET/POST /company-profiles`,
  `PATCH/DELETE /company-profiles/{id}`, and `GET /company-profiles/{id}/matches` (accepts
  `limit`/`offset`/`min_score`; 404s for an unknown profile id, distinct from an empty match list).
- Test suite: 117 → 138. New `tests/test_matching.py` (pure per-dimension logic, no DB) plus a new
  `CompanyProfilesAndMatchingTests` class in `tests/test_api.py` (CRUD, duplicate-name rejection,
  404 on unknown profile, non-actionable-status exclusion, `min_score` filtering).
- No MCP tool changes -- expanding the tool surface is explicitly Milestone 8's scope.

## Milestone 4 — Classification + advanced search

- Rule-based, title-only multi-category classification (`parsing.classify_categories`), same style
  as Milestone 2's `classify_notice_type`: a notice can match several categories (e.g. "CCTV and
  networking equipment"), each with a flat 0.6 confidence ("a keyword matched", nothing more
  precise is honestly knowable from title text alone); unmatched titles get `("Other", 0.5)` so
  every notice has at least one category. New `notice_categories` table (notice_id, category,
  confidence_score) rather than a column, since a notice can carry more than one.
- Added real indexes on `notices.source_id`/`notices.discovered_at` and
  `notice_categories.category` (audit §12 flagged these as unindexed).
- `queries.list_notices`/`GET /notices` upgraded with filters (`province`, `notice_type`,
  `status`, `category`, `has_documents`) and `offset`-based pagination. Deliberately no
  `published_after`/`published_before`: `published_at` is free-text extracted from source pages in
  varying formats (not a normalized comparable value), so a `>=`/`<=` string comparison on it
  would silently misorder results. Added `discovered_after`/`discovered_before` instead, filtering
  on the real ISO timestamp this process itself sets.
- `GET /notices/{id}` now includes the notice's `categories`.
- Existing rows backfilled via the same one-time-per-process pattern as Milestone 2, guarded so the
  `count(*)` check that decides whether backfill is needed runs at most once per process lifetime,
  not on every `conn()` call (conn() is called extremely often).
- Test suite: 113 → 117.

## Milestone 3 — Document intelligence

- First new dependency of the project: `pypdf==6.16.1` (pure Python, no system libraries --
  installs cleanly on Railway's Nixpacks build with zero extra config). OCR was deliberately not
  added: no evidence yet of a scanned-document rate that would justify the added system
  dependency (tesseract); a PDF that yields no text is recorded as `empty_text_likely_scanned`
  rather than guessed at.
- New `tender_monitor/documents.py`: discovers `.pdf` links, downloads them SSRF-checked (shared
  guard in `net.is_safe_public_url`), size-capped (streamed, aborts mid-download rather than
  reading an oversized body into memory first), and magic-byte-verified (`%PDF-`) before ever
  handing bytes to the parser. Never raises -- every outcome, including failure, is a typed
  `extraction_status`. No raw PDF bytes are persisted, only extracted text (capped) + metadata.
- New `documents` table (additive migration, same `SCHEMA_MIGRATION_LOCK` pattern as Milestone 2)
  and a `submission_deadline` column on `notices`, populated only when a date appears near an
  explicit deadline keyword in extracted text -- not just any date in the document (never
  fabricate). Deliberately NOT extracting `estimated_amount`/`bid_security_amount`/`eligibility`
  this milestone: a document typically states several monetary figures, and attributing the right
  one via regex without real NLP is unreliable enough that a wrong-but-confident number would be
  worse than none. Deferred to Milestone 10 (AI) or a dedicated pass.
- Wired into `collector.collect_one`: document discovery only runs for genuinely new notices
  (never re-processes a notice's documents on a later re-encounter), gated by
  `DOCUMENT_PROCESSING_ENABLED` (off by default) and bounded by `DOCUMENT_DOWNLOAD_LIMIT`/
  `_WORKERS`/`_MAX_SIZE_BYTES`/`_TIMEOUT_SECONDS` so a burst of new notices on one source can't
  blow up that cycle's duration -- the ~9-minute cycle time from Milestone 1's fixes was hard-won
  and this milestone is designed not to spend it without an explicit opt-in.
- New `GET /notices/{id}/documents` endpoint.
- Test suite: 99 → 113. New `tests/test_documents.py` (SSRF rejection, size-cap rejection,
  magic-byte rejection, corrupt-PDF handling -- none touch a real network, all mock
  `urllib.request.urlopen` directly) plus collector-level and migration-level coverage of the
  new wiring.
- **Shipped with `DOCUMENT_PROCESSING_ENABLED=0`** (the .env.example/README default) even though
  the code is deployed: a new dependency, a schema migration, and a new download pipeline landing
  together is enough compounded risk for one deploy. Turning it on live (real PDF downloads across
  every configured source) is a deliberate, separate follow-up via a Railway variable, not
  something this milestone unilaterally enables.

## Milestone 2 — Normalized tender schema + adapter interface

- Added `tender_monitor/adapters.py`: a `BaseTenderSource` ABC (`discover_notices`,
  `health_check`) and `GenericHtmlLinkAdapter`, the single implementation today, holding exactly
  the scrape/filter/date-extraction logic that used to live inline in `collector.collect_one`.
  Milestone 3+ adapters can now be added without touching collection orchestration (concurrency,
  locking, writes, alerting).
- `notices` gains 9 additive columns, populated without needing document intelligence:
  `organization` (mirrors `authority` for now), `province` (stamped from the source's registered
  province), `district` (column exists, always null -- no honest data source yet), `notice_type`
  and `status` (rule-based keyword classification: cancellation/award/corrigendum/tender_notice),
  `first_seen`/`last_seen` (the first real signal toward change detection -- `last_seen` advances
  every cycle a notice is still listed), `content_hash` (hash of the listing-page text near the
  link, independent of the notice's identity digest so it can later detect "this entry changed"),
  `confidence_score` (0.9/0.7/0.5 by whether a date was found on the listing page, via a
  per-notice-page lookup, or not at all).
- Migration is additive `ALTER TABLE` + a one-time backfill for existing rows, guarded by a
  dedicated `SCHEMA_MIGRATION_LOCK` (deliberately separate from `DB_WRITE_LOCK` -- collector.py
  calls `conn()` from inside a `DB_WRITE_LOCK`-held block, and `threading.Lock` isn't reentrant, so
  reusing that lock here would deadlock) with double-checked locking so concurrent collector
  threads racing to migrate right after a fresh deploy can't hit "duplicate column" errors.
  `district`/`content_hash` are never backfilled for pre-migration rows -- there's no honest way to
  derive them retroactively, so they stay null rather than being fabricated.
- **Closed the sources.json/watchlists.json read-modify-write race** flagged in
  `ARCHITECTURE_AUDIT.md` §4/§11: a `storage.REGISTRY_WRITE_LOCK` now spans every mutation's full
  read-modify-write cycle (api.py's source/watchlist POST/PATCH/DELETE, discovery.py's
  bootstrap-*). Reproducing the unlocked race directly (30 concurrent "add a source" calls) showed
  it was worse than the audit described: 25 of 30 writes were silently lost, and several calls
  crashed outright, because `save_json`'s temp file used a single fixed name shared by every
  caller. Hardened `save_json` itself to use a (process, thread)-unique temp filename as defense
  in depth, independent of the lock.
- Test suite: 73 → 99. New: `tests/test_registry_concurrency.py` (reproduces the write race
  directly against `storage.sources()`/`save_sources()`) and `tests/test_migration.py`, which
  seeds a database in the *exact* pre-Milestone-2 schema (matching real production row shape) and
  verifies the migration end to end -- not a fresh empty database, which would never exercise the
  backfill path.
- Verified against a real (if stale, local-only, gitignored) 505-row dataset in addition to the
  synthetic migration tests: 0 nulls across organization/province/notice_type/status/first_seen/
  last_seen after migration, 505/505 correctly-null for district/content_hash/confidence_score.
- Full test suite green, local smoke test (isolated data dir, `serve` + add-a-source flow) clean,
  deployed and verified against live production data.

## Milestone 1 — Architecture cleanup + regression tests

- Split the 606-line `app.py` monolith into a `tender_monitor/` package with one module per
  responsibility (config, parsing, net, storage, health, discovery, alerts, collector, queries,
  api, scheduler, mcp_server, cli). `app.py` is now a thin shim that delegates to
  `tender_monitor.cli.main()`; every CLI invocation (`serve`, `collect`, `mcp`, `alert`,
  `test-whatsapp`, `bootstrap-*`, `sync-all-local-levels`) is byte-for-byte unchanged.
- Zero behavior change and zero new dependencies (still stdlib-only). Modules call each other by
  module reference (e.g. `net.fetch(...)`, not `from .net import fetch`) specifically so tests can
  patch a single, precise location, the same way the original single-file tests could patch
  `app.fetch` directly.
- Added `ARCHITECTURE_AUDIT.md` (full pipeline/storage/security/reliability audit, evidence-based
  against the pre-refactor code) and `ROADMAP.md` (12 milestones, adapted from the target platform
  spec to this codebase's actual constraints).
- Test suite grew from 37 to 73 tests. New coverage for three previously entirely-untested paths
  (audit §16): the HTTP API (`tests/test_api.py`, boots a real `ThreadingHTTPServer` against an
  isolated data dir — routing, auth gating, all CRUD endpoints), the MCP JSON-RPC loop
  (`tests/test_mcp.py`), and the Ministry-directory bootstrap/import path
  (`tests/test_discovery.py`, synthetic fixture, no live-site dependency).
- **Fixed a real risk found while writing the new tests**: `tender_monitor.config.load_dotenv()`
  loads this repo's real `.env` — including live WhatsApp Business credentials — into the process
  environment on import. Several tests exercise the real `collect_one` success path and insert a
  genuinely new notice, which fires an alert send. Without explicitly clearing those four env vars
  in test setup, that send is not a mock — it's a real HTTPS call to Meta's Graph API capable of
  delivering a real WhatsApp message with fabricated test content to the real configured recipient.
  All test files that reach this path now clear `WHATSAPP_API_URL`/`WHATSAPP_ACCESS_TOKEN`/
  `WHATSAPP_RECIPIENT`/`WHATSAPP_TEMPLATE_NAME` in setUp/tearDown, so `send_whatsapp_alert` takes
  its own designed "not configured" no-op path instead.
- Verified: full test suite green, a local `python3 app.py serve`/`collect`/`mcp`/`alert` smoke
  test against an isolated empty-sources data dir succeeds with no errors, deployed to Railway and
  confirmed live (`/health`, `/collection/status`, a full collection cycle).

## Earlier, pre-Milestone-1 production fixes (same day, before this refactor)

- Fixed a production incident where raising `COLLECTOR_WORKERS` caused concurrent SQLite writers to
  hit "database is locked", which escaped `collect_one` uncaught and silently killed the entire
  background collection thread while `/health` kept reporting healthy. Added a process-wide write
  lock and made `collect_one` exception-proof.
- Fixed collection cycles taking 62–174 minutes (against a 60-minute target) by bounding, capping,
  and parallelizing the per-notice supplementary date-page lookups that were previously serial with
  a full network-retry budget each. Cycles now complete in ~8–9 minutes.
- Added per-source failure tracking with a skip/cooldown mechanism, a `/collection/status` endpoint
  and dashboard surface for whole-cycle health, and removed a dead unreachable code path in the
  dashboard route handler.
