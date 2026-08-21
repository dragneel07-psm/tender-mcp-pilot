# Sudurpashchim Tender Monitor

A private, dependency-free prototype that monitors tender pages from local governments, stores notices locally, exposes a small HTTP API, and implements a standard-input/standard-output MCP interface for an AI client.

## Architecture

`app.py` is a thin entrypoint shim; all logic lives in the `tender_monitor/` package (config, parsing, net, storage, health, discovery, alerts, collector, queries, api, scheduler, mcp_server, cli — one module per responsibility). See `ARCHITECTURE_AUDIT.md` for a full breakdown of the pipeline, storage model, and known gaps, and `ROADMAP.md` for where this is headed next.

## Run it

```bash
cd outputs/tender-mcp-pilot
python3 app.py collect
python3 app.py serve
```

To add every Sudurpashchim local-government source from the official Ministry directory, run this once before starting the server:

```bash
python3 app.py bootstrap-sudurpashchim
```

The importer preserves the sources already configured. It can take a few minutes because it checks the official directory pages.

Starting the server now runs an automatic collection immediately and then every 60 minutes. Set `AUTO_COLLECT_INTERVAL_MINUTES` before starting it to change that schedule. Keep the server running; for a reliable always-on schedule, host it on a cloud server.

For the Dhangadhi-only pilot, use `python3 app.py collect dhangadhi`.

The collector checks up to eight sources in parallel. Slow websites are retried once, then retried during the next scheduled collection cycle. You can adjust the timeout, retry, and worker settings in the private `.env` file.

The dashboard starts at `http://127.0.0.1:8787`. The JSON API remains available at `/health`, `/notices`, and `/notices?query=road`.

To use it as an MCP server, configure your MCP client to launch `python3 /absolute/path/to/app.py mcp`. It provides 11 tools covering search (`search_tenders`, `latest_tenders`, `tender_details`, `tender_documents`, `tender_changes`), operations (`source_health`, `collection_status`), watchlists (`list_watchlists`, `watchlist_notices`), and company matching (`list_company_profiles`, `match_tenders_to_company`). Every list-shaped tool supports `limit`/`offset` pagination, and every tool returns a structured `{"error": {"code": ..., "message": ...}}` object on failure rather than a bare string.

## Company profiles & matching

`POST /company-profiles` saves a company's matching preferences (`categories`, `provinces`,
`keywords` -- at least one required). `GET /company-profiles/{id}/matches` scores every open
notice against that profile and returns them ranked, each with a `match_score` (0-1) and
`match_dimensions` explaining exactly which categories/province/keywords drove the score.
Cancelled/awarded notices are excluded, and a dimension the profile didn't configure is left out
of the score entirely rather than guessed at.

## Change detection

Re-scraping an already-known notice now compares it against what's stored instead of only
recording that it's still listed. A genuine change gets classified as `TENDER_CANCELLED`,
`DEADLINE_CHANGED`, or `CORRIGENDUM` (each fires the same WhatsApp alert as a new notice, title
prefixed to signal it's an update) or the unclassified `listing_changed` (recorded, not alerted).
`GET /notices/{id}/changes` returns a notice's full version history.

## Alerts

Copy `.env.example` to `.env`, then set `WHATSAPP_API_URL`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_RECIPIENT`, and `WHATSAPP_TEMPLATE_NAME`. Each newly collected tender will then send a WhatsApp template alert automatically. The approved template must have three body variables: local government, notice title, and notice link. Production WhatsApp alerts require recipient opt-in.

Alert delivery goes through `alerts.AlertProvider` (`WhatsAppAlertProvider` today), so change-detection and deadline-reminder alerts reuse the same template with a bracketed reason prefix (e.g. `[DEADLINE CHANGED] ...`) rather than needing their own delivery code.

## Advanced watchlists

A watchlist is a full saved search, not just a list of sources: `POST /watchlists` accepts `source_ids` plus any `/notices` filter (`query`, `province`, `notice_type`, `status`, `category`, `discovered_after`, `discovered_before`, `has_documents`). `GET /watchlists/{id}/notices` re-runs those saved filters and returns the current matches.

## Deadline reminders

When a notice has a known `submission_deadline` (Milestone 3, document intelligence) within `DEADLINE_REMINDER_DAYS` (default 3) days, the scheduler sends exactly one reminder alert for it per collection cycle pass. Has no effect while `DOCUMENT_PROCESSING_ENABLED=0` (the default), since `submission_deadline` stays null until that's turned on.

## Dashboard

`dashboard.html` is a single static file (no build step) that now drives its notice feed entirely off the server's own pagination/filters (`GET /notices?...`) instead of fetching a capped batch and filtering it client-side. It supports category/province/notice-type/status/unread filters, a "Load more" button, per-notice change-history panels (lazy-loaded from `GET /notices/{id}/changes`), and status-derived badges (Cancelled/Awarded/Corrigendum). Selecting a watchlist switches the feed to `GET /watchlists/{id}/notices`, so a saved search behaves identically whether it's viewed from the dashboard, the API, or an MCP client.

## AI-assisted extraction

Off by default (`AI_EXTRACTION_ENABLED=0`), and only reachable when `DOCUMENT_PROCESSING_ENABLED=1` (it works from a document's already-extracted text). When on, and an AI provider is configured (`AI_PROVIDER=anthropic` + `ANTHROPIC_API_KEY`), each genuinely new notice with usable document text gets one bounded LLM call (`AI_EXTRACTION_LIMIT` per source per cycle) to extract `estimated_amount`, `bid_security_amount`, and `eligibility_summary` -- the fields Milestone 3's regex-based extraction deliberately left null because attributing the right monetary figure without real language understanding is unreliable. These fields have no other writer anywhere in the codebase, so their presence is itself the provenance tag: never confused with a source-derived or rule-based value, and never overwritten once set. `tender_monitor/ai.py`'s `AIProvider` interface is provider-independent; `AnthropicProvider` (plain `urllib`, no new dependency) is the only implementation today.

## Cloud deployment: Railway

The Railway service runs the collector, database, source registry, dashboard, and WhatsApp delivery — there is no separate frontend host.

1. Push this folder to a **private** GitHub repository. Never commit `.env`.
2. In Railway, deploy the repository, add a Volume mounted at `/app/data`, and set the start command to `python3 app.py serve`. `railway.toml` contains the same deployment settings.
3. Set Railway Variables:

   ```text
   HOST=0.0.0.0
   DATA_DIR=/app/data
   AUTO_COLLECT_INTERVAL_MINUTES=60
   COLLECTOR_WORKERS=40
   SOURCE_FAILURE_SKIP_THRESHOLD=5
   SOURCE_FAILURE_SKIP_COOLDOWN_MINUTES=360
   NOTICE_PAGE_LOOKUP_LIMIT=15
   NOTICE_PAGE_LOOKUP_WORKERS=5
   NOTICE_PAGE_LOOKUP_TIMEOUT_SECONDS=15
   REQUIRE_AUTH=1
   APP_USERNAME=your-company-login-name
   APP_PASSWORD=a-long-unique-password
   WHATSAPP_API_URL=...
   WHATSAPP_ACCESS_TOKEN=...
   WHATSAPP_RECIPIENT=...
   WHATSAPP_TEMPLATE_NAME=tender_alert
   WHATSAPP_TEMPLATE_LANGUAGE=en_US
   ```

   With a large source list, raise `COLLECTOR_WORKERS` well above the default — it's I/O-bound
   (each worker waits on a different government site's socket), not CPU-bound, so a low worker
   count is the main reason a full collection sweep runs far longer than
   `AUTO_COLLECT_INTERVAL_MINUTES`. Sources that fail `SOURCE_FAILURE_SKIP_THRESHOLD` times in a
   row are skipped on later scheduled sweeps until `SOURCE_FAILURE_SKIP_COOLDOWN_MINUTES` has
   passed, so chronically dead sites stop slowing down every cycle. The dashboard's "Sources
   failing" tile and each source's fetch-failure badge surface this without needing log access.

   When a source's listing page doesn't show a notice's date, the collector checks that notice's
   own page for one. `NOTICE_PAGE_LOOKUP_LIMIT` caps how many such lookups one source attempts per
   cycle, `NOTICE_PAGE_LOOKUP_WORKERS` runs that many at once, and `NOTICE_PAGE_LOOKUP_TIMEOUT_SECONDS`
   bounds each one with no retries — without these, a single source with many undated notices (or a
   few slow/dead notice pages) could serially stall an entire collection cycle by tens of minutes.
   A notice that misses its lookup this cycle just keeps its "collected" date instead of a
   "published" date; it isn't lost.

4. Open the Railway service's public domain. The browser requests the configured company username and password before it can read or change monitoring data.

The first Railway start copies the repository's source registry to its Volume and creates a new local database. It then starts collecting notices automatically. The Railway `/health` endpoint is intentionally unauthenticated for the provider's health check; all dashboard and API data require the company credentials.

## Important pilot limitation

Each government website can use a different design and may change without notice. The collector intentionally records fetch failures and has a separate source registry so each site adapter can be improved independently. It currently uses the official notice pages where known and a generic official-site discovery page for the remaining sources.
