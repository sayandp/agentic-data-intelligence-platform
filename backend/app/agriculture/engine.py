"""Deterministic orchestration of the Agriculture Agent.

Mirrors app/marketing/engine.py exactly: qualify, prepare, run every rule,
assign stable ids over the final list. No LLM on this path and no place to put
one.

One rule raising must never sink the rest - the same guarantee the marketing
and analytics engines give - so each is wrapped and its failure recorded as a
skipped rule rather than losing the whole run's agricultural output.
"""

from __future__ import annotations

import pandas as pd

from app.agriculture.applicability import qualifies, role_report
from app.agriculture.chart_specs import agriculture_charts
from app.agriculture.config import AgricultureConfig
from app.agriculture.findings import AgricultureFindings
from app.agriculture.preprocess import prepare
from app.agriculture.rules import RULES
from app.analytics.roles import RoleDetection


def run_agriculture(
    run_id: str,
    df: pd.DataFrame,
    detection: RoleDetection,
    config: AgricultureConfig | None = None,
    baseline_profile: dict | None = None,
) -> AgricultureFindings:
    """`detection` is the run's ONE semantic role detection pass, passed in
    rather than recomputed - the same object exploration, analytics and the
    marketing pack read."""
    config = config or AgricultureConfig()
    resolved, missing = role_report(detection, config)

    applicable, missing_required, reason = qualifies(detection, config)
    if not applicable:
        return AgricultureFindings(
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
        finding.id = f"agriculture-{finding.finding_type.value}-{index}"

    return AgricultureFindings(
        run_id=run_id,
        applicable=True,
        resolved_roles=resolved,
        missing_roles=missing,
        preprocessing=preprocessing,
        findings=findings,
        skipped_rules=skipped,
        parameters=config.as_reported(),
        charts=agriculture_charts(prepared, roles, config),
    )
