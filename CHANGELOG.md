# Changelog

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
