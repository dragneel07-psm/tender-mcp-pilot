---
name: Notice Feed
description: A Nepali procurement desk read as a working temple courtyard, not a generic SaaS dashboard.
colors:
  bg: "#f3ecdd"
  surface: "#fbf6ea"
  surface-raised: "#f7efdd"
  ink: "#3a2317"
  muted: "#7c6650"
  line: "#e2d3b6"
  accent: "#8a6a2e"
  accent-soft: "#efe2c2"
  structure: "#96391f"
  structure-soft: "#f3e0d7"
  danger: "#8a3324"
  danger-soft: "#ecd9d0"
  warn: "#b3242b"
  warn-soft: "#f6dfdd"
typography:
  body:
    fontFamily: "Hind, Noto Sans Devanagari, ui-sans-serif, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.55
  heading:
    fontFamily: "Hind, Noto Sans Devanagari, ui-sans-serif, system-ui, sans-serif"
    fontSize: "clamp(24px, 3.2vw, 36px)"
    fontWeight: 700
    lineHeight: 1.14
    letterSpacing: "-0.015em"
rounded:
  sm: "9px"
  md: "12px"
spacing:
  sm: "10px"
  md: "18px"
  lg: "28px"
components:
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.accent-soft}"
    rounded: "{rounded.sm}"
    padding: "9px 13px"
  button-primary-hover:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.surface}"
  button-default:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "9px 13px"
  tag-important:
    backgroundColor: "{colors.warn-soft}"
    textColor: "{colors.warn}"
    rounded: "999px"
---

# Design System: Notice Feed

## Overview

**Creative North Star: "The Durbar Ledger"**

Notice Feed reads as a working ledger kept in a Kathmandu Valley temple courtyard, not a generic
operations SaaS. The material world is whitewashed plaster underfoot, aged carved wood for ink and
structure, brick terracotta for the building's own bones, and patinated brass for anything that
invites a touch — a link, a button, a focus ring. Against that restrained, workmanlike ground, one
color is reserved and precious: vermilion, the sindoor/tika red used across Nepal to mark something
auspicious or significant. It appears in exactly one place — the "Important" flag on an ICT/
electronics-relevant notice — never as decoration, never diluted into a second use. The system
refuses the two defaults every "Nepal-themed" interface reaches for: the literal flag palette
(crimson + royal blue) played as wallpaper, and tourist iconography (mountains, prayer flags,
temple silhouettes as illustration). Nothing here is illustrated; everything is built from real
material color and real carved-relief geometry, at operate-mode restraint — this is a task tool a
small team checks all day, not a marketing surface.

**Key Characteristics:**
- One reserved accent (vermilion) carries exactly one meaning across the whole system — significance — and nowhere else.
- Devanagari and Latin script are typeset in the same family (Hind) at the same weight and size scale; neither is a fallback for the other.
- Flat, warm, paper-and-wood surfaces — no glass, no gradients-as-decoration, no glow except the one authored brass focus/hover treatment.
- A single repeating geometric frieze (a carved-strut relief, rendered in pure CSS gradients) is the system's one ornamental signature, used sparingly at structural boundaries only.

## Colors

Every color is a real material read off the Durbar Square world, not a hue picked for contrast alone.

### Primary
- **Patinated Brass** (`#8a6a2e`, soft wash `#efe2c2`): the interactive color — links, focus rings, hover borders, the province-chip active state. Anything the user can act on reads brass. Dark mode: `#d7ab5e` on `#4a3820`, brighter, like brass catching lamplight.

### Secondary
- **Brick Terracotta** (`#96391f`, soft wash `#f3e0d7`): the informational/structural tone — "New", "Awarded", "Corrigendum" tags, and the favorite-source top-edge accent. Distinct from brass so "you can act on this" and "here is a status" never look like the same signal. Dark mode: `#e0836a` on `#3a2015`.

### Tertiary — reserved
- **Vermilion / Tika Red** (`#b3242b`, soft wash `#f6dfdd`): reserved to exactly one use — the "Important" ICT-relevance flag, rendered as a tika-mark dot (not a star, not a generic pill) with a soft halo, plus a full vermilion-tinted card background and border on the flagged notice. Dark mode: `#e2645f` on `#341613`. **This color must never be reused for anything else** — its rarity is what makes it legible as "act on this specifically."
- **Rust** (`#8a3324`, soft wash `#ecd9d0`): errors, cancellations, failed fetches — a distinct, duller red from vermilion so a genuine problem is never confused with a flagged opportunity. Dark mode: `#e2917f` on `#4a2a20`.

### Neutral
- **Whitewashed Plaster** (`#f3ecdd`): the page ground. Warm ivory, never cold white — this is a lived-in courtyard, not a lightbox. Dark mode inverts to `#231610`, a temple interior at dusk.
- **Plaster Surface** (`#fbf6ea`): card/panel background, a hair lighter than the ground so surfaces read as objects sitting on the courtyard floor. Dark mode `#2d1c14`.
- **Aged Wood** (`#3a2317`): primary text — deep carved-wood ink, not near-black. Dark mode inverts to a warm ivory `#f1e6d4`.
- **Umber-Gray** (`#7c6650`): secondary/muted text. Dark mode `#b99d7f`.
- **Plaster Hairline** (`#e2d3b6`): borders and dividers — a crack in old plaster, not a hard digital rule. Dark mode `#4a3423`.

### Named Rules
**The One Red Rule.** Vermilion (`--warn`) means exactly one thing everywhere in this system: an ICT/electronics-relevant notice worth following up on. It is never used for emphasis, decoration, or any other alert — Rust (`--danger`) carries every other error/failure state instead.

## Typography

**Body & Display Font:** Hind (with Noto Sans Devanagari, then system sans, as fallback)

**Character:** A single humanist sans purpose-built for unified Devanagari + Latin type — chosen specifically because most real content here is bilingual or Nepali-only, and a fallback font for Devanagari reads as an afterthought. Hind carries both scripts at matching x-height and weight, so hierarchy comes from size and weight alone, never from switching typefaces between languages.

### Hierarchy
- **Heading** (700, `clamp(24px, 3.2vw, 36px)`, line-height 1.14, tracking -0.015em): hero title, "the tenders worth acting on" — the one large display moment on the page.
- **Section title** (700, 20px): "Local governments", "Latest notices", "Add source".
- **Body** (400, 15px, line-height 1.55): notice titles, meta text, form labels — the working density of the page.
- **Small/label** (600–700, 12–13px): tags, badges, metric labels.

### Named Rules
**The No-Kicker Rule.** No small-caps label sits above a heading as a decorative flourish. `#hero-eyebrow` exists in the DOM (JavaScript writes to it when a single source page is open) but is visually hidden (`sr-only`) — the heading carries its own weight.

## Layout

A single-column content shell (`max-width: 1380px`), centered, with generous outer padding (24px desktop, 16px mobile). Sections stack vertically with consistent rhythm; grids (metrics, source cards) use `auto-fit`/`auto-fill` with a `minmax()` floor so columns collapse gracefully rather than breaking at fixed pixel breakpoints. One explicit breakpoint at 760px switches the hero to a single column, collapses the metrics grid to 2-up, and stacks notice-card actions. Source cards are `display:flex;flex-direction:column` — never absolutely-positioned metadata — so a long (frequently bilingual, sometimes two-line) authority name always pushes content below it rather than overlapping.

## Elevation & Depth

Mostly flat — surfaces sit at the same visual height as the plaster ground, distinguished by a 1px hairline border, not a shadow. A single warm, umber-tinted shadow (`0 18px 40px rgba(58,35,23,.14)`, dark mode `rgba(0,0,0,.4)`) appears only on the hero panel and on hover/interaction (a lifted card, an active dropdown) — depth is a response to attention, not an ambient default.

### Named Rules
**The Hairline-Not-Shadow Rule.** A resting card or panel is separated from its ground by a 1px `--line` border. Shadow is reserved for the hero (the one panel that's always "raised") and for hover states.

## Shapes

Corners are a tight 12px (`--r`) on major panels and cards, 9px on buttons/inputs/chips — precise and structural rather than the soft, very-rounded corners of a generic SaaS card. The one exception is the pill shape (`border-radius: 999px`) reserved for tags/badges, where a fully rounded shape reads correctly as a small stamped label. Favorite sources carry a 3px solid brass top border — a gilt lintel line, not a full outline — echoing an entrance lintel rather than a decorative frame.

## Components

### Buttons
- **Shape:** 9px radius, 1px border by default.
- **Primary** (`.button.primary`): aged-wood ink background (`--ink`), brass-tinted text (`--accent-soft`) — the darkest, most confident surface in the system. On hover it inverts to a solid brass fill.
- **Default** (`.button`): plaster surface, hairline border, brass border + soft-brass background on hover.
- **Ghost** (`.button.ghost`): dashed border, muted text — used for "add this back" affordances (a disabled province waiting to be re-enabled).
- **Danger** (`.button.danger`): rust text only, no fill, for destructive actions (remove source/watchlist).
- All interactive transitions use one shared easing curve, `--ease: cubic-bezier(.16,1,.3,1)`, at 180ms — the system's one authored motion signature, not a different curve per component.

### Chips (province filters)
- **Enabled province:** a button + an attached square-cornered-outer/round-inner "remove" segment (a drawn ×, never a bare unicode character) sharing one pill-like compound shape.
- **Disabled province:** a dashed-border ghost button with a drawn "+" icon, offered inline rather than hidden in a menu.

### Tags / Badges
- **Style:** 999px pill, 12px bold text, 3–8px padding, no border except `.tag.priority`.
- **Important** (`.tag.priority`): the one tag with a border and a `::before` tika-mark dot (6px filled circle + 3px soft halo) — every other tag is text-only on a soft fill.
- **New / Awarded / Corrigendum** (`.tag.info`, `.new-badge`): terracotta text on terracotta-soft.
- **Cancelled / unread count** (`.tag.danger`, `.badge`): rust text on rust-soft.

### Cards / Containers
- **Corner style:** 12px.
- **Background:** plaster surface (`--surface`) at rest; a matching notice or source card never uses a different neutral than its siblings.
- **Border:** 1px hairline; the flagged-important notice card is the one exception, gaining a full vermilion border and vermilion-soft background (never a colored border-left accent stripe).
- **Internal padding:** 18–19px (cards), 22–30px (hero/manager panels).

### Inputs / Fields
- **Style:** plaster-ground background (`--bg`, one step darker than the surrounding surface), 1px hairline border, 9px radius.
- **Focus:** border turns brass, plus a 3px soft-brass halo (`box-shadow: 0 0 0 3px var(--accent-soft)`) — applied uniformly via `:focus-visible` across buttons, inputs, and selects.

### Signature component: the frieze
A thin (7px) horizontal band of repeating diamonds, built from two overlapping CSS `linear-gradient`s (no image request), standing in for a temple strut's carved relief. Used once, directly under the header, as the system's single ornamental signature — never repeated at every section boundary, so it stays a moment rather than wallpaper.

## Do's and Don'ts

### Do:
- **Do** keep vermilion (`--warn`) reserved to the "Important" flag alone, everywhere in the system.
- **Do** set Hind (or another genuinely Devanagari-native family) as the actual font loaded — never let Devanagari silently fall back to the platform default sans.
- **Do** use `display:flex;flex-direction:column` (or an equivalent flow-respecting layout) for any card holding a variable-length authority name or title; absolute-positioned metadata over unconstrained text will overlap on real (frequently long, sometimes bilingual) content.
- **Do** draw every icon (close, add, warning, the tika mark) as CSS or inline SVG in `currentColor`, one consistent stroke weight.

### Don't:
- **Don't** add a kicker/eyebrow label above a heading, even a small muted one — the heading carries its own weight.
- **Don't** use a colored `border-left`/`border-right` as a status accent on a card; use a full border + tinted background (see the Important notice treatment) instead.
- **Don't** use a unicode glyph or emoji (★, ⚠, ×, +) standing in for an icon.
- **Don't** introduce a second "reserved" accent color competing with vermilion for the reader's attention — every other status (new, awarded, cancelled, failing) already has a home in terracotta or rust.
