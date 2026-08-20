"""Outbound HTTP fetching. The only module that talks to arbitrary external URLs."""
import os
import re
import time
import urllib.error
import urllib.request

from . import parsing
from .config import USER_AGENT


def fetch(url, timeout=None, retries=None):
    req=urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language":"en,ne;q=0.8"})
    timeout = int(os.getenv("SOURCE_TIMEOUT_SECONDS", "45")) if timeout is None else timeout
    retries = max(1, int(os.getenv("SOURCE_RETRIES", "2"))) if retries is None else max(1, retries)
    last_error = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode(r.headers.get_content_charset() or "utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt < retries - 1: time.sleep(3 * (attempt + 1))
    raise last_error


def linked_notice_date(url):
    """Check an individual HTML notice page when the listing itself has no date.
    Best-effort and deliberately cheap: a short timeout and a single attempt (no retries), since a
    source can have many such notices and this used to run one at a time with the full
    SOURCE_TIMEOUT_SECONDS*SOURCE_RETRIES budget each -- a handful of slow/dead notice pages on one
    source could stall an entire collection cycle by tens of minutes. See collector.collect_one,
    which also caps and parallelizes these lookups per source."""
    if url.lower().split("?",1)[0].endswith(".pdf"): return None
    try:
        timeout=int(os.getenv("NOTICE_PAGE_LOOKUP_TIMEOUT_SECONDS", "15"))
        page=fetch(url, timeout=timeout, retries=1)
        return parsing.first_date(parsing.clean(re.sub(r"<[^>]+>", " ", page)))
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None
