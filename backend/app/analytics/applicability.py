"""Which analyses this run's data can actually support, and why not.

An analysis that silently never fires is dead code. So every analysis
declares the roles it requires up front, and this module turns those
declarations plus a RoleDetection into a report that is as explicit about
refusals as it is about successes:

    "cohort_retention: not applicable - needs a customer identifier; none
     detected. Closest candidate: `region` (low confidence, 12% unique)."

That sentence is the product. A user looking at an empty analytics page
must never have to guess whether the analysis found nothing interesting or
was never eligible to run.

NO LLM. NO CAUSAL VOCABULARY.
"""

from __future__ import annotations

from enum import Enum

from app.analytics.roles import ColumnRole, RoleDetection


class AnalysisKind(str, Enum):
    """Closed set. PART 1 of the brief and nothing else - an analysis not
    named here does not exist, and the deliberately-excluded methods are
    documented in the README rather than stubbed out in code."""

    ABC_PARETO = "abc_pareto"
    RFM = "rfm"
    COHORT_RETENTION = "cohort_retention"
    BEHAVIOURAL_SEGMENTATION = "behavioural_segmentation"
    MARKET_BASKET = "market_basket"
    RETENTION_CHURN = "retention_churn"
    HISTORICAL_CLV = "historical_clv"


#: What each analysis needs to run at all. Declared as data so the
#: applicability report and the analyses themselves can never disagree
#: about the requirement - the report is generated FROM this table, not
#: written alongside it.
REQUIRED_ROLES: dict[AnalysisKind, tuple[ColumnRole, ...]] = {
    AnalysisKind.ABC_PARETO: (ColumnRole.MONETARY,),
    AnalysisKind.RFM: (ColumnRole.ENTITY_ID, ColumnRole.EVENT_DATE, ColumnRole.MONETARY),
    AnalysisKind.COHORT_RETENTION: (ColumnRole.ENTITY_ID, ColumnRole.EVENT_DATE),
    AnalysisKind.BEHAVIOURAL_SEGMENTATION: (ColumnRole.ENTITY_ID, ColumnRole.EVENT_DATE, ColumnRole.MONETARY),
    AnalysisKind.MARKET_BASKET: (ColumnRole.TRANSACTION_ID, ColumnRole.ITEM_ID),
    AnalysisKind.RETENTION_CHURN: (ColumnRole.ENTITY_ID, ColumnRole.EVENT_DATE),
    AnalysisKind.HISTORICAL_CLV: (ColumnRole.ENTITY_ID, ColumnRole.EVENT_DATE, ColumnRole.MONETARY),
}

#: A role that improves an analysis without gating it. ABC/Pareto groups by
#: an entity when one exists and falls back to ranking rows otherwise.
OPTIONAL_ROLES: dict[AnalysisKind, tuple[ColumnRole, ...]] = {
    AnalysisKind.ABC_PARETO: (ColumnRole.ENTITY_ID, ColumnRole.ITEM_ID),
}

#: Human-readable names for what a missing role means, so the refusal reads
#: like a sentence rather than an enum dump.
_ROLE_DESCRIPTIONS: dict[ColumnRole, str] = {
    ColumnRole.ENTITY_ID: "a customer-like identifier that repeats across rows",
    ColumnRole.TRANSACTION_ID: "a transaction/order identifier",
    ColumnRole.ITEM_ID: "a line-item identifier that groups within a transaction",
    ColumnRole.EVENT_DATE: "a date column",
    ColumnRole.MONETARY: "a non-negative monetary column",
    ColumnRole.QUANTITY: "a per-line quantity column",
}


class AnalysisApplicability:
    def __init__(
        self,
        analysis: AnalysisKind,
        applicable: bool,
        resolved_columns: dict[str, str],
        missing: list[str],
        near_misses: list[dict],
    ):
        self.analysis = analysis
        self.applicable = applicable
        #: role value -> column name, for the roles that WERE filled.
        self.resolved_columns = resolved_columns
        #: One sentence per unmet requirement.
        self.missing = missing
        #: Scored-but-rejected candidates for the unmet roles, so a user can
        #: see what the detector looked at and confirm one if it was right.
        self.near_misses = near_misses

    def to_dict(self) -> dict:
        return {
            "analysis": self.analysis.value,
            "applicable": self.applicable,
            "resolved_columns": self.resolved_columns,
            "missing_requirements": self.missing,
            "near_misses": self.near_misses,
        }


def build_applicability_report(detection: RoleDetection) -> list[AnalysisApplicability]:
    """One entry per analysis, in declaration order. Every analysis appears
    whether or not it can run - the not-applicable list is the point."""
    assigned = detection.assigned()
    report: list[AnalysisApplicability] = []

    for analysis in AnalysisKind:
        required = REQUIRED_ROLES[analysis]
        resolved: dict[str, str] = {}
        missing: list[str] = []
        near_misses: list[dict] = []

        for role in required:
            found = assigned.get(role)
            if found is not None:
                resolved[role.value] = found.column
                continue
            missing.append(f"needs {_ROLE_DESCRIPTIONS[role]}; none detected")
            for candidate in detection.rejected(role)[:2]:
                near_misses.append(
                    {
                        "role": role.value,
                        "column": candidate.column,
                        "confidence": candidate.confidence.value,
                        "score": candidate.score,
                        "why_rejected": candidate.reasons[0] if candidate.reasons else "below the confidence floor",
                    }
                )

        for role in OPTIONAL_ROLES.get(analysis, ()):
            found = assigned.get(role)
            if found is not None:
                resolved[role.value] = found.column

        report.append(
            AnalysisApplicability(
                analysis=analysis,
                applicable=not missing,
                resolved_columns=resolved,
                missing=missing,
                near_misses=near_misses,
            )
        )

    return report
