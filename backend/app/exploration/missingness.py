"""Part 2.6: structural missing-value patterns - columns whose nulls
co-occur, either symmetrically or in one direction (nulls in A contained
within nulls in B). Distinct from ValidationEngine's null_threshold rule,
which compares a single column's null RATE against a baseline; this looks
at co-occurrence ACROSS columns on the current frame only, something
validation never computes."""

from __future__ import annotations

import itertools

import pandas as pd

from app.exploration.config import ExplorationConfig
from app.exploration.findings import Evidence, Finding, FindingType, MissingPatternPayload, MissingPatternRelationship


def compute_missing_patterns(df: pd.DataFrame, config: ExplorationConfig) -> list[Finding]:
    null_columns = sorted(c for c in df.columns if df[c].isna().any())
    findings: list[Finding] = []

    for column_a, column_b in itertools.combinations(null_columns, 2):
        a_null = df[column_a].isna()
        b_null = df[column_b].isna()
        support = int((a_null & b_null).sum())
        if support < config.missing_pattern_min_support:
            continue

        a_total = int(a_null.sum())
        b_total = int(b_null.sum())
        p_b_given_a = support / a_total if a_total else 0.0
        p_a_given_b = support / b_total if b_total else 0.0

        if p_b_given_a >= config.missing_pattern_threshold and p_a_given_b >= config.missing_pattern_threshold:
            findings.append(
                Finding(
                    finding_type=FindingType.MISSING_PATTERN,
                    columns=[column_a, column_b],
                    payload=MissingPatternPayload(
                        columns=[column_a, column_b],
                        relationship=MissingPatternRelationship.CO_OCCURRING,
                        support=support,
                        confidence=min(p_b_given_a, p_a_given_b),
                    ),
                    evidence=Evidence(sample_size=len(df)),
                )
            )
        elif p_b_given_a >= config.missing_pattern_threshold:
            findings.append(_one_directional(column_a, column_b, support, p_b_given_a, len(df)))
        elif p_a_given_b >= config.missing_pattern_threshold:
            findings.append(_one_directional(column_b, column_a, support, p_a_given_b, len(df)))

    return findings


def _one_directional(narrower: str, broader: str, support: int, confidence: float, sample_size: int) -> Finding:
    return Finding(
        finding_type=FindingType.MISSING_PATTERN,
        columns=[narrower, broader],
        payload=MissingPatternPayload(
            columns=[narrower, broader],
            relationship=MissingPatternRelationship.ONE_DIRECTIONAL,
            narrower_column=narrower,
            broader_column=broader,
            support=support,
            confidence=confidence,
        ),
        evidence=Evidence(sample_size=sample_size),
    )
