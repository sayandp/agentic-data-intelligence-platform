"""Deterministic orchestration of the Marketing Agent.

Mirrors app/analytics/engine.py: qualify, prepare, run every rule, assign
stable ids over the final list. No LLM on this path and no place to put one.

One rule raising must never sink the rest - the same guarantee the analytics
engine gives - so each is wrapped and its failure recorded as a skipped rule
rather than losing the whole run's marketing output.
"""

from __future__ import annotations

import pandas as pd

from app.analytics.roles import RoleDetection
from app.marketing.applicability import qualifies, role_report
from app.marketing.chart_specs import marketing_charts
from app.marketing.config import MarketingConfig
from app.marketing.findings import MarketingFindings
from app.marketing.preprocess import prepare
from app.marketing.rules import RULES


def run_marketing(
    run_id: str,
    df: pd.DataFrame,
    detection: RoleDetection,
    config: MarketingConfig | None = None,
    baseline_profile: dict | None = None,
) -> MarketingFindings:
    """`detection` is the run's ONE semantic role detection pass, passed in
    rather than recomputed - the same object exploration and the analytics
    agent read (app/semantic_roles.py)."""
    config = config or MarketingConfig()
    resolved, missing = role_report(detection, config)

    applicable, missing_required, reason = qualifies(detection, config)
    if not applicable:
        return MarketingFindings(
            run_id=run_id,
            applicable=False,
            not_applicable_reason=reason,
            resolved_roles=resolved,
            missing_roles=missing,
            parameters=config.as_reported(),
        )

    prepared, preprocessing, roles = prepare(df, detection, config)

    findings = []
    skipped: list[dict] = []
    for rule in RULES:
        try:
            produced, rule_skips = rule(prepared, roles, config, baseline_profile)
            findings.extend(produced)
            skipped.extend(rule_skips)
        except Exception as exc:  # noqa: BLE001 - one rule must never sink the rest
            skipped.append({"rule": rule.__name__, "reason": f"rule raised {type(exc).__name__}: {exc}"})

    # Assigned here, once, over the FINAL list - deterministic given the
    # already-deterministic rule order, and the only place that can number
    # findings relative to the whole run rather than one rule's slice.
    for index, finding in enumerate(findings):
        finding.id = f"marketing-{finding.finding_type.value}-{index}"

    return MarketingFindings(
        run_id=run_id,
        applicable=True,
        resolved_roles=resolved,
        missing_roles=missing,
        preprocessing=preprocessing,
        findings=findings,
        skipped_rules=skipped,
        parameters=config.as_reported(),
        charts=marketing_charts(prepared, roles, config),
    )
