# Changelog

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
