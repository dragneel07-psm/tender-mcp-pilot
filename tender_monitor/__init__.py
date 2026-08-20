"""Sudurpashchim Tender Monitor: collector, HTTP API, and MCP stdio server.

Package layout (see ARCHITECTURE_AUDIT.md and ROADMAP.md for the full rationale):
  config      -- paths, env loading, constants
  parsing     -- HTML parsing and date/text extraction primitives
  net         -- outbound HTTP fetch
  storage     -- SQLite schema/connection and JSON-file source/watchlist registries
  health      -- per-source failure tracking and skip/cooldown logic
  discovery   -- official Ministry directory import (bootstrap-*)
  alerts      -- outbound notification delivery (WhatsApp today)
  collector   -- the collection pipeline (collect_one/collect_all)
  queries     -- read-models backing the HTTP API and MCP tools
  api         -- the HTTP handler
  scheduler   -- the background collection loop
  mcp_server  -- the MCP JSON-RPC stdio server
  cli         -- argv dispatch (the app.py entrypoint delegates here)
"""
