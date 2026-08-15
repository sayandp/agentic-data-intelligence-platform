"""Whether this run's data supports the Marketing Agent, and what is missing.

Same contract as app/analytics/applicability.py: an agent that silently
never fires is dead code, so the refusal is a first-class output carrying
the precise unmet requirement. A user looking at an absent Marketing tab
must never have to guess whether the agent found nothing or was never
eligible.

NO LLM. NO CAUSAL VOCABULARY.
"""

from __future__ import annotations

from app.analytics.roles import ColumnRole, RoleDetection
from app.marketing.config import MarketingConfig

#: What a missing role means in words, so a refusal reads as a sentence
#: rather than an enum dump. Same table shape the analytics report uses.
ROLE_DESCRIPTIONS: dict[ColumnRole, str] = {
    ColumnRole.SPEND: "an amount-spent column (ad spend per row)",
    ColumnRole.EVENT_DATE: "a reporting-date column",
    ColumnRole.CAMPAIGN_ID: "a campaign or ad-set name",
    ColumnRole.IMPRESSIONS: "an impressions count",
    ColumnRole.CLICKS: "a clicks count",
    ColumnRole.CTR: "a click-through rate",
    ColumnRole.FREQUENCY: "an average-impressions-per-person (frequency) column",
    ColumnRole.CONVERSIONS: "a conversions count",
    ColumnRole.CONVERSION_VALUE: "a conversion-value column (revenue attributed to ads)",
}


def qualifies(detection: RoleDetection, config: MarketingConfig) -> tuple[bool, list[ColumnRole], str | None]:
    """(applicable, missing required roles, reason).

    The required set is CONFIGURABLE (MarketingConfig.required_roles) rather
    than hardcoded here, because "what makes a file an ads export" is a
    judgement an operator may need to change without a code change.
    """
    assigned = detection.assigned()
    missing = [role for role in config.required_roles if role not in assigned]

    # Spend and a date alone do not make a file an ads export. At least one
    # role only an ad platform produces must be present too, or a retail
    # dataset with a cost column is analysed as a campaign.
    if not missing and config.required_any_of and not any(role in assigned for role in config.required_any_of):
        wanted = " or ".join(ROLE_DESCRIPTIONS.get(r, r.value) for r in config.required_any_of)
        return (
            False,
            list(config.required_any_of),
            (
                "This run's data has spend and a date but nothing only an ad platform reports. "
                f"Marketing analysis also needs at least one of: {wanted}. "
                "Without one of those, a file with a cost column is indistinguishable from any other commerce export."
            ),
        )

    if not missing:
        return True, [], None

    needed = ", ".join(ROLE_DESCRIPTIONS.get(role, role.value) for role in missing)
    reason = (
        f"This run's data does not look like an ad-platform export. "
        f"Marketing analysis needs {needed}, and no column in this source scored high enough to fill "
        f"{'that role' if len(missing) == 1 else 'those roles'}."
    )

    # Name the closest rejected candidate, so the refusal is actionable
    # rather than a dead end - a user can confirm a role the detector was
    # not confident enough to take on its own.
    hints = []
    for role in missing:
        rejected = detection.rejected(role)
        if rejected:
            best = rejected[0]
            why = best.reasons[0] if best.reasons else "did not score high enough"
            hints.append(f"closest candidate for {role.value}: `{best.column}` ({best.confidence.value} confidence - {why})")
    if hints:
        reason += " " + "; ".join(hints) + "."
    return False, missing, reason


def role_report(detection: RoleDetection, config: MarketingConfig) -> tuple[dict, list[str]]:
    """Which marketing role each column filled, and which were absent.

    Optional roles are reported as missing too - their absence is why a rule
    was skipped, and that has to be visible.
    """
    assigned = detection.assigned()
    marketing_roles = tuple(config.required_roles) + tuple(config.optional_roles)

    resolved = {
        role.value: {
            "column": assigned[role].column,
            "confidence": assigned[role].confidence.value,
            "confirmed": detection.confirmed_roles.get(role.value) == assigned[role].column,
        }
        for role in marketing_roles
        if role in assigned
    }
    missing = [role.value for role in marketing_roles if role not in assigned]
    return resolved, missing
