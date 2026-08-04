"""The Narrative Agent's structural contracts.

Every other LLM component in this project (the Diagnostic Agent) is
constrained by a closed enum with a deterministic gate behind it - the model
picks from a fixed vocabulary, and a downstream check decides what happens
next. Prose has no enum to hide behind. The structural move here is
different but the same in spirit: split generation into two stages joined by
a validated, non-empty-by-construction intermediate (GroundedClaim), so the
stage that writes free text (stage 2) can never see - and therefore never
invent - anything the stage that IS checked against the data (stage 1)
didn't already establish.

CLOSED ENUMS THROUGHOUT, same as app/exploration/findings.py. Field names
here describe statistical relationships and generation mechanics only -
nothing in this module authorizes causal phrasing; app/narrative/postchecks.py
is what actually enforces that on the generated text.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class ClaimValue(BaseModel):
    """One exact numeric value a GroundedClaim rests on - a label for what
    it is (not free text describing it, just an identifying tag) and the
    number itself, exactly as it appears in the finding's own payload or
    evidence block."""

    label: str
    value: float


class GroundedClaim(BaseModel):
    """Stage 1's output unit. `finding_ids` non-empty is enforced
    structurally (Field(min_length=1)) - the model cannot construct a claim
    that cites nothing, the same way FixAction cannot hold a free-text
    action. Referencing a finding_id absent from the input findings is NOT
    something a bare Pydantic field can check (it needs the actual input),
    so that half of the validation rule lives in
    app/narrative/grounding.py::filter_grounded_claims, run immediately
    after this model is parsed - "reject at parse time" describes the whole
    two-part check, not just the part a single model class can enforce
    alone."""

    # Assigned after filter_grounded_claims validates the raw list against
    # the run's actual finding ids - not by the model, for the same reason
    # Finding.id isn't assigned by its own analysis module (see
    # app/exploration/findings.py). Empty only in the instant between
    # parsing the LLM's raw response and that validation pass.
    claim_id: str = ""
    claim_text: str
    finding_ids: list[str] = Field(min_length=1)
    values: list[ClaimValue] = Field(default_factory=list)


class GroundedClaimsResponse(BaseModel):
    """Stage 1's LLMClient.complete() response_schema."""

    claims: list[GroundedClaim] = Field(default_factory=list)


class Recommendation(BaseModel):
    """Confined to its own field on NarrativeProse/NarrativeReport - never
    inline in the narrative body - and required to name the claim that
    motivated it, so a recommendation can never appear ungrounded in
    anything the findings actually said."""

    text: str
    claim_id: str


class NarrativeProse(BaseModel):
    """Stage 2's LLMClient.complete() response_schema. `report_text` is the
    narrative body ONLY - recommendations live in their own field
    structurally, not because the prompt asks nicely for a separate
    section."""

    report_text: str
    recommendations: list[Recommendation] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Part 2: deterministic post-checks
# ---------------------------------------------------------------------------


class PostCheckKind(str, Enum):
    NUMBER_FIDELITY = "number_fidelity"
    CAUSAL_LANGUAGE = "causal_language"
    CLAIM_COVERAGE = "claim_coverage"


class PostCheckIssue(BaseModel):
    """Precisely what failed and where - never just a boolean."""

    kind: PostCheckKind
    detail: str


class PostCheckOutcome(BaseModel):
    kind: PostCheckKind
    passed: bool
    issues: list[PostCheckIssue] = Field(default_factory=list)


class PostCheckAttempt(BaseModel):
    """One full pass of all three checks against one generated prose
    candidate. attempt=1 is the first generation; attempt=2 (if it exists)
    is the one-and-only regeneration Part 2 permits before falling back to
    the template."""

    attempt: int
    outcomes: list[PostCheckOutcome]

    @property
    def passed(self) -> bool:
        return all(outcome.passed for outcome in self.outcomes)

    @property
    def failed_kinds(self) -> list[PostCheckKind]:
        return [outcome.kind for outcome in self.outcomes if not outcome.passed]


# ---------------------------------------------------------------------------
# Part 3: charts
# ---------------------------------------------------------------------------


class ChartType(str, Enum):
    """Selected deterministically by data shape (app/narrative/charts.py) -
    never by the LLM, and never present anywhere in a prompt or an LLM
    response schema."""

    LINE = "line"
    BAR = "bar"
    HISTOGRAM = "histogram"
    SCATTER = "scatter"


class ChartRef(BaseModel):
    chart_id: str
    chart_type: ChartType
    finding_ids: list[str] = Field(min_length=1)
    title: str
    # A Plotly figure, serialized via `fig.to_plotly_json()` - a plain,
    # JSON-serializable dict (no binary blobs), reconstructable with
    # `plotly.graph_objects.Figure(**figure_json)`.
    figure_json: dict


# ---------------------------------------------------------------------------
# Part 6: the assembled report
# ---------------------------------------------------------------------------


class GenerationMode(str, Enum):
    LLM = "llm"
    TEMPLATE = "template"


class NarrativeReport(BaseModel):
    """The Narrative Agent's full output. `quality_context_summary` is
    ALWAYS deterministically rendered (app/narrative/quality.py), never
    LLM-authored, in both generation modes - that's what makes Part 5's
    requirement ("the caveat cannot be separated from the content") a
    structural fact about this model rather than a prompt instruction that
    could be skipped."""

    run_id: str
    generation_mode: GenerationMode
    quality_context_summary: str
    narrative_text: str
    recommendations: list[Recommendation] = Field(default_factory=list)
    grounded_claims: list[GroundedClaim] = Field(default_factory=list)
    charts: list[ChartRef] = Field(default_factory=list)
    post_check_history: list[PostCheckAttempt] = Field(default_factory=list)
    fallback_reason: str | None = None  # set only when generation_mode == template
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def rendered_text(self) -> str:
        """The single flat document persisted as Report.narrative_text -
        quality context FIRST, then the narrative body, then recommendations
        as their own labelled section. Never reordered per generation_mode:
        template reports use this exact same assembly (Part 5)."""
        sections = [
            "## Data Quality Context",
            self.quality_context_summary,
            "",
            self.narrative_text,
        ]
        if self.recommendations:
            sections += ["", "## Recommendations"]
            sections += [f"- {rec.text} (see {rec.claim_id})" for rec in self.recommendations]
        if self.generation_mode == GenerationMode.TEMPLATE:
            sections += ["", f"_This report was generated by a deterministic template, not an LLM ({self.fallback_reason})._"]
        return "\n".join(sections)


class ClaimsOutcome(BaseModel):
    """Stage 1's result, mirroring app/diagnosis/agent.py::DiagnosisOutcome's
    shape - `claims` is None only when the LLM stage failed entirely
    (quota exhausted / malformed output survived the one repair attempt),
    which the pipeline treats identically to "LLM unavailable"."""

    model_config = {"arbitrary_types_allowed": True}

    claims: list[GroundedClaim] | None
    source: Literal["llm", "escalated_parse_failure", "escalated_quota_exhausted", "escalated_unavailable"]
    rejected_reasons: list[str] = Field(default_factory=list)
    model_name: str | None = None
    temperature: float | None = None
    #: Short, user-facing phrase naming WHY the stage failed ("the model's
    #: response was cut off before it finished"). Separate from
    #: rejected_reasons, which holds the full technical detail for logs and
    #: the audit trace: a report reader needs the cause, not a stack of
    #: provider enums, and "escalated_parse_failure" alone told them
    #: neither.
    failure_summary: str | None = None


class ProseOutcome(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    prose: NarrativeProse | None
    source: Literal["llm", "escalated_parse_failure", "escalated_quota_exhausted", "escalated_unavailable"]
    model_name: str | None = None
    temperature: float | None = None
    rejected_reasons: list[str] = Field(default_factory=list)
    failure_summary: str | None = None
