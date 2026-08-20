"""Source adapters: BaseTenderSource defines the contract every source-type implementation must
meet (discover notices from a source, check whether the source is reachable). GenericHtmlLinkAdapter
is the only implementation today -- it's exactly today's scrape-a-listing-page-of-<a>-tags logic,
now behind an interface so Milestone 3+ adapters (a JSON API, a PPMO/e-GP feed, ...) can be added
without touching collector.py's orchestration (concurrency, locking, DB writes, alerting, retries).
"""
import hashlib
import os
import urllib.parse
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor

from . import net, parsing


class BaseTenderSource(ABC):
    """The contract a source adapter must implement. Kept intentionally small for now -- see
    ROADMAP.md Milestone 3 for where fetch_notice()/extract_metadata()/discover_documents() land,
    once document intelligence exists to make them meaningful rather than speculative stubs."""

    @abstractmethod
    def discover_notices(self, source):
        """Return a list of candidate notice dicts for this source (see GenericHtmlLinkAdapter for
        the exact shape). Should not raise for an unreachable/broken site -- but if it does,
        collector.collect_one treats that as the whole source having failed this cycle, same as
        today; adapters aren't required to swallow every possible error themselves."""
        raise NotImplementedError

    @abstractmethod
    def health_check(self, source):
        """True if the source's site is currently reachable at all. Not used by the collection
        pipeline yet (collect_one's own try/except and source_health already cover this) -- exists
        so a future MCP source_health tool (ROADMAP.md Milestone 8) has something adapter-level to
        call independent of running a full discover_notices() pass."""
        raise NotImplementedError


class GenericHtmlLinkAdapter(BaseTenderSource):
    """Scrapes a listing page's <a> tags, filters by title length and keyword relevance, and
    derives the Milestone 2 normalized fields that don't require document intelligence. This is
    the only adapter that exists today; every one of the currently configured sources uses it."""

    def discover_notices(self, source):
        body = net.fetch(source["notice_url"])
        parser = parsing.LinkTextParser(); parser.feed(body)
        candidates = []
        for href, label in parser.links:
            title = parsing.clean(label)
            url = urllib.parse.urljoin(source["notice_url"], href)
            if len(title) < 8 or not parsing.relevant(title + " " + url, source): continue
            snippet = parsing.context_snippet(body, href)
            published = parsing.first_date(title) or (parsing.first_date(snippet) if snippet else None)
            digest = hashlib.sha256((source["id"]+url+title).encode()).hexdigest()
            notice_type = parsing.classify_notice_type(title)
            candidates.append({
                "id": digest, "authority": source["name"], "organization": source["name"],
                "province": source.get("province"), "title": title, "url": url,
                "published": published, "notice_type": notice_type,
                "status": parsing.status_for_notice_type(notice_type),
                "categories": parsing.classify_categories(title),
                "content_hash": hashlib.sha256(snippet.encode()).hexdigest() if snippet else None,
                # 0.9 = found directly on the listing page; downgraded to 0.5 if no date is ever
                # found; _resolve_missing_dates below upgrades to 0.7 for a per-notice-page find.
                "confidence_score": 0.9 if published else 0.5,
            })
        self._resolve_missing_dates(candidates)
        return candidates

    def _resolve_missing_dates(self, candidates):
        """Notices with no date on the listing page get a supplementary per-notice-page lookup.
        This used to run one notice at a time with the full network-fetch retry budget each, so a
        source with many such notices (or several slow/dead notice pages) could stall an entire
        collection cycle by tens of minutes on its own (fixed same day, before Milestone 1). Cap
        how many are attempted per cycle and run them a few at a time; any left over simply keep
        their "collected" date this cycle instead of a "published" date -- a cosmetic fallback,
        not a correctness issue."""
        if os.getenv("NOTICE_PAGE_DATE_LOOKUPS", "1") != "1": return
        needs_lookup=[c for c in candidates if not c["published"]][:max(0, int(os.getenv("NOTICE_PAGE_LOOKUP_LIMIT", "15")))]
        if not needs_lookup: return
        lookup_workers=min(len(needs_lookup), max(1, int(os.getenv("NOTICE_PAGE_LOOKUP_WORKERS", "5"))))
        with ThreadPoolExecutor(max_workers=lookup_workers) as pool:
            for c, published in zip(needs_lookup, pool.map(lambda c: net.linked_notice_date(c["url"]), needs_lookup)):
                if published:
                    c["published"]=published
                    c["confidence_score"]=0.7

    def health_check(self, source):
        try:
            net.fetch(source["notice_url"], timeout=10, retries=1)
            return True
        except Exception:
            return False


# The collection pipeline always uses this today; a module-level singleton (not a factory) because
# GenericHtmlLinkAdapter is stateless -- see collector.collect_one.
DEFAULT_ADAPTER = GenericHtmlLinkAdapter()
