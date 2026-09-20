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
  # -- Dark theme (OPERATE) --
  dark-surface: "#0F1319"
  dark-surface-raised: "#171C24"
  dark-surface-sunken: "#1E242E"
  dark-nav-bg: "#0B0E13"
  dark-ink: "#E8ECF2"
  dark-ink-muted: "#B3BCC9"
  dark-ink-faint: "#8D97A5"
  dark-border: "#2B333F"
  dark-border-strong: "#414B5A"
  dark-accent: "#3FC3BD"
  dark-accent-hover: "#63D6D0"
  dark-status-positive: "#56D68A"
  dark-status-negative: "#FF8A8A"
  dark-status-caution: "#E9B455"
  dark-status-active: "#8FB0FF"
  # -- Aurora theme (EXPERIENCE, opt-in) --
  aurora-ground: "#0A0E1C"
  aurora-gradient-teal: "#07313A"
  aurora-gradient-indigo: "#141A4D"
  aurora-gradient-violet: "#2E1A54"
  aurora-wash-teal: "rgba(7, 74, 87, 0.55)"
  aurora-wash-violet: "rgba(62, 27, 104, 0.55)"
  aurora-surface-raised: "rgba(11, 16, 32, 0.90)"
  aurora-surface-sunken: "rgba(6, 10, 24, 0.88)"
  aurora-nav-bg: "rgba(7, 11, 26, 0.96)"
  aurora-ink: "#EAF1FF"
  aurora-ink-muted: "#B9C6E6"
  aurora-ink-faint: "#93A3C9"
  aurora-border: "rgba(150, 200, 255, 0.16)"
  aurora-border-strong: "rgba(160, 210, 255, 0.34)"
  aurora-accent-cyan: "#5EE0F5"
  aurora-accent-cyan-hover: "#8AEAF9"
  aurora-accent-magenta: "#FF86DD"
  aurora-status-positive: "#5EF0A8"
  aurora-status-negative: "#FF8A9C"
  aurora-status-caution: "#FFC46B"
  aurora-status-active: "#8FB8FF"
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

Every step is a NAMED UTILITY, defined once in `frontend/src/index.css`'s
`@theme` block. The token is the only way to express a step: a literal
(`text-[22px] font-semibold`) repeated across eight page headers was two
representations of one documented fact, which is the shape that produced two
`resolve_run` functions and `--chart-1..6` turning out to be the status
palette. Each token carries its own size, line-height and weight, so a call
site cannot apply half a step.

| Step | Utility | Weight | Size | Line-height | Tracking |
| --- | --- | --- | --- | --- | --- |
| Page title | `text-page-title` | 600 | 22px | 1.3 | - |
| Card title | `text-card-title` | 600 | 15px | 1.4 | - |
| Body/UI | `text-body-ui` | 400 | 13px | 1.45 | - |
| Narrative | `text-narrative` | 400 | 16px | 1.65 | - |
| Data/mono | `text-data` | 400 | 12px | 1.4 | - |
| Label | `text-label` | 500 | 11px | 1.4 | 0.04em |

- **Page title**: one per page, top-left, never centered.
- **Card title**: section headings inside a card or panel.
- **Body/UI**: default chrome - labels, table cells, buttons, form inputs.
- **Narrative**: report prose only, the one place text is set FOR READING rather than scanning. Pair with a `max-w-[72ch]` measure; a font-size utility cannot carry one.
- **Data/mono**: ids, timestamps, numeric table columns, code blocks.
- **Label**: small section eyebrows where genuinely structural, and table column headers (never a decorative "STEP 01" style numbered label). Pair with `uppercase` - `text-transform` is not expressible as a font-size modifier, so the utility carries size, weight and tracking and the transform sits beside it.

Two steps are defined but not yet applied anywhere: **Label** (table headers
currently render at Body/UI size with no transform) and **Narrative** (report
prose currently uses Tailwind's default scale). Both are recorded here because
the ramp is the specification; adopting them at those call sites is a visible
change to the interface, not a refactor, and has not been made.

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

### Pagination (result lists)

Result lists here are bounded by the DATA, not by the code. Measured on 7,680
rows of district-crop statistics, one agriculture run produced 1,328 findings
(939 of them warnings) and 655 filter chips - rendered whole, a single card ran
for thousands of rows and the only way to learn how much was there was to
scroll to the end of it.

- **Shape:** a divider, then the range and total on the left, `Prev` / `Page N
  of M` / `Next` on the right. Standard control treatment (4px radius, `Border
  Strong` stroke) - it is navigation, not a feature, and takes no accent fill.
- **Position:** below its list, inside the same card. Paging scrolls that card
  back into view, so the next page is read from its start rather than its end.
- **Page sizes:** 10 for rich multi-line findings, 25 for table rows, 48 for
  chips. Chosen so one page is about one screen of that content.

- **Rule (Stated-Total Rule):** a pager always states the FULL count, never
  just the page. This system's premise is that every warning reaches a person;
  a control that showed ten warnings while silently holding back 929 would
  break that promise rather than tidy the screen. Hiding rows is only
  acceptable while the reader can see how many are hidden.

- **Rule (No-Pager-Without-Overflow Rule):** a list that already fits renders
  with no pager at all. A disabled `Page 1 of 1` above a count of four is
  chrome reporting its own irrelevance.

- **Rule (Bounded-By-Data Rule):** only lists whose length comes from the data
  get a pager. The audit trace (one row per graph node, max 7 measured) and the
  egress table (one row per outbound call site, max 5) are bounded by the code
  instead, so they were deliberately left unpaged - a pager that can never
  render is dead UI, and shipping one implies a scale problem that does not
  exist.

### Run Picker (signature component)

The app's answer to "which run is this about," used identically on Ask and Predict. A labelled `<select>` of recent completed runs (`Run #48 - orders.csv - completed 9h ago`) paired with a narrow free-text field for a run number, so selection is the primary act and typing is the fallback. Beneath it, the selected run's own column names render as quiet `Data`-font chips.

- **Shape/colors:** standard input treatment (4px radius, `Border Strong` stroke, `Surface` fill) - it is a form control, not a feature card.
- **Rule:** a run is CHOSEN, never transcribed. No surface in this system asks a human to retype an identifier the app already knows, and no surface asks for a raw UUID.

### Navigation
- **Style:** fixed left sidebar, `nav-bg` (#141922) background, `nav-ink` (#B8C0CC) default label color.
- **Active state:** `nav-ink-active` (white) text and icon, subtle background lift (`rgba(255,255,255,0.08)`) - text/icon color change plus tone carries the state; no border accent bar (a decorative color bar is refused elsewhere in this system, so the nav does not get an exception) and never a solid filled pill in the accent color (that reads as a button, not a location).
- **Hover:** background lift only, no color change until active.

## Themes

Three surfaces share one information architecture. A theme is ONE
`data-theme` attribute on `<html>` swapping a set of CSS custom properties
(`frontend/src/index.css`); no component branches on it, and no theme
changes what a screen says, emphasises, or lets a human do. Everything
above this section describes the **Default** theme, which is the baseline
the other two override.

### Default - OPERATE (ships as the default)
The Instrument Panel exactly as documented above. Flat, bordered, one
accent, no gradient, no translucency, no glow.

### Dark - OPERATE
The same instrument in a dark room. Values are re-derived for a dark
ground rather than inverted, so status keeps both meaning and contrast.
Still no glow, gradient, or translucency: `--panel-filter`, `--glow` and
`--page-backdrop` all resolve to `none`.

- Ground `#0F1319`, panels `#171C24`, inset `#1E242E`, nav `#0B0E13`
- Text `#E8ECF2` / `#B3BCC9` / `#8D97A5`
- Accent `#3FC3BD` (the teal, lifted for a dark ground); status
  `#56D68A` / `#FF8A8A` / `#E9B455` / `#8FB0FF`

### Aurora - EXPERIENCE, opt-in
Exists to look striking in a live demo. **Never the default and never
auto-selected** - first-visit detection resolves only to Default or Dark.

Aurora knowingly adopts three anti-references the operate surfaces refuse:
a violet-family gradient ground, glassmorphic translucency, and glow. The
exception is scoped to this theme and recorded in PRODUCT.md so it reads
as a decision rather than drift. Everything else on the refusal list still
binds here - and the ban on motion implying activity is absolute in all
three: the glow is a static `box-shadow`, nothing pulses.

- **Ground:** a fixed three-stop gradient, deep teal to indigo to violet -
  `#07313A` -> `#141A4D` -> `#2E1A54`, lit by two radial washes
  (`rgba(7,74,87,0.55)` top-left, `rgba(62,27,104,0.55)` upper-right -
  dimmed from their first values because the brighter top-left lobe sat
  behind the sidebar glass and dragged worst-case text contrast down).
  These five values exist only as `--page-backdrop` and are part of the
  system, not stray literals.
- **Panels:** `rgba(11,16,32,0.72)` plus `blur(18px) saturate(135%)`,
  applied through a single `.panel` class - never globally, because a
  backdrop-filter on every element is a real performance cost and would
  blur things that are not panels.
- **Edges:** a light hairline (`rgba(150,200,255,0.16)`) reads as a lit rim
  on dark glass, where a dark border would vanish.
- **Accents:** cyan `#5EE0F5` (actions, links, active state) and magenta
  `#FF86DD` - the only theme where `--accent-secondary` is a genuine
  second voice rather than an alias of the first.
- **Glow:** `--glow` is applied to exactly two things, the primary action
  and the current nav item, so a demo audience can find both instantly.

### Named Rules

**The Skin-Not-Surgery Rule.** A theme may change how a surface looks. It
may never change what it says, what it emphasises, or what it lets a human
do. Every E2E spec runs unmodified against all three themes; a spec that
fails in one theme means that theme made a structural change, and the
theme is wrong.

**The Substantive Tint Rule.** A status tint is a SURFACE, not a wash. On
a gradient ground a 15%-alpha tint let the violet through and a status
panel read as decoration - which is precisely what the Quality Context
panel must never do. Aurora's tints are therefore dark, hue-carrying fills
at 0.92 alpha, keeping every status label at 7.4:1 or better.

**The Measured-Not-Eyeballed Rule.** No theme ships on a colour that was
judged by eye. `frontend/scripts/contrast-audit.mjs` measures every text
node on every page in every theme by two independent methods - alpha
compositing, and dominant-pixel sampling of a real screenshot for any
surface with a gradient, translucency, or backdrop blur - and takes the
worse of the two. A frosted panel can never pass on a computed number
while failing on screen.

### Measured contrast (worst case per theme, WCAG AA needs 4.5:1)

Every text node on Sources, Approvals, Reports, Audit, Ask and Predict, in
all three themes: **0 failures** (338 / 337 / 337 nodes measured). Aurora
is measured against the LIGHTEST point of its gradient behind a frosted
panel, which is the worst case for light text.

| Role | Default | Dark | Aurora |
| --- | --- | --- | --- |
| Body text on a panel | 16.1 | 14.4 | 15.7 |
| Muted text | 8.2 | 8.9 | 10.4 |
| Faint text (ids) on inset | 4.6 | 5.3 | 7.0 |
| Accent / link | 6.4 | 7.9 | 11.4 |
| Success label on its tint | 4.6 | 8.5 | 10.6 |
| Danger label on its tint | 5.6 | 7.4 | 7.5 |
| Caution label on its tint | 4.6 | 8.2 | 8.9 |
| Info label on its tint | 7.5 | 7.6 | 8.0 |
| Label on a filled accent | 5.9 | 8.9 | 10.6 |

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
