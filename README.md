# Sudurpashchim Tender Monitor

A private, dependency-free prototype that monitors tender pages from the four agreed Kailali-area local governments, stores notices locally, exposes a small HTTP API, and implements a standard-input/standard-output MCP interface for an AI client.

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

## Cloud deployment: Railway backend and Vercel dashboard

The Railway service runs the collector, database, source registry, and WhatsApp delivery. Vercel serves the dashboard and securely proxies its API requests to Railway.

1. Push this folder to a **private** GitHub repository. Never commit `.env`.
2. In Railway, deploy the repository, add a Volume mounted at `/app/data`, and set the start command to `python3 app.py serve`. `railway.toml` contains the same deployment settings.
3. Set Railway Variables:

   ```text
   HOST=0.0.0.0
   DATA_DIR=/app/data
   AUTO_COLLECT_INTERVAL_MINUTES=60
   REQUIRE_AUTH=1
   APP_USERNAME=your-company-login-name
   APP_PASSWORD=a-long-unique-password
   WHATSAPP_API_URL=...
   WHATSAPP_ACCESS_TOKEN=...
   WHATSAPP_RECIPIENT=...
   WHATSAPP_TEMPLATE_NAME=tender_alert
   WHATSAPP_TEMPLATE_LANGUAGE=en_US
   ```

4. Deploy the same repository in Vercel. Set `RAILWAY_API_URL` to your Railway public service URL, for example `https://your-service.up.railway.app`. `vercel.json` routes dashboard API requests through Vercel to Railway.
5. Open the Vercel URL. The browser requests the configured company username and password before it can read or change monitoring data.

The first Railway start copies the repository's source registry to its Volume and creates a new local database. It then starts collecting notices automatically. The Railway `/health` endpoint is intentionally unauthenticated for the provider's health check; all dashboard and API data require the company credentials.

## Important pilot limitation

Each government website can use a different design and may change without notice. The collector intentionally records fetch failures and has a separate source registry so each site adapter can be improved independently. It currently uses the official notice pages where known and a generic official-site discovery page for the remaining sources.
