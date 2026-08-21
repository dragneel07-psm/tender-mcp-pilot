# Changelog

## Milestone 12 — Security, observability, production hardening

Scoped against what actually shipped in Milestones 1-11 (per the roadmap's own instruction),
rather than the pre-Milestone-1 audit's gap list verbatim -- several of its Security (§13) items
no longer apply (e.g. the ZIP-extraction attack surface it warned about was never built; Milestone
3 stayed PDF-only) or were already fixed in passing (`load_dotenv()` already split on the first
`=` correctly, contrary to the audit's specific claim).

- **Rate limiting** (closes audit §13's "no rate limiting anywhere -- a leaked password allows
  unlimited API hammering"): new `tender_monitor/ratelimit.py`, an in-process fixed-window counter
  per client IP. Checked in `Api.rate_limited()` before `require_auth()` on every method handler,
  so hammering is throttled regardless of whether the request would have authenticated. `/health`
  is exempt (Railway's own healthcheck must never be capable of tripping this). In-process only,
  deliberately -- this app runs as a single Railway replica (the Milestone 11 decision above), so
  there's no need for shared state across instances, the same reasoning that already justified
  `DB_WRITE_LOCK` being in-process. `RATE_LIMIT_REQUESTS=0` disables it entirely.
- **`.env` parser hardening**: a value wrapped in matching quotes (`FOO="bar"`) now has the quotes
  stripped -- some providers' dashboards paste API keys pre-quoted, and Milestone 10 just added
  the first API key (`ANTHROPIC_API_KEY`) this project has ever needed to handle. Splitting on the
  first `=` was already correct (verified, not just assumed) contrary to the audit's claim; still
  no backslash-escape or multi-line-value support, documented as a real limitation rather than
  silently left unaddressed.
- **Per-cycle log correlation IDs** (target spec §21): `scheduler.py` generates a short id per
  collection cycle, printed on every log line that cycle emits and exposed as `cycle_id` on
  `GET /collection/status`, so a dashboard-visible failure can be matched back to its exact
  Railway log lines without timestamp-window guessing.
- **Per-user accounts**: explicitly scoped out, not silently skipped. The roadmap's own condition
  for this work ("if multi-operator use has materialized by this point") isn't met -- this remains
  a single-operator pilot behind one shared `APP_USERNAME`/`APP_PASSWORD`, and nothing shipped in
  Milestones 1-11 introduced a multi-user concept to build accounts against.
- The "metrics list from target spec §21" item is **not implemented**: that spec section's exact
  contents aren't available in this session, and inventing a plausible-sounding metrics list to
  claim the item as done would be exactly the kind of fabrication engineering rule #2 forbids.
  `GET /collection/status` (existing) plus the new `cycle_id` are what shipped instead.
- Test suite: 213 → 230. New `tests/test_ratelimit.py` (window/limit/reset/disable/per-IP
  isolation/bounded-memory) and `tests/test_config.py` (quote-stripping, `=`-in-value
  preservation, malformed-quote fallback, no-override-of-real-env-vars), plus rate-limit-specific
  API coverage in `tests/test_api.py` (429 + `Retry-After`, `/health` exemption) -- rate limiting
  is disabled by default across every *other* test in that file (shared 127.0.0.1 client IP across
  many requests within one test run would otherwise make unrelated tests flaky).

## Milestone 11 — PostgreSQL/queue migration decision: not yet

Per target spec §24 and the "do not migrate for fashion" engineering rule, this milestone's
deliverable is a documented decision against real production evidence, not code -- the roadmap
itself only calls for a migration if a trigger condition is actually met. Evidence gathered
directly from the live Railway deployment (`railway ssh` into the running container, not a local
dev copy) on 2026-08-21:

- **6,945** rows in `notices`, **40** in `documents`, **30,290** in `runs`, **7,027** in
  `deliveries` -- `tenders.db` is **16 MB** on disk.
- **One Railway replica.** No `numReplicas`/scaling configuration exists in `railway.toml`, and
  nothing in this project's growth (roughly linear with the ~662-source registry, not exponential)
  suggests horizontal scaling is imminent.
- No reports of `DB_WRITE_LOCK` contention since Milestone 1's fix (the write-serialization lock
  introduced specifically because raising `COLLECTOR_WORKERS` once caused "database is locked"
  errors) -- that fix has held for the ~10 milestones since, across a full `COLLECTOR_WORKERS=40`
  production configuration.
- Milestone 4 already added the indexes (`notices.source_id`, `notices.discovered_at`,
  `notice_categories.category`) that were the actual bottleneck risk at this scale, not SQLite's
  storage engine itself.

**Neither trigger condition in the roadmap is met**: no multi-replica requirement, and row count
nowhere near where SQLite's single-file model becomes the bottleneck (SQLite comfortably handles
databases orders of magnitude larger than 16 MB / ~7,000 rows). **Decision: not yet.** Revisit
this decision if either condition changes -- concretely, if Railway replicas become necessary
(this app currently can't run multi-replica anyway, since `DB_WRITE_LOCK` is an in-process lock
that provides no cross-process/cross-replica coordination) or if `notices` growth trajectory
starts pointing toward tens of millions of rows. No code changed this milestone.

## Milestone 10 — AI intelligence

- New `tender_monitor/ai.py`: `AIProvider` ABC (`is_configured`/`extract`) and `AnthropicProvider`,
  the only implementation today -- built on plain `urllib` (same choice `alerts.py` already made
  for WhatsApp's Graph API), so this milestone adds **zero new dependencies** despite being the
  first one the roadmap flagged as potentially needing one. Extracts the three fields Milestone
  3's regex-based document intelligence deliberately left null: `estimated_amount`,
  `bid_security_amount`, `eligibility_summary` -- a tender document routinely states several
  monetary figures, and attributing the right one without real language understanding is
  unreliable enough that a wrong-but-confident number is worse than none. The extraction prompt
  explicitly instructs the model to return `null` for anything not directly stated in the text --
  never guess, infer, or estimate (the same "never fabricate" discipline
  `documents.extract_submission_deadline`'s keyword-gating already applies to the non-AI case).
- **Provenance-tagged** (target spec §29): the new `estimated_amount`/`bid_security_amount`/
  `eligibility_summary`/`ai_provider`/`ai_extraction_status`/`ai_extracted_at` columns have no
  other writer anywhere in this codebase, so their mere presence unambiguously means an AI model
  produced them -- never confused with a source-derived or rule-based field, and the content
  fields are coalesce-only in `collector.py` (never overwrite an already-set value with a new AI
  guess).
- **Strictly optional** (engineering rule #13), same "skipped" pattern `alerts.py` already uses
  for unconfigured WhatsApp: collection, search, and alerts all keep working with zero AI
  configured. Gated behind two independent switches -- `AI_EXTRACTION_ENABLED=0` by default, and
  only reachable at all when `DOCUMENT_PROCESSING_ENABLED=1` (it works from a document's
  already-extracted text, so it inherits that flag's own off-by-default posture) -- plus
  `AI_EXTRACTION_LIMIT` bounding how many real, paid LLM calls run per source per cycle, since this
  is real per-call cost unlike the free document-download step it depends on. Runs outside
  `DB_WRITE_LOCK` (network calls), each notice's result written back under its own short lock, same
  pattern the alert-sending loop already uses.
- **Shipped fully inert**: no `AI_PROVIDER`/`ANTHROPIC_API_KEY` is configured in production, so
  this milestone has zero live effect until an operator deliberately opts in -- same discipline
  Milestone 3 used for `DOCUMENT_PROCESSING_ENABLED`.
- `dashboard.html` gained a purely additive display: when a notice has any AI-derived field, its
  card shows an "AI" tag with the extracted amount(s)/eligibility summary; renders nothing
  (unchanged from Milestone 9) for the vast majority of notices where these stay null.
- Test suite: 192 → 213. New `tests/test_ai.py` (provider dispatch, null-preservation, malformed/
  non-object JSON, network/HTTP errors, field-length truncation, provider selection) plus
  collection-pipeline wiring tests in `tests/test_collection_health.py` (off-by-default, no-
  provider skip, successful write, failure-status recording, zero-limit skip) and a migration test
  confirming the six new columns are never backfilled.

## Milestone 9 — Dashboard 2.0

- `dashboard.html`'s notice feed now uses the server's real pagination/filters
  (`GET /notices?query=&category=&province=&notice_type=&status=&unread=&limit=&offset=`) instead
  of fetching up to 100 rows and filtering them client-side (audit §10's "fetch all 662 sources,
  all 100 notices, client-side filter" complaint, finally addressed on the notices side now that
  the API has real filters/pagination from Milestones 4 and 7). New category/province/notice-type/
  status filter dropdowns (debounced text search unchanged) and a "Load more" button replacing the
  fixed 100-row cap. Metric tiles that summarize the *loaded* batch are relabeled ("Loaded
  notices", "Unread in view", "New in 48h (view)") rather than implying they're totals -- with
  pagination now capping the page at 30 by default, presenting a partial count as a total would be
  the same class of dishonesty the "never fabricate" rule already forbids for data fields.
- New backend `unread` filter on `queries.list_notices()`/`GET /notices` (tri-state, same pattern
  as `has_documents`) so the dashboard's "Unread only" toggle filters server-side too, not just
  within whatever page happened to be loaded.
- Watchlist creation gained category/province/notice-type/status selects alongside the existing
  source checkboxes (Milestone 7's saved-search fields, now actually reachable from the UI).
  Selecting a watchlist now calls `GET /watchlists/{id}/notices` instead of client-side filtering
  by `source_ids` -- a watchlist with no sources checked (pure category/province/etc. search) is
  now correctly treated as "every source", not "zero sources" (the old client-side filter would
  have hidden everything for an unscoped watchlist).
- Notices now show status-derived badges (Cancelled, Awarded, Corrigendum) directly from fields
  already in the row -- no extra request. A new "History" button per notice lazily fetches
  `GET /notices/{id}/changes` on first click and caches it client-side, so Milestone 6's change
  detection has its first dashboard surface.
- Fixed a real CSS bug caught during manual verification: `.load-more{display:flex}` overrode the
  `hidden` attribute's implicit `display:none` (same class of bug the existing `.manager[hidden]`
  rule was already guarding against elsewhere in this file) -- the "Load more" button was visible
  even with nothing left to load until an explicit `.load-more[hidden]{display:none}` rule was
  added.
- Visual style intentionally unchanged (same CSS variables, layout, component look) -- this
  milestone is a functional rewrite of the data-fetching model, not a redesign.
- Backend test suite: 190 → 192 (`unread` filter coverage in `tests/test_collection_health.py` and
  `tests/test_api.py`). `dashboard.html` itself has no automated test harness (matches the rest of
  this pilot's approach to the dashboard); verified manually end-to-end in a real browser against
  a seeded isolated dataset -- new-notice badges, category/province filtering, watchlist
  save/view, per-notice History panels, and the mark-read/unread-only interaction.

## Milestone 8 — MCP 2.0

- Expanded from 3 MCP tools to 11, now that source health, collection status, matching,
  watchlists, and change detection all exist as real backing functions (Milestones 2-7) rather
  than needing to be stubbed: `search_tenders`, `latest_tenders`, `tender_details` (existing,
  upgraded), plus new `tender_documents`, `tender_changes`, `source_health`, `collection_status`,
  `list_watchlists`, `watchlist_notices`, `list_company_profiles`, `match_tenders_to_company`.
  Every tool is a thin wrapper over an existing `queries`/`storage` function -- no new business
  logic, same "MCP tools are read-models over what the HTTP API already exposes" shape as before.
- Every list-shaped tool now takes `limit`/`offset`, delegated to the same clamping (1-100)
  `list_notices()`/`matches_for_company()` already apply -- no new pagination logic invented in
  `mcp_server.py` itself (audit §8).
- Every tool now returns a structured `{"error": {"code": ..., "message": ...}}` object on
  failure (`invalid_argument`, `not_found`, `unknown_tool`, `internal_error`) instead of a bare
  string, so a client can branch on `code` rather than string-matching a message (audit §8). A
  handler exception is caught per-call and turned into an `internal_error` result rather than
  crashing the stdio loop -- same per-call isolation principle `collect_one` already applies
  per-source.
- Test suite: 177 → 190. `tests/test_mcp.py` covers every new tool's happy path plus its
  not-found/invalid-argument/internal-error paths.

## Milestone 7 — Advanced watchlists + alert engine

- **AlertProvider abstraction** (`alerts.py` rewrite): an `AlertProvider` ABC (`is_configured`,
  `send(notice, reason)`) and `WhatsAppAlertProvider`, the only implementation today -- exactly
  today's single approved 3-parameter template, now behind an interface. `alerts.send_alert(notice,
  reason)` is the one call site collector.py and reminders.py use; neither knows WhatsApp's
  template shape or field count. A `reason` other than `"new_notice"` (a notice_changes.change_type,
  or `"deadline_reminder"`) is folded into the outbound title as a bracketed prefix inside the
  provider -- collector.py no longer builds that prefix itself, and the notices row is never
  mutated. `alerts.send_whatsapp_alert()` is retired; `cli.py`'s `test-whatsapp` command now
  exercises `WhatsAppAlertProvider` directly (it's deliberately testing the concrete provider, not
  the abstraction).
- **Watchlists upgraded to full saved-search objects**: `validate_watchlist()` now accepts every
  `list_notices()` filter (`query`, `province`, `notice_type`, `status`, `category`,
  `discovered_after`, `discovered_before`, `has_documents`) alongside `source_ids`, not just a list
  of source IDs. New `queries.notices_for_watchlist()` and `GET /watchlists/{id}/notices` run a
  watchlist's saved filters through `list_notices()` (which gained a `source_ids` param -- an IN
  clause, additive at the end of its signature so no existing positional caller shifts). The one
  checked-in watchlist predating this milestone keeps working untouched: every reader defaults a
  missing filter key to "no filter" rather than requiring a migration.
- **Deadline-reminder scheduling** (new `reminders.py`): sends exactly one `deadline_reminder`
  alert per notice whose `submission_deadline` falls within `DEADLINE_REMINDER_DAYS` (default 3)
  days, excluding cancelled/awarded notices and any notice already reminded (checked against
  `deliveries.reason`, no new table needed). Wired into `scheduler.py`'s existing collection loop,
  isolated in its own try/except so a reminder-pass bug can't mark an otherwise-successful cycle
  "crashed". New `parsing.to_calendar_date()` turns one of `first_date()`'s extracted strings into
  a real `date` for the arithmetic this needs -- deliberately conservative: only unambiguous
  numeric/month-name formats are attempted (never the Devanagari-digit pattern), and a parsed date
  is discarded unless it falls in a plausible window around today. This guards against Nepali
  government sites' routine use of the Bikram Sambat calendar (~56-57 years ahead of Gregorian,
  with nothing in the source text marking which calendar a date uses) -- a BS date parsed as
  Gregorian would land decades off and silently produce a nonsensical reminder schedule; the
  plausibility bound is what catches that instead of trusting arithmetic it can't justify.
  **Has no observable effect in production today**: `submission_deadline` is only ever populated
  when `DOCUMENT_PROCESSING_ENABLED=1` (Milestone 3), which remains off by default and hasn't been
  turned on live -- this ships inert until that prerequisite is enabled, same pattern Milestone 3
  itself used.
- Test suite: 147 → 177. New `tests/test_alerts.py` (provider dispatch, reason-prefix behavior,
  unconfigured no-op path), `tests/test_reminders.py` (due/not-due edge cases, no-repeat sends),
  and new `ToCalendarDateTests` in `tests/test_parsing.py` (all supported formats, garbage input,
  Devanagari rejection, implausible-date rejection) plus new watchlist coverage in
  `tests/test_api.py`.

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
