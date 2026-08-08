"""Runs every business analysis for one run, and assembles the findings.

Deliberately thin: detection, applicability, then each analysis in a fixed
order. An analysis that raises is captured as a not-run result with the
exception recorded - one failing analysis must never take the other six down
with it, exactly as one failing exploration analysis does not fail a run.

Finding ids are assigned HERE, after everything has run, for the same reason
ExplorationEngine assigns them centrally: no analysis module knows its own
offset into the whole run's output, and a GroundedClaim needs a stable
reference.

NO LLM ANYWHERE IN THIS PIPELINE.
"""

from __future__ import annotations

import pandas as pd

from app.analytics.applicability import AnalysisKind, build_applicability_report
from app.analytics.basket import run_market_basket
from app.analytics.cohorts import run_cohort_retention, run_historical_clv, run_retention_churn
from app.analytics.findings import BusinessAnalysisResult, BusinessAnalyticsFindings
from app.analytics.pareto import run_abc_pareto
from app.analytics.rfm import run_rfm
from app.analytics.roles import SemanticColumnDetector, apply_confirmed_roles
from app.analytics.value_basis import quantity_needs_confirmation, resolve_value_basis
from app.analytics.segmentation import run_behavioural_segmentation

#: Fixed order: simplest first, so a reader scanning the page meets the
#: cheapest-to-understand result before the most involved one.
_ANALYSES = (
    (AnalysisKind.ABC_PARETO, run_abc_pareto),
    (AnalysisKind.RFM, run_rfm),
    (AnalysisKind.COHORT_RETENTION, run_cohort_retention),
    (AnalysisKind.BEHAVIOURAL_SEGMENTATION, run_behavioural_segmentation),
    (AnalysisKind.MARKET_BASKET, run_market_basket),
    (AnalysisKind.RETENTION_CHURN, run_retention_churn),
    (AnalysisKind.HISTORICAL_CLV, run_historical_clv),
)


def run_business_analytics(
    run_id: str,
    df: pd.DataFrame,
    confirmed_roles: dict[str, str] | None = None,
) -> BusinessAnalyticsFindings:
    """`confirmed_roles` are roles a HUMAN confirmed for this run's source
    (app/models.py::ConfirmedColumnRole). They are layered on top of a full
    detection pass rather than replacing it, so the report can still explain
    what the detector saw - and so an analysis that was already applicable
    stays applicable for the reason it always was."""
    detection = apply_confirmed_roles(SemanticColumnDetector().detect(df), confirmed_roles or {}, df.columns)
    applicability = build_applicability_report(detection)
    # What "value" means for this table, resolved once and reported at the
    # top level so a reader sees it before any figure that depends on it.
    basis = resolve_value_basis(detection)
    # A unit-price monetary column with no quantity to multiply by: the
    # analyses still run, on the unit price, but the gap is surfaced for
    # confirmation rather than papered over with a guessed multiplier.
    quantity_gap = quantity_needs_confirmation(detection)

    results: list[BusinessAnalysisResult] = []
    for kind, runner in _ANALYSES:
        try:
            results.append(runner(df, detection))
        except Exception as exc:  # noqa: BLE001 - one analysis must never sink the rest
            results.append(
                BusinessAnalysisResult(
                    analysis=kind.value,
                    ran=False,
                    not_run_reason=f"analysis raised {type(exc).__name__}: {exc}",
                )
            )

    # Stable, citable ids, assigned once over the whole run's output.
    position = 0
    for result in results:
        for finding in result.findings:
            finding.id = f"{result.analysis}-{finding.finding_type.value}-{position}"
            position += 1

    return BusinessAnalyticsFindings(
        run_id=run_id,
        detected_roles=detection.to_dict(),
        applicability=[a.to_dict() for a in applicability],
        results=results,
        value_definition=basis.to_dict() if basis else None,
        quantity_confirmation=quantity_gap,
    )
