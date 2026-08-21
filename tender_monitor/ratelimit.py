"""Milestone 12: a minimal in-process rate limiter, closing audit §13's "no rate limiting
anywhere -- a leaked password allows unlimited API hammering" gap.

A fixed-window counter per client IP, not a token bucket -- simpler, and precise enough for a
single-operator pilot app guarding against brute-force/hammering, not a public-facing
high-traffic service. In-process only (no Redis/external store): this app runs as a single
Railway replica (see the Milestone 11 "not yet" migration decision in CHANGELOG.md), so state
doesn't need to survive a restart or be shared across instances -- the same reasoning that made
DB_WRITE_LOCK an acceptable in-process lock applies here.
"""
import os
import threading
import time

_LOCK = threading.Lock()
_WINDOWS = {}  # client_ip -> (window_start_epoch, count)

# Bounds memory across long uptimes against many distinct client IPs (e.g. a distributed hammering
# attempt). Reset rather than let the dict grow unboundedly; worst case a handful of legitimate
# clients get one extra allowed request the cycle this fires, not a real cost.
_MAX_TRACKED_CLIENTS = 1000


def allow(client_ip):
    """True if this request may proceed; False if `client_ip` has exceeded RATE_LIMIT_REQUESTS
    requests within the current RATE_LIMIT_WINDOW_SECONDS window. Set RATE_LIMIT_REQUESTS to 0
    (or negative) to disable rate limiting entirely."""
    limit = int(os.getenv("RATE_LIMIT_REQUESTS", "120"))
    if limit <= 0: return True
    window_seconds = max(1, int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60")))
    now = time.time()
    with _LOCK:
        if len(_WINDOWS) > _MAX_TRACKED_CLIENTS:
            _WINDOWS.clear()
        window_start, count = _WINDOWS.get(client_ip, (now, 0))
        if now - window_start >= window_seconds:
            window_start, count = now, 0
        count += 1
        _WINDOWS[client_ip] = (window_start, count)
        return count <= limit
