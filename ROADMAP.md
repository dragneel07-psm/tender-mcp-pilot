# Roadmap — Nepal Tender Intelligence & Procurement Automation Platform

This adapts the requested 12-milestone structure to what `ARCHITECTURE_AUDIT.md` actually found. Two adjustments from the original brief, both consequences of the audit rather than a scope cut:

1. **Milestone ordering is tightened around risk.** This app is live in production, running real WhatsApp alerts off ~7,000 already-collected notices, on a schedule that was only made reliable in the hours before this roadmap was written. Every milestone below is sequenced so the previous one leaves the system in a shippable, tested state — no milestone should ever leave `master` red or the Railway deployment broken.
2. **Dependencies stay stdlib-only until a milestone genuinely requires otherwise.** Per the audit (§17), that's Milestone 3 (document text extraction) and Milestone 10 (AI providers) at the earliest. Milestones 1-2 need zero new dependencies.

Each milestone ends with: tests green, README/CHANGELOG updated, and (for milestones that touch the live app) a deploy + production verification pass — the same discipline already used for every change made to this app so far this session.

**Status: Milestones 1 and 2 shipped.** See `CHANGELOG.md` for what actually landed in each (the entries there are the record of truth; this file stays the forward-looking plan and isn't rewritten after the fact).

---

## Milestone 1 — Architecture cleanup + regression tests *(this session)*

Split `app.py` into a package with clean module boundaries and zero behavior change, verified by running every existing test plus new ones added for previously-untested paths (`Api` HTTP routing, MCP loop, source-import/bootstrap path). See "Proposed target structure" below for the exact layout. No schema change, no dependency change, no endpoint/response-shape change.

**Exit criteria:** full test suite green, a local smoke-test boot against an isolated data dir succeeds, Railway deploy succeeds, `/health` and `/collection/status` verified live, at least one full collection cycle completes cleanly post-deploy.

## Milestone 2 — Normalized tender schema + adapter interface

Add the `BaseTenderSource` adapter interface and a `Tender` schema (additive columns on `notices`, or a new `tenders` table — decided during implementation once the exact migration cost is known) covering the fields in the target spec §3 that can be populated *without* new capabilities: `organization`, `province`, `district` (new — not currently modeled at all), `notice_type`, `status`, `first_seen`/`last_seen`, `content_hash`, `confidence_score`. Fields that require document intelligence (`estimated_amount`, `bid_security_amount`, `eligibility`, etc.) stay `null` until Milestone 3 — per engineering rule #2, never fabricate.

This is also where source registry management (currently `sources.json`, read/write-races per audit §4/§11) gets a proper concurrency-safe store, since the adapter interface needs somewhere sane to persist `last_success`/`failure_count`/`priority` per source without another flat-file race.

## Milestone 3 — Document intelligence

The single biggest capability gap (audit §3, §15). Introduces the first new dependencies: a PDF text extractor at minimum (evaluate `pdfminer.six` vs. `pypdf`; OCR — e.g. `pytesseract` — only if a real scanned-document rate justifies the added system dependency, per engineering rule #18, "avoid unnecessary dependencies"). Document download must be sandboxed: SHA-256 verification before any processing, strict content-type/size limits, no execution of downloaded content, ZIP extraction with path-traversal and zip-bomb guards (audit §22 flags this as new attack surface with zero existing mitigation). Populates the amount/eligibility/deadline-detail fields Milestone 2 left null.

## Milestone 4 — Classification + advanced search

Category taxonomy (target spec §6) applied via keyword/rule-based classification first (no AI dependency required for a first pass — keeps this milestone stdlib-only, consistent with the `TENDER_WORDS` mechanism already in place). Upgrade `list_notices()`/`/notices` into the filtered, paginated search described in target spec §12, with a real index plan for `notices.source_id`/`discovered_at` (currently unindexed, per audit §12) before enabling arbitrary filter combinations.

## Milestone 5 — Company profiles + matching

`company_profile` storage, `match_tender_to_company()` with the explainable per-dimension scoring the target spec §7 requires. Pure business logic over the Milestone 2-4 schema — no new infrastructure needed.

## Milestone 6 — Tender change detection

Requires notices to stop being effectively-immutable (audit §6, §9: today a re-scrape either inserts a new row or is silently discarded — nothing is ever compared). Introduces version history and a diff step in the collection pipeline, plus the `DEADLINE_CHANGED`/`CORRIGENDUM`/`TENDER_CANCELLED` alert types this unlocks.

## Milestone 7 — Advanced watchlists + alert engine

`AlertProvider` abstraction (target spec §14), replacing the single hardcoded `send_whatsapp_alert()` call site (audit §9). Watchlists upgraded from "list of source IDs" (today's `validate_watchlist()`) to full saved-search objects. Deadline-reminder scheduling (target spec §15) lands here, since it needs both the alert-type work from this milestone and the deadline field from Milestone 3.

## Milestone 8 — MCP 2.0

Expand from 3 tools to the full target-spec §16 set, now that source health, collection status, matching, and change detection all exist as real backing functions rather than needing to be stubbed. Add pagination and structured error objects to every tool (audit §8 flags today's tools as having neither).

## Milestone 9 — Dashboard 2.0

Only after the API has pagination, filters, and the richer schema — building the target spec §19 dashboard against today's "fetch all 662 sources, all 100 notices, client-side filter" model (audit §10) would mean rebuilding it again immediately after Milestone 4.

## Milestone 10 — AI intelligence

First milestone where an AI provider dependency is justified. Provider-independent (target spec §8), strictly optional (engineering rule #13) — collection, search, and alerts must all keep working with zero AI configured, exactly like WhatsApp alerts already degrade gracefully today when unconfigured (`send_whatsapp_alert()`'s `"skipped", "WhatsApp is not configured"` path, `app.py:270-271`, is the existing pattern to follow). AI-derived fields must be provenance-tagged separately from source-derived fields (target spec §29) — never overwrite a verified extraction with an LLM guess.

## Milestone 11 — PostgreSQL/queue migration (only if justified)

Per target spec §24 and engineering rule: do not migrate for fashion. Trigger conditions to actually do this: sustained write contention past what one `DB_WRITE_LOCK` can serialize (i.e., multiple Railway replicas become necessary), or `notices`/`tenders` row count reaching a point where SQLite's single-file model becomes the bottleneck despite proper indexing from Milestone 4. Document the decision either way at this point in the roadmap, even if the answer is "not yet."

## Milestone 12 — Security, observability, production hardening

Structured logging with per-cycle IDs (target spec §21), the metrics list from target spec §21, and closing the gaps audit §13 identified (rate limiting, per-user accounts if multi-operator use has materialized by this point). Revisit this milestone's scope based on what actually shipped in 1-11 rather than pre-committing to specifics now.

---

Every milestone after 1 is scoped at kickoff time (its own short plan, not written speculatively here) against whatever Milestones 1-N actually shipped — the target spec is a north star, not a spec frozen before Milestone 1 has even run.
