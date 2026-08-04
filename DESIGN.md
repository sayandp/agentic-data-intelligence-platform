---
name: Agentic Data Intelligence Platform
description: A clinical, evidence-first operating dashboard for a self-healing data pipeline
colors:
  ink: "#12161C"
  ink-muted: "#4B5563"
  ink-faint: "#6B7280"
  paper: "#F7F8FA"
  surface: "#FFFFFF"
  surface-sunken: "#F1F3F6"
  border: "#E2E5EA"
  border-strong: "#C7CCD4"
  signal-teal: "#0B6E6E"
  signal-teal-hover: "#095A5A"
  signal-teal-tint: "#E3F1F1"
  signal-teal-50: "#E3F1F1"
  signal-teal-100: "#CFE7E7"
  signal-teal-200: "#A9D4D4"
  signal-teal-500: "#12807E"
  signal-teal-900: "#0A3D3D"
  signal-teal-950: "#072B2B"
  status-positive: "#15803D"
  status-positive-tint: "#E8F5EC"
  status-negative: "#B91C1C"
  status-negative-tint: "#FBEBEB"
  status-caution: "#9A6700"
  status-caution-tint: "#FBF2DE"
  status-active: "#1E40AF"
  status-active-tint: "#E9EEFB"
  nav-bg: "#141922"
  nav-ink: "#B8C0CC"
  nav-ink-active: "#FFFFFF"
typography:
  ui:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.45
  narrative:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
    fontSize: "16px"
    fontWeight: 400
    lineHeight: 1.65
  data:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.4
rounded:
  sm: "4px"
  md: "6px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "40px"
components:
  button-primary:
    backgroundColor: "{colors.signal-teal}"
    textColor: "#FFFFFF"
    rounded: "{rounded.sm}"
    padding: "6px 14px"
  button-primary-hover:
    backgroundColor: "{colors.signal-teal-hover}"
  button-danger:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.status-negative}"
    rounded: "{rounded.sm}"
    padding: "6px 14px"
  card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.md}"
    padding: "20px"
---

# Design System: Agentic Data Intelligence Platform

## Overview

**Creative North Star: "The Instrument Panel"**

This is a diagnostic instrument, not a product pitch. The system ingests data, decides what it can safely fix and what it can't, and shows a human exactly why - the interface's job is to make that reasoning legible at a glance, the same way a calibrated lab instrument or an avionics status panel reads: quiet at rest, unambiguous the moment something needs attention, never decorative for its own sake.

The prior implementation used a violet/purple accent (`#7c5cff`) inherited from an unmodified Tailwind starter palette, `rounded-2xl` cards with soft shadows stacked in undifferentiated columns, and no typographic distinction between a report's prose and its surrounding chrome. All three read as generic AI-dashboard default rather than a considered instrument, and are retired by this system.

**Confirmed visual rejections** (binding, from product brief): purple gradients, glassmorphism, hero eyebrow chips, italic serif display type, "AI beige," nested cards, icon-tile stacks, numbered section labels, pulsing "AI is thinking" dots.

**Key Characteristics:**
- Flat, bordered surfaces - no drop shadows on ordinary content, depth comes from tone and a 1px border, never a floating card illusion.
- One accent color (a deep signal teal) used sparingly for actions and active state; everything else is ink, paper, and border grays.
- Status is color- and shape-coded (a filled dot plus a muted pill), never plain text a reader has to parse.
- Data (ids, timestamps, scores, counts) is monospaced and visually quieter than the judgment it supports; prose (a report's narrative) is set larger and looser than the UI chrome around it.

## Colors

Restrained strategy: neutrals carry the interface; one accent (signal teal) marks action and active state; a separate, fixed set of status colors carries meaning that must never be confused with the accent.

### Primary
- **Signal Teal** (`#0B6E6E`): the one accent. Primary buttons, active nav item, links, focus rings, chart accent lines. Nothing else uses this hue - it must always mean "action" or "current."
  - Implemented as an 8-step ramp (`--color-brand-50/100/200/500/600/700/900/950`) rather than a single value, because a Tailwind utility scale needs stops. **600 is Signal Teal itself** and is the only stop that carries the accent's meaning; 700 is its hover, 50-200 are tints used for quiet fills (a file-input chip, a hover wash). The `brand-*` name is a Tailwind-utility artifact, not a second identity - there is exactly one accent hue in this system.

### Neutral
- **Ink** (`#12161C`): primary text, headings.
- **Ink Muted** (`#4B5563`): secondary text, labels, table headers.
- **Ink Faint** (`#6B7280`): tertiary/disabled text, placeholder copy.
- **Paper** (`#F7F8FA`): page background.
- **Surface** (`#FFFFFF`): card and table background. Currently emitted as the literal `bg-white` utility rather than a named token - the one neutral without a variable behind it.
- **Surface Sunken** (`#F1F3F6`): nested/inset regions (a table's header row, a code block's frame) - the ONE permitted "layer inside a card," used for structural grouping, never as a second decorative card.
- **Border** (`#E2E5EA`): default dividers and card borders.
- **Border Strong** (`#C7CCD4`): input borders, emphasis dividers.

### Status (fixed roles, never reused for anything else)
- **Positive** (`#15803D` on `#E8F5EC`): completed, resolved, answered, low risk.
- **Negative** (`#B91C1C` on `#FBEBEB`): failed, rejected, high risk, the irreversible-action affordance.
- **Caution** (`#9A6700` on `#FBF2DE`): awaiting approval, escalated, provisional.
- **Active** (`#1E40AF` on `#E9EEFB`): running, in progress.

### Named Rules
**The One Accent Rule.** Signal teal appears on primary actions, the active nav item, links, and focus rings only - never as a decorative fill, never as a background wash. If a screen has more than a few teal elements, something has drifted into decoration.

**The Status-Never-Accent Rule.** The four status colors and the one accent color are never the same hue family. A reader must be able to tell "this is the brand color" from "this is a verdict" without reading the label.

## Typography

**UI Font:** system-ui, -apple-system, "Segoe UI", Roboto, sans-serif
**Narrative Font:** the same system stack, at a larger size and looser line-height - a distinguishing SCALE, not a distinguishing typeface (an italic serif display face is explicitly rejected; switching families for "editorial" prose risks the same drift).
**Data Font:** ui-monospace, SFMono-Regular, Menlo, Consolas, monospace

**Character:** one workhorse sans for the whole system; monospace is reserved for anything a reader might copy, compare digit-by-digit, or verify (ids, timestamps, scores, code). The pairing is deliberately quiet - legibility over expression, per Operate mode.

### Hierarchy
- **Page title** (600, 22px, 1.3): one per page, top-left, never centered.
- **Card title** (600, 15px, 1.4): section headings inside a card or panel.
- **Body/UI** (400, 13px, 1.45): default chrome - labels, table cells, buttons, form inputs.
- **Narrative** (400, 16px, 1.65, max 72ch measure): report prose only. This is the one place text is set FOR READING rather than scanning.
- **Data/mono** (400, 12px, 1.4): ids, timestamps, numeric table columns, code blocks.
- **Label** (500, 11px, uppercase, 0.04em tracking): table column headers, small section eyebrows where genuinely structural (never a decorative "STEP 01" style numbered label).

### Named Rules
**The Reading vs. Scanning Rule.** Anything meant to be read start-to-finish (report narrative, recommendations) is set at Narrative scale with a real measure. Anything meant to be scanned (tables, metadata, status) stays at Body/UI or Data scale, dense and left-aligned (numerics right-aligned).

## Layout

Single main column with a max-width content container (`72rem`), left sidebar navigation fixed at `16rem`. Spacing scale: 4 / 8 / 16 / 24 / 40px - tight (4-8px) within a related group (a label and its value, a badge and its text), generous (24-40px) between distinct sections. A page never uses more than two "gap sizes" (grouping is not modulated by five different margins).

Tables use a real header row on a sunken background, right-aligned numeric columns, monospaced and muted id columns, and comfortable but not loose row height (36-40px). Cards do not nest inside other cards; a section that needs internal grouping uses a sunken sub-region (a `Surface Sunken` block) or a plain divider, never a second bordered box.

Responsive floor: this is an operator dashboard used on a desk, not optimized for phone-sized screens, but layout must not break below tablet width (nav collapses to icons only under ~900px if ever needed; not built until a real need appears).

## Elevation & Depth

Flat by default. No drop shadows on cards, tables, or buttons at rest - separation comes from a 1px border and a background tone shift (`Surface` on `Paper`), never a shadow implying the element floats above the page. The one exception is a toast/transient notification, which gets a small ambient shadow because it is genuinely overlaying content, not part of the page flow.

### Named Rules
**The Flat-By-Default Rule.** If removing a shadow would not change what the element IS (a card is still a card, a button is still a button), the shadow was decorative and is removed. Shadows exist only for things temporarily floating above the page.

## Shapes

Small, consistent corner radius: 4px for buttons/badges/inputs, 6px for cards and panels. Never the soft `rounded-2xl`/`rounded-3xl` treatment the prior implementation used - large radii read as consumer-app-friendly, which this instrument is deliberately not. Borders are always 1px, solid, never a gradient or double-border effect.

## Components

### Buttons
- **Shape:** 4px radius, 1px border (default/danger) or none (primary, filled).
- **Primary:** `Signal Teal` fill, white text, no border. Hover darkens to `#095A5A`. Used for exactly one primary action per view.
- **Default:** white fill, `Border Strong` outline, `Ink` text. Hover: `Surface Sunken` fill.
- **Danger:** white fill, `status-negative` outline and text. Reserved for the single irreversible action (discard run) - never used for an ordinary "reject"/"dismiss" decision, so its color alone signals irreversibility before a reader even reads the label.
- **Loading:** an inline spinner plus the in-progress verb ("Ingesting...", "Resolving...") - never a bare spinner with no label, and never a percentage that isn't real.

### Badges / Status
- **Style:** filled pill (existing pattern kept - it is legible and already familiar), tinted status background, a small leading dot in the solid status color so meaning reads from shape and color before the word is read.
- **Roles:** exactly the four status colors above, mapped consistently across every page - completed/answered/resolved/low = positive; failed/rejected/high = negative; awaiting_approval/escalated/provisional = caution; running/template = active.

### Cards / Panels
- **Corner:** 6px.
- **Background:** `Surface` on `Paper`.
- **Border:** 1px `Border`.
- **Shadow:** none.
- **Internal padding:** 20-24px.
- **Quality Context panel (Reports only):** a distinct treatment from every other card - full 1px `status-caution` border (not a left-accent bar - a decorative color bar is a cliché this system refuses; a full-perimeter border plus a tinted fill is a structurally different card, not a decorated one) and `status-caution-tint` background, never demoted to plain-card weight, always the first thing on the page after the title. This is a hard product constraint, not a style choice up for revision.

### Tables
- **Header row:** `Surface Sunken` background, `Label` typography, bottom `Border` divider.
- **Body rows:** `Surface` background, 1px bottom `Border` divider, no zebra striping (striping adds visual noise this system doesn't need; a hovered row gets a subtle `Surface Sunken` tint instead).
- **Numeric columns:** right-aligned, `Data` font.
- **Id columns:** `Data` font, `Ink Faint` color (present but visually quiet - it is the app's job to make the id copyable/linkable, not the reader's job to parse it character by character).
- **Overflow:** every table sits in its own `overflow-x: auto` container. A wide table scrolls inside its own frame; the page body never scrolls sideways.

### Run Picker (signature component)

The app's answer to "which run is this about," used identically on Ask and Predict. A labelled `<select>` of recent completed runs (`Run #48 - orders.csv - completed 9h ago`) paired with a narrow free-text field for a run number, so selection is the primary act and typing is the fallback. Beneath it, the selected run's own column names render as quiet `Data`-font chips.

- **Shape/colors:** standard input treatment (4px radius, `Border Strong` stroke, `Surface` fill) - it is a form control, not a feature card.
- **Rule:** a run is CHOSEN, never transcribed. No surface in this system asks a human to retype an identifier the app already knows, and no surface asks for a raw UUID.

### Navigation
- **Style:** fixed left sidebar, `nav-bg` (#141922) background, `nav-ink` (#B8C0CC) default label color.
- **Active state:** `nav-ink-active` (white) text and icon, subtle background lift (`rgba(255,255,255,0.08)`) - text/icon color change plus tone carries the state; no border accent bar (a decorative color bar is refused elsewhere in this system, so the nav does not get an exception) and never a solid filled pill in the accent color (that reads as a button, not a location).
- **Hover:** background lift only, no color change until active.

## Do's and Don'ts

### Do:
- **Do** render Quality Context first on every report, in the caution-tinted panel, above the narrative - never a footnote.
- **Do** keep generation mode (`llm`/`template`) visible as a plain badge near the report title on every report, in every mode.
- **Do** right-align every numeric table column and monospace-mute every id column.
- **Do** show a live elapsed-time counter on every long-running action (ingest, resolve, predict) - never a bare spinner with no sense of progress.
- **Do** list every gate reason in full on an escalated item - this is the accountability argument; it is never truncated, collapsed, or hidden behind a tooltip.
- **Do** render a correlated group of validation failures as exactly one item with one set of decision buttons.

### Don't:
- **Don't** use purple, violet, or any gradient anywhere in this system.
- **Don't** add a drop shadow to an element that isn't genuinely floating above the page.
- **Don't** nest a card inside another card - use a sunken sub-region or a divider instead.
- **Don't** use `caused`, `drove`, `impact`, `because of`, or any causal phrasing in UI copy, empty states, or labels - correlation is never presented as causation, in the chrome any more than in a report's prose.
- **Don't** let "Discard run (permanent)" read as a fourth variant of the three recoverable decisions - it keeps its own color (danger), its own visual separation, and its confirmation step.
- **Don't** give a card a corner radius larger than 6px, or a button/badge larger than 4px.
