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

To use it as an MCP server, configure your MCP client to launch `python3 /absolute/path/to/app.py mcp`. It provides `search_tenders`, `latest_tenders`, and `tender_details`.

## Alerts

Copy `.env.example` to `.env`, then set `WHATSAPP_API_URL`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_RECIPIENT`, and `WHATSAPP_TEMPLATE_NAME`. Each newly collected tender will then send a WhatsApp template alert automatically. The approved template must have three body variables: local government, notice title, and notice link. Production WhatsApp alerts require recipient opt-in.

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
