#!/usr/bin/env python3
"""Sudurpashchim Tender Monitor entrypoint.

This file is intentionally a thin shim: Railway's start command, the Procfile, and any external
MCP client configuration all invoke `python3 app.py <command>` directly, so this exact filename and
its argv-dispatch behavior must not change. All actual logic lives in the tender_monitor/ package
-- see tender_monitor/__init__.py, ARCHITECTURE_AUDIT.md, and ROADMAP.md.
"""
from tender_monitor.cli import main

if __name__ == "__main__":
    main()
