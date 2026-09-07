# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

A small internal team (a handful of people, not a single solo operator) at a company that bids on
Nepali government procurement — day-to-day they check the dashboard for new tender notices,
especially ones needing ICT/electronics/networking/machinery equipment, so they can follow up and
bid before a deadline. There is no login -- the dashboard and API are open to anyone who can reach
the host, consistent with a small trusted team rather than public/multi-tenant access.

## Product Purpose

Continuously monitor procurement (tender) notices published by Nepali local governments and
national procurement portals, so the team never has to manually check dozens of separate
government websites. It collects notices hourly, extracts text from linked PDF/photo attachments
(OCR when the source has no real text layer — a scanned document or a photographed notice board),
and flags any notice mentioning ICT/computer/e-attendance/networking/printer/CCTV/smartboard/
machinery/electronics — whether that's in the title or only inside an attached document — as
"Important," with an instant WhatsApp alert on every new notice found.

## Positioning

Where a team would otherwise have someone manually re-check each local government's own tender
page, this system watches all of them on a schedule, reads the notice's own document attachments
(not just the listing title) via OCR, and surfaces the ICT-relevant subset specifically — the
category of tender this team can act on — rather than a generic firehose of every notice type
(the taxonomy also covers roads, water supply, medical, agriculture, etc., which this team
doesn't bid on).

## Operating Context

- Hosted as a single Python process on Railway (`tender-mcp-pilot`), started via `python3 app.py
  serve`; a background thread runs the collection cycle every `AUTO_COLLECT_INTERVAL_MINUTES`
  (60 by default) while the same process serves the dashboard and JSON API.
- Sources are Nepali local-government notice pages, organized by the seven provinces (Koshi,
  Madhesh, Bagmati, Gandaki, Lumbini, Karnali, Sudurpashchim), plus a "National / other" bucket
  for national-level sources (Bolpatra — Nepal's PPMO e-GP portal — and Jobs Nepal). 663 sources
  configured today, with Sudurpashchim/Karnali/National currently the team's focus in the
  dashboard's province browser (a persisted, reversible visibility toggle — collection itself
  still covers every configured source regardless of what's shown).
- Notice text is routinely bilingual or Nepali-only (Devanagari script — authority names, notice
  titles, dates), sometimes citing Bikram Sambat dates rather than Gregorian. This is a normal,
  expected case, not an edge case to tolerate poorly.
- A notice can carry attached documents (PDF, or a direct photo of a notice board/scanned
  paper) that the dashboard doesn't currently surface as a distinct UI element, though the data
  (extraction status, content type, extracted text) is captured server-side.
- Distributed via a shared WhatsApp Business number today; the dashboard is the durable
  record and search surface behind that alert stream.

## Capabilities and Constraints

- Single self-contained `dashboard.html` (no build step, no JS/CSS framework, inline `<style>`/
  `<script>`) served by a stdlib `http.server`-based Python API — no client-side router, no
  bundler. Any redesign works within that constraint (plain HTML/CSS/vanilla JS).
- Core dashboard sections today: hero/status header, WhatsApp alert + collection-cycle status,
  metric tiles, a province-filterable "Local governments" source browser with add/remove-province
  visibility controls, a source manager (add/edit/remove/favorite a source, manage watchlists),
  and a filterable "Latest notices" feed (search, category, province, notice type, status, unread,
  and "Important only" filters).
- "Important" is a real, structured signal (`notices.priority`, a ★ badge + highlighted card),
  not just a copy label — computed from title keywords and escalated from OCR'd/extracted document
  text. Design work must keep this visually distinct and legible, not decorative.
- Company-profile matching and saved watchlists exist as features but have no real data yet
  (`company_profiles.json` is empty; one watchlist named "Important" exists, unrelated to the
  priority-flag feature of the same name — don't conflate the two in copy).
- No design system or component library exists yet; there's no DESIGN.md. Colors, spacing, and
  components today are ad hoc CSS custom properties in the file's own `<style>` block.

## Brand Commitments

No company name or logo is attached to this product — it stays generically branded (the dashboard
currently calls itself "Notice Feed"). Any redesign should keep it nameless/generic rather than
introduce a specific company identity, per the team's explicit choice.

## Evidence on Hand

- Real, live data: `sources.json` (663 real Nepali local-government + national sources with real
  province/URL/keyword metadata) and a real `tenders.db` with genuinely collected notices (not
  seed/demo data) — screenshots and design decisions should be validated against this real content
  (long Nepali titles, mixed-script authority names, varying notice-card density), not lorem ipsum.
- No customer testimonials, case studies, or external marketing claims exist or should be
  fabricated — this is an internal operations tool, not a marketing surface.

## Product Principles

1. Bilingual-by-default: Nepali (Devanagari) and English coexist in the same notice, often the
   same line — typography and layout must treat both as first-class, not a fallback case.
2. The "Important" signal is the product's core value; it must never be visually weaker than
   decorative brand elements introduced by a redesign.
3. This is an operate/Read surface for a small trusted team doing a recurring monitoring task,
   not a persuade/marketing surface — scanability and trustworthy, official-feeling clarity over
   expressive flourish.
4. No build tooling: every visual decision must be deliverable as plain CSS/HTML/vanilla JS
   inside the existing single-file structure.
5. Never fabricate data, companies, or content to fill out a "prettier" empty state — real source/
   notice data (or an honest, explicit empty state) only.

## Accessibility & Inclusion

No formal accessibility standard has been set. The one confirmed, load-bearing requirement:
Devanagari (Nepali) script must render with a proper, intentional typeface pairing (not a silent
system-default fallback) since real notice content is frequently Nepali-only or mixed-script.
