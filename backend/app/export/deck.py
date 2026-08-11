"""Renders a run's ALREADY-PERSISTED artifacts as a PowerPoint deck.

THIS IS A RENDERER, NOT A GENERATOR. Every sentence in the output deck was
already written to the database by the run itself - the narrative, the
grounded claims, the analytics findings and their refusals, the model's
scores, the audit trail. Nothing here computes a number, summarises,
rephrases, or infers. There is no LLM on this path and no place to put one.

Two consequences worth stating, because they are the reason the deck can be
trusted at all:

  - A deck cannot disagree with the dashboard. Both read the same rows, and
    the charts are rendered from the same figure JSON the browser is handed
    (app/export/chart_images.py).
  - A deck cannot claim more than the run did. If the run produced no model,
    the deck says so on its own slide; it does not quietly omit the section
    and leave a reader to assume there was nothing to say.

NO CAUSAL VOCABULARY. Every fixed string below is checked by a test against
the same lexicon app/narrative/postchecks.py enforces on generated prose -
the chrome is held to the standard the content is.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from datetime import datetime

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Inches, Pt

from app.export.chart_images import render_chart_png

SLIDE_WIDTH = Inches(13.333)
SLIDE_HEIGHT = Inches(7.5)

INK = RGBColor(0x12, 0x16, 0x1C)
INK_MUTED = RGBColor(0x4B, 0x55, 0x63)
ACCENT = RGBColor(0x0B, 0x6E, 0x6E)
CAUTION = RGBColor(0x95, 0x64, 0x00)

BODY_LEFT = Inches(0.9)
BODY_TOP = Inches(1.6)
BODY_WIDTH = Inches(11.5)


@dataclass
class DeckSources:
    """Everything the deck is allowed to draw on. Assembled by the caller
    from committed rows; this module never queries."""

    run_number: int | None
    run_id: str
    run_status: str
    source_label: str
    started_at: datetime | None
    generation_mode: str | None
    narrative_text: str | None
    grounded_claims: list[dict]
    chart_refs: list[dict]
    analytics: dict | None
    model_runs: list[dict]
    trace: list[dict]
    validation_events: list[dict]


def _text_slide(prs: Presentation, title: str):
    """A blank layout with our own title box. The built-in layouts carry
    placeholder styling we would have to fight on every slide."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    box = slide.shapes.add_textbox(BODY_LEFT, Inches(0.55), BODY_WIDTH, Inches(0.8))
    frame = box.text_frame
    frame.word_wrap = True
    para = frame.paragraphs[0]
    run = para.add_run()
    run.text = title
    run.font.size = Pt(30)
    run.font.bold = True
    run.font.color.rgb = INK
    return slide


def _body(slide, lines: list[tuple[str, int, RGBColor | None]], top=BODY_TOP, height=Inches(5.2)):
    """Paragraphs as (text, size, colour). One helper so every content slide
    shares a type scale instead of each inventing one."""
    box = slide.shapes.add_textbox(BODY_LEFT, top, BODY_WIDTH, height)
    frame = box.text_frame
    frame.word_wrap = True
    first = True
    for text, size, colour in lines:
        para = frame.paragraphs[0] if first else frame.add_paragraph()
        first = False
        para.space_after = Pt(8)
        run = para.add_run()
        run.text = text
        run.font.size = Pt(size)
        run.font.color.rgb = colour or INK
    return frame


def _title_slide(prs: Presentation, s: DeckSources) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    box = slide.shapes.add_textbox(BODY_LEFT, Inches(2.4), BODY_WIDTH, Inches(2.6))
    frame = box.text_frame
    frame.word_wrap = True

    para = frame.paragraphs[0]
    run = para.add_run()
    run.text = s.source_label
    run.font.size = Pt(40)
    run.font.bold = True
    run.font.color.rgb = INK

    label = f"Run #{s.run_number}" if s.run_number is not None else f"Run {s.run_id[:8]}"
    when = s.started_at.strftime("%d %b %Y, %H:%M") if s.started_at else "date not recorded"
    mode = s.generation_mode or "no report"
    sub = frame.add_paragraph()
    sub.space_before = Pt(14)
    run = sub.add_run()
    run.text = f"{label}  ·  {when}  ·  narrative: {mode}"
    run.font.size = Pt(18)
    run.font.color.rgb = INK_MUTED

    # A template-generated report exports as a template-generated deck, and
    # says so on the cover. Reading a deterministic fallback as though a
    # model had written it is exactly the confusion this label prevents.
    if s.generation_mode == "template":
        note = frame.add_paragraph()
        note.space_before = Pt(10)
        run = note.add_run()
        run.text = (
            "This report was written by the deterministic template, not a language model - "
            "a fully supported mode, and the same findings either way."
        )
        run.font.size = Pt(13)
        run.font.color.rgb = CAUTION


def _quality_slide(prs: Presentation, s: DeckSources) -> None:
    """ALWAYS slide 2. Never an appendix, never dropped.

    A fluent report read as authoritative over repaired or unvalidated data
    is the single most harmful thing this system can hand someone, and a
    deck travels further from its context than a screen does.
    """
    slide = _text_slide(prs, "Data quality context")
    lines: list[tuple[str, int, RGBColor | None]] = []

    quality = _quality_paragraph(s.narrative_text)
    if quality:
        lines.append((quality, 15, INK))

    unresolved = [e for e in s.validation_events if e.get("state") == "awaiting_approval"]
    fixed = [e for e in s.validation_events if e.get("action_taken")]
    reverted = [e for e in s.validation_events if e.get("reversal")]

    lines.append((f"Validation events recorded: {len(s.validation_events)}", 15, INK_MUTED))
    lines.append((f"Still awaiting a human decision: {len(unresolved)}", 15, CAUTION if unresolved else INK_MUTED))
    lines.append((f"Fixes applied automatically: {len(fixed)}", 15, INK_MUTED))
    lines.append((f"Fixes reverted after post-condition checks: {len(reverted)}", 15, INK_MUTED))
    if s.run_status != "completed":
        lines.append((f"This run is {s.run_status} - it did not finish.", 15, CAUTION))
    if not quality and not s.validation_events:
        lines.append(
            ("No data-quality issues were recorded for this run, and no baseline caveat applies.", 15, INK_MUTED)
        )
    _body(slide, lines)


def _quality_paragraph(narrative_text: str | None) -> str:
    """The quality section as the report itself rendered it.

    Read out of the persisted narrative rather than re-derived, so the deck
    repeats the run's own wording instead of composing a second, subtly
    different account of the same facts.
    """
    if not narrative_text or "## Data Quality Context" not in narrative_text:
        return ""
    after = narrative_text.split("## Data Quality Context", 1)[1]
    block = after.split("\n\n", 1)[0]
    return block.strip()


def _narrative_slides(prs: Presentation, s: DeckSources) -> None:
    if not s.narrative_text:
        slide = _text_slide(prs, "Narrative")
        _body(slide, [("This run produced no narrative report, so there is nothing to reproduce here.", 15, INK_MUTED)])
        return

    body = s.narrative_text
    if "## Data Quality Context" in body:
        after = body.split("## Data Quality Context", 1)[1]
        parts = after.split("\n\n")
        body = "\n\n".join(parts[1:])
    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip() and not p.strip().startswith("##")]

    # Grounded claims become SPEAKER NOTES, not body text. Each is the
    # traceable evidence behind a sentence on the slide; a presenter needs
    # them to hand, an audience does not need them read aloud.
    notes = "\n".join(
        f"{c.get('claim_id', '?')}: {c.get('claim_text', '')} [cites {', '.join(c.get('finding_ids') or [])}]"
        for c in s.grounded_claims
    )

    per_slide = 3
    chunks = [paragraphs[i : i + per_slide] for i in range(0, len(paragraphs), per_slide)] or [[]]
    for index, chunk in enumerate(chunks):
        slide = _text_slide(prs, "Narrative" if index == 0 else f"Narrative ({index + 1})")
        _body(slide, [(p, 15, INK) for p in chunk])
        if notes:
            slide.notes_slide.notes_text_frame.text = (
                f"Grounded claims behind this report ({len(s.grounded_claims)}):\n{notes}"
            )


def _chart_slides(prs: Presentation, s: DeckSources) -> None:
    if not s.chart_refs:
        slide = _text_slide(prs, "Charts")
        _body(
            slide,
            [("This run produced no charts - the exploration findings had no shape a deterministic chart fits.", 15, INK_MUTED)],
        )
        return

    for chart in s.chart_refs:
        title = str(chart.get("title") or "Chart")
        slide = _text_slide(prs, title)
        png = render_chart_png(chart.get("figure_json") or {}, chart.get("chart_type"))
        if png is None:
            _body(slide, [("This chart could not be rendered as an image for the deck.", 15, INK_MUTED)])
            continue
        slide.shapes.add_picture(io.BytesIO(png), Inches(1.4), Inches(1.5), width=Inches(10.5))


def _analytics_slides(prs: Presentation, s: DeckSources) -> None:
    if not s.analytics:
        slide = _text_slide(prs, "Business analytics")
        _body(slide, [("No business analyses were computed for this run.", 15, INK_MUTED)])
        return

    results = s.analytics.get("results") or []
    applicability = s.analytics.get("applicability") or []
    ran = [r for r in results if r.get("ran")]
    not_run = [a for a in applicability if not a.get("applicable")]

    slide = _text_slide(prs, "Business analytics")
    lines: list[tuple[str, int, RGBColor | None]] = []
    definition = (s.analytics.get("value_definition") or {}).get("note")
    if definition:
        lines.append((definition, 13, INK_MUTED))
    if ran:
        for result in ran:
            findings = result.get("findings") or []
            lines.append((f"{result.get('analysis')}: {len(findings)} finding(s)", 15, INK))
    else:
        lines.append(("No analysis was applicable to this run's data.", 15, INK_MUTED))
    _body(slide, lines)

    # THE NOT-APPLICABLE LIST SHIPS IN THE DECK. It is the honesty of the
    # feature: a reader looking at three results has to be able to tell that
    # the other four were ineligible, and why, rather than concluding the
    # agent found nothing worth reporting.
    slide = _text_slide(prs, "Analyses that did not apply")
    if not_run:
        lines = [("Each needs a specific data shape. These did not run, and this is the requirement each was missing.", 13, INK_MUTED)]
        for entry in not_run:
            lines.append((str(entry.get("analysis")), 15, INK))
            for requirement in entry.get("missing_requirements") or []:
                lines.append((f"    {requirement}", 13, INK_MUTED))
    else:
        lines = [("Every analysis was applicable to this run's data.", 15, INK_MUTED)]
    _body(slide, lines)


def _model_slides(prs: Presentation, s: DeckSources) -> None:
    """A run with no model gets a slide SAYING so.

    Dropping the section would let a reader assume modelling was never
    attempted, when it may have been attempted and honestly refused.
    """
    slide = _text_slide(prs, "Model results")
    if not s.model_runs:
        _body(
            slide,
            [
                ("No forecast or model was requested for this run.", 15, INK),
                ("Modelling is asked for explicitly on the Predict screen; it is not part of an ingest.", 13, INK_MUTED),
            ],
        )
        return

    lines: list[tuple[str, int, RGBColor | None]] = []
    for model in s.model_runs:
        target = model.get("target_column") or model.get("question") or "model"
        lines.append((f"{target} ({model.get('task_type') or 'task not recorded'})", 17, INK))

        metric = model.get("out_of_sample_metric") or "score"
        score = model.get("out_of_sample_score")
        baseline_text = _baseline_text(model.get("baseline_scores"))
        # The model's score NEXT TO its baseline, always. A score with no
        # baseline beside it cannot be judged.
        lines.append((f"    out-of-sample {metric}: {_fmt(score)}   ·   baseline: {baseline_text}", 14, INK))

        interval = model.get("prediction_interval") or {}
        if isinstance(interval, dict) and interval.get("lower") is not None:
            level = interval.get("confidence_level")
            level_text = f" at {float(level) * 100:.0f}% confidence" if isinstance(level, (int, float)) else ""
            lines.append(
                (f"    prediction interval: {_fmt(interval.get('lower'))} to {_fmt(interval.get('upper'))}{level_text}", 13, INK_MUTED)
            )
        elif interval:
            lines.append((f"    prediction interval: {json.dumps(interval)[:160]}", 13, INK_MUTED))

        excluded = model.get("excluded_features") or []
        if excluded:
            lines.append((f"    excluded features ({len(excluded)}):", 13, INK_MUTED))
            for item in excluded[:6]:
                if isinstance(item, dict):
                    lines.append((f"        {item.get('feature')} - {item.get('reason')}", 12, INK_MUTED))
                else:
                    lines.append((f"        {item}", 12, INK_MUTED))

        if model.get("state") == "escalated":
            lines.append(
                (f"    Escalated: {model.get('escalation_reason') or 'no trustworthy model to report'}", 14, CAUTION)
            )
    _body(slide, lines)


def _baseline_text(baselines) -> str:
    """The baselines as app/modeling/ actually persists them: a LIST of
    {model_family, metric, out_of_sample_score}, not a mapping. Assuming the
    mapping shape is what made the first deck build fail - so this handles
    the real shape and tolerates the other rather than trusting either."""
    if not baselines:
        return "no baseline recorded"
    if isinstance(baselines, dict):
        return ", ".join(f"{k}={_fmt(v)}" for k, v in baselines.items())
    parts = []
    for entry in baselines:
        if isinstance(entry, dict):
            family = entry.get("model_family") or entry.get("name") or "baseline"
            parts.append(f"{family}={_fmt(entry.get('out_of_sample_score'))}")
        else:
            parts.append(str(entry))
    return ", ".join(parts) or "no baseline recorded"


def _fmt(value) -> str:
    if isinstance(value, (int, float)):
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    return "not recorded" if value is None else str(value)


def _audit_slide(prs: Presentation, s: DeckSources) -> None:
    slide = _text_slide(prs, "Audit trail")
    lines: list[tuple[str, int, RGBColor | None]] = []
    if s.trace:
        lines.append(("Nodes visited, and the edge taken out of each:", 13, INK_MUTED))
        for entry in s.trace:
            edge = entry.get("edge_taken") or "-"
            lines.append((f"    {entry.get('node')}  ->  {edge}", 14, INK))
    else:
        lines.append(("No agent trace was recorded for this run.", 15, INK_MUTED))

    resolved = [e for e in s.validation_events if e.get("resolved_by")]
    if resolved:
        lines.append(("Decisions taken by a human:", 13, INK_MUTED))
        for event in resolved:
            lines.append(
                (f"    {event.get('rule_failed')} - {event.get('action_taken') or 'resolved'} by {event.get('resolved_by')}", 13, INK)
            )
    else:
        lines.append(("No escalation on this run required a human decision.", 14, INK_MUTED))
    _body(slide, lines)


def build_deck(sources: DeckSources) -> bytes:
    """The deck, in a fixed order. Quality context is ALWAYS slide 2."""
    prs = Presentation()
    prs.slide_width = SLIDE_WIDTH
    prs.slide_height = SLIDE_HEIGHT

    _title_slide(prs, sources)
    _quality_slide(prs, sources)
    _narrative_slides(prs, sources)
    _chart_slides(prs, sources)
    _analytics_slides(prs, sources)
    _model_slides(prs, sources)
    _audit_slide(prs, sources)

    buffer = io.BytesIO()
    prs.save(buffer)
    return buffer.getvalue()
