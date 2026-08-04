# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Two audiences, both reading fast and needing to see WHY the system decided something, not just what it decided:

- **A data analyst** operating the system day to day - registering sources, watching ingests, resolving escalated data-quality issues, reading generated reports, and asking follow-up questions or requesting forecasts.
- **An academic examination panel** watching a live demo of the system - evaluating it as a working piece of engineering, not a pitch. They need the reasoning legible at a glance: why a value was auto-fixed vs. escalated, why a claim in a report is trustworthy, why a forecast beat (or didn't beat) its baseline.

## Product Purpose

An agentic data intelligence platform. It ingests business data, detects and self-heals data-quality issues where it safely can, escalates what it can't safely fix to a human with full reasoning attached, then generates grounded analytics reports, answers natural-language questions about the data, and trains forecasting/classification/regression models - all with the same discipline: never assert more confidence than the evidence actually supports.

Success is a human being able to trust (or correctly distrust) every number the system shows them, because the reasoning behind it is always one click away, never hidden.

## Positioning

The self-healing + human-escalation loop: a deterministic gate auto-applies only a matrix of proven-safe fixes; everything else - anything risky, low-confidence, or unprecedented - escalates to a human with the full diagnosis and gate reasons attached, never silently guessed at and never silently dropped. A correlated group of failures escalates as ONE decision, not one per event.

The same discipline carries into the reporting layer: narrative claims are grounded against real findings before any prose is generated (a claim must cite a real finding, checked structurally, not just prompted for), and causal language ("caused", "drove", "impact", "because of") is structurally banned even when a correlation looks tempting - enforced by deterministic post-checks on every generation, whether LLM-authored or template-authored. Data-layer restraint and reporting-layer restraint are the same principle applied twice: never claim more than the evidence shows.

## Operating Context

The core workflow: register a source (file/SQL/API) -> ingest -> validate against a baseline -> diagnose failures -> gate decides auto-fix or escalate -> (if escalated) a human resolves it on the Approvals screen, choosing from four decisions (approve the suggested fix / keep data as-is / accept as new baseline / discard the run permanently, the last being the only irreversible one) -> explore (deterministic statistical findings + chart selection) -> narrate (a grounded report, LLM-authored or template-degraded if no LLM is configured).

Independently of a specific run, the analyst can also Ask a natural-language question against the data (always shown alongside its generated code, never the answer alone) or run Predict to forecast/classify/regress a target column, with the model's own score always shown next to its trivial baseline's score for comparison.

Every decision the system or a human made about a run is visible on the Audit screen: every node the graph visited, the edge taken out of each, every gate reason, every resolution and who made it.

Long-running steps (ingest, resolving an escalation, training a model) run in the background and are polled for status - the UI always shows live elapsed-time progress, never a bare spinner with no sense of whether it's stuck.

## Capabilities and Constraints

- Deterministic wherever a decision can be made from data shape alone (chart type selection, task-type selection for modeling, the auto-fix/escalate gate's matrix). The LLM is used only where genuine language understanding is needed (diagnosing an unfamiliar failure, classifying question intent, generating prose) and its output is always structurally validated before anything downstream trusts it.
- No LLM configured is a fully supported operating mode, not a degraded error: every corruption escalates to human review, and reports fall back to a deterministic template - the platform never fails outright for lack of an LLM.
- A provisional baseline (established automatically on a source's first ingest) must be confirmed or rejected by a human before it's fully trusted the same way a human-confirmed baseline is.
- Reveal depth (how far a chain of dependent fixes is chased before escalating the rest) is capped, not unbounded.
- Terminology used consistently across the UI and this document: **run** (one ingest attempt), **validation event** (one detected data-quality failure, possibly grouped with correlated others), **escalation** (a decision routed to a human), **baseline** (the established "normal" a run is checked against), **finding** (a deterministic statistical observation from exploration), **grounded claim** (a narrative statement traceable to a real finding).

## Brand Commitments

- **Voice:** clinical, evidence-first, no hype, no marketing language. This is an operational tool, not something trying to persuade or sell.
- **Mode:** the interface operates (a working dashboard for completing tasks and reading evidence) - it never persuades. Nothing in the UI should read as marketing copy, a pitch, or a call to action beyond the task at hand.
- **Anti-references (explicitly ruled out):** purple gradients, glassmorphism, hero eyebrow chips, italic serif display type, "AI beige," nested cards, icon-tile stacks, numbered section labels, pulsing "AI is thinking" dots.

## Evidence on Hand

- A real, committed demo dataset (`backend/data/demo_full.csv`, generator at `backend/scripts/generate_demo_full.py`) with a genuine trend, a genuine correlation, a discrete rating column, and a plain continuous numeric column - built specifically so a live demo can show every deterministic chart type and a forecast that actually beats its baseline.
- A working backend test suite (490 passing tests) and Playwright E2E specs that drive the real dashboard against the real backend - no mocked screenshots, no invented metrics.
- No customer testimonials, case studies, press, or pricing exist or should be fabricated - this is an internal/demo tool, not a marketed product.

## Product Principles

1. Never assert more confidence than the evidence supports - not in a diagnosis, not in a narrative claim, not in a forecast's score. Correlation is never presented as causation, anywhere.
2. Escalate rather than silently guess. An auto-fix only ever comes from a matrix of actions already proven safe for that failure family; anything else surfaces to a human with full reasoning, never applied quietly.
3. Every number the UI shows traces to evidence a human can actually reach - a finding, a gate reason, a baseline comparison, a generated-code block. Nothing floats free of its source.
4. The reasoning trail (audit trail, gate reasons, excluded features, quality context) is a first-class product surface, not a debug afterthought bolted on later.
5. The system degrades honestly, never silently. No LLM configured, a model that doesn't beat its baseline, a report that fails its own quality checks - each of these is reported as exactly what it is, never dressed up as success.
