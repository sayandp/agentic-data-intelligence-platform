"""Part 4: the deterministic fallback. Runs when the LLM is unavailable,
quota is exhausted, stage 1 grounds nothing usable, or stage 2's output
fails post-checks twice - never leaves a run without a report.

Produces exactly ONE GroundedClaim per Finding, deterministically templated
from that finding's own payload and evidence - trivially, perfectly
grounded, because the claim IS the finding restated as a sentence. Less
readable than an LLM narrative by design; that trade is the whole point of
Part 4 (the system loses fluency here, never correctness).
"""

from __future__ import annotations

from app.exploration.findings import (
    CardinalityNotePayload,
    CategoricalSummaryPayload,
    CorrelationPayload,
    DatetimeSummaryPayload,
    DistributionShapePayload,
    ExplorationFindings,
    Finding,
    MissingPatternPayload,
    NumericSummaryPayload,
    OutlierClusterPayload,
    TrendPayload,
)
from app.narrative.models import ClaimValue, GroundedClaim


def _numeric_summary_claim(finding: Finding, payload: NumericSummaryPayload) -> tuple[str, list[ClaimValue]]:
    column = finding.columns[0] if finding.columns else "column"
    parts = [f"{column}: {payload.count} value(s), {payload.null_rate:.1%} null"]
    values = [ClaimValue(label="count", value=float(payload.count)), ClaimValue(label="null_rate", value=payload.null_rate)]
    if payload.mean is not None:
        parts.append(f"mean {payload.mean:.6g}")
        values.append(ClaimValue(label="mean", value=payload.mean))
    if payload.std is not None:
        parts.append(f"std {payload.std:.6g}")
        values.append(ClaimValue(label="std", value=payload.std))
    if payload.min is not None and payload.max is not None:
        parts.append(f"range [{payload.min:.6g}, {payload.max:.6g}]")
        values.append(ClaimValue(label="min", value=payload.min))
        values.append(ClaimValue(label="max", value=payload.max))
    return ", ".join(parts) + ".", values


def _categorical_summary_claim(finding: Finding, payload: CategoricalSummaryPayload) -> tuple[str, list[ClaimValue]]:
    column = finding.columns[0] if finding.columns else "column"
    text = (
        f"{column}: {payload.cardinality} distinct value(s) across {payload.count} row(s) "
        f"({payload.null_rate:.1%} null); most common is {payload.mode!r}."
    )
    values = [
        ClaimValue(label="cardinality", value=float(payload.cardinality)),
        ClaimValue(label="count", value=float(payload.count)),
        ClaimValue(label="null_rate", value=payload.null_rate),
    ]
    return text, values


def _datetime_summary_claim(finding: Finding, payload: DatetimeSummaryPayload) -> tuple[str, list[ClaimValue]]:
    column = finding.columns[0] if finding.columns else "column"
    text = f"{column}: {payload.count} value(s), spanning {payload.span_days:.1f} day(s) from {payload.min} to {payload.max}"
    values = [ClaimValue(label="count", value=float(payload.count))]
    if payload.span_days is not None:
        values.append(ClaimValue(label="span_days", value=payload.span_days))
    if payload.inferred_frequency:
        text += f", inferred frequency {payload.inferred_frequency!r}"
    return text + ".", values


def _correlation_claim(finding: Finding, payload: CorrelationPayload) -> tuple[str, list[ClaimValue]]:
    text = (
        f"{payload.column_a} and {payload.column_b} show a {payload.method.value} correlation of "
        f"{payload.coefficient:.6g} (n={finding.evidence.sample_size}"
    )
    values = [
        ClaimValue(label="coefficient", value=payload.coefficient),
        ClaimValue(label="sample_size", value=float(finding.evidence.sample_size)),
    ]
    if finding.evidence.p_value is not None:
        text += f", p={finding.evidence.p_value:.6g}"
        values.append(ClaimValue(label="p_value", value=finding.evidence.p_value))
    return text + ").", values


def _outlier_claim(finding: Finding, payload: OutlierClusterPayload) -> tuple[str, list[ClaimValue]]:
    text = (
        f"{payload.column}: {payload.count} outlier value(s) detected outside "
        f"[{payload.lower_bound:.6g}, {payload.upper_bound:.6g}] ({payload.method.value.upper()} method)."
    )
    values = [
        ClaimValue(label="count", value=float(payload.count)),
        ClaimValue(label="lower_bound", value=payload.lower_bound),
        ClaimValue(label="upper_bound", value=payload.upper_bound),
    ]
    return text, values


def _trend_claim(finding: Finding, payload: TrendPayload) -> tuple[str, list[ClaimValue]]:
    text = (
        f"{payload.numeric_column} shows a {payload.direction.value} trend over {payload.datetime_column} "
        f"from {payload.start} to {payload.end}"
    )
    values = [ClaimValue(label="slope", value=payload.slope)]
    if finding.evidence.r_squared is not None:
        text += f" (R-squared={finding.evidence.r_squared:.6g})"
        values.append(ClaimValue(label="r_squared", value=finding.evidence.r_squared))
    if payload.seasonality_detected:
        text += ", with seasonality also detected"
        if payload.seasonality_period is not None:
            values.append(ClaimValue(label="seasonality_period", value=float(payload.seasonality_period)))
    return text + ".", values


def _distribution_shape_claim(finding: Finding, payload: DistributionShapePayload) -> tuple[str, list[ClaimValue]]:
    text = f"{payload.column}: distribution is {payload.normality_indication.value.replace('_', ' ')}"
    values = []
    if payload.skew is not None:
        text += f", skew={payload.skew:.6g}"
        values.append(ClaimValue(label="skew", value=payload.skew))
    text += f", modality hint {payload.modality_hint.value.replace('_', ' ')}."
    return text, values


def _cardinality_note_claim(finding: Finding, payload: CardinalityNotePayload) -> tuple[str, list[ClaimValue]]:
    text = (
        f"{payload.column}: {payload.note.value.replace('_', ' ')} - {payload.cardinality} distinct value(s) "
        f"across {payload.row_count} row(s) ({payload.unique_ratio:.1%} unique)."
    )
    values = [
        ClaimValue(label="cardinality", value=float(payload.cardinality)),
        ClaimValue(label="row_count", value=float(payload.row_count)),
        ClaimValue(label="unique_ratio", value=payload.unique_ratio),
    ]
    return text, values


def _missing_pattern_claim(finding: Finding, payload: MissingPatternPayload) -> tuple[str, list[ClaimValue]]:
    text = (
        f"Null values in {' and '.join(payload.columns)} are {payload.relationship.value.replace('_', ' ')} "
        f"(support={payload.support}, confidence={payload.confidence:.6g})."
    )
    values = [ClaimValue(label="support", value=float(payload.support)), ClaimValue(label="confidence", value=payload.confidence)]
    return text, values


_CLAIM_BUILDERS = {
    NumericSummaryPayload: _numeric_summary_claim,
    CategoricalSummaryPayload: _categorical_summary_claim,
    DatetimeSummaryPayload: _datetime_summary_claim,
    CorrelationPayload: _correlation_claim,
    OutlierClusterPayload: _outlier_claim,
    TrendPayload: _trend_claim,
    DistributionShapePayload: _distribution_shape_claim,
    CardinalityNotePayload: _cardinality_note_claim,
    MissingPatternPayload: _missing_pattern_claim,
}


def build_template_claims(findings: ExplorationFindings) -> list[GroundedClaim]:
    """One claim per finding, in finding order - deterministic, and
    trivially grounded (finding_ids=[finding.id], values taken verbatim
    from that finding's own payload/evidence)."""
    claims: list[GroundedClaim] = []
    for finding in findings.findings:
        builder = _CLAIM_BUILDERS.get(type(finding.payload))
        if builder is None:
            continue
        text, values = builder(finding, finding.payload)
        claims.append(GroundedClaim(claim_id=f"claim-{len(claims)}", claim_text=text, finding_ids=[finding.id], values=values))
    return claims


def render_template_narrative(claims: list[GroundedClaim]) -> str:
    """Plain, unadorned prose: one line per claim. Deliberately less
    readable than an LLM narrative - perfectly grounded is the trade."""
    if not claims:
        return "No findings were available to report for this run."
    return "\n".join(f"- {claim.claim_text}" for claim in claims)
