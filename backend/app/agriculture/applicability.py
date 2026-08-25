"""Whether this run's data supports the Agriculture Agent, and what is missing.

Same contract as app/marketing/applicability.py: an agent that silently never
fires is dead code, so the refusal is a first-class output carrying the precise
unmet requirement. A user looking at an absent Agriculture tab must never have
to guess whether the agent found nothing or was never eligible.

NO LLM. NO CAUSAL VOCABULARY.
"""

from __future__ import annotations

from app.agriculture.config import AgricultureConfig
from app.analytics.roles import ColumnRole, RoleDetection

ROLE_DESCRIPTIONS: dict[ColumnRole, str] = {
    ColumnRole.DISTRICT: "a district, region or state column",
    ColumnRole.CROP: "a crop column",
    ColumnRole.SEASON: "a season column (kharif, rabi, whole year)",
    ColumnRole.CROP_YEAR: "a crop-year column",
    ColumnRole.AREA_CULTIVATED: "an area-cultivated column (hectares or acres)",
    ColumnRole.PRODUCTION_QUANTITY: "a production column (tonnes or quintals)",
    ColumnRole.CROP_YIELD: "a yield column (production per unit area)",
    ColumnRole.RAINFALL: "a rainfall column (millimetres)",
    ColumnRole.MARKET_PRICE: "a market-price column",
}


def qualifies(detection: RoleDetection, config: AgricultureConfig) -> tuple[bool, list[ColumnRole], str | None]:
    """(applicable, missing required roles, reason)."""
    assigned = detection.assigned()
    missing = [role for role in config.required_roles if role not in assigned]

    if missing:
        wanted = ", ".join(ROLE_DESCRIPTIONS.get(r, r.value) for r in missing)
        return (
            False,
            missing,
            f"this source is missing {wanted}, so it does not read as agricultural production data",
        )

    # A district, a crop and a production figure could still be a logistics
    # table. A season, a crop year or an area is what makes it a growing
    # record rather than a shipping one.
    if config.required_any_of and not any(role in assigned for role in config.required_any_of):
        wanted = " or ".join(ROLE_DESCRIPTIONS.get(r, r.value) for r in config.required_any_of)
        return (
            False,
            list(config.required_any_of),
            (
                f"this source has a district, a crop and a production figure but none of {wanted}. "
                "Without one of those it reads as a quantity table rather than a record of what was grown."
            ),
        )

    return True, [], None


def role_report(detection: RoleDetection, config: AgricultureConfig) -> tuple[dict, list[str]]:
    """(resolved roles, missing role names) across every role this pack can
    use - not only the required ones. A reader needs to know that rainfall was
    absent even though its absence did not block the pack."""
    assigned = detection.assigned()
    resolved = {
        role.value: {
            "column": candidate.column,
            "confidence": candidate.confidence.value,
            "reasons": candidate.reasons,
        }
        for role, candidate in assigned.items()
        if role in ROLE_DESCRIPTIONS
    }
    missing = [role.value for role in ROLE_DESCRIPTIONS if role not in assigned]
    return resolved, missing
