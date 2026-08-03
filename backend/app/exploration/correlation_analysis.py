"""Part 2.2 + Part 3's column cap: Pearson (and Spearman where the
relationship looks monotonic-but-nonlinear) between numeric column pairs.

Not to be confused with app/correlation.py, which groups VALIDATION FAILURES
before diagnosis - an unrelated, deterministic pre-diagnosis layer. This
module computes statistical correlation between DATA columns.
"""

from __future__ import annotations

import itertools

import pandas as pd
from scipy.stats import pearsonr, spearmanr

from app.exploration.config import ExplorationConfig
from app.exploration.findings import CorrelationMethod, CorrelationPayload, Evidence, Finding, FindingType, SkippedEntry


def _select_columns_within_cap(df: pd.DataFrame, numeric_columns: list[str], config: ExplorationConfig, skipped: list[SkippedEntry]) -> list[str]:
    if len(numeric_columns) <= config.max_correlation_columns:
        return numeric_columns
    # Highest-variance columns first, column name as a deterministic
    # tiebreaker - never left to pandas' sort stability on float ties.
    ranked = sorted(numeric_columns, key=lambda c: (-float(df[c].var(ddof=0)), c))
    kept = ranked[: config.max_correlation_columns]
    kept_set = set(kept)
    for column in sorted(numeric_columns):
        if column not in kept_set:
            skipped.append(
                SkippedEntry(
                    column=column,
                    reason=(
                        f"correlation column cap ({config.max_correlation_columns}) exceeded; "
                        "kept the higher-variance columns"
                    ),
                )
            )
    return kept


def compute_correlations(
    df: pd.DataFrame, numeric_columns: list[str], config: ExplorationConfig, sampling_seed: int | None
) -> tuple[list[Finding], list[SkippedEntry]]:
    skipped: list[SkippedEntry] = []
    columns = _select_columns_within_cap(df, numeric_columns, config, skipped)

    findings: list[Finding] = []
    for column_a, column_b in itertools.combinations(sorted(columns), 2):
        pair = df[[column_a, column_b]].dropna()
        n = len(pair)
        if n < config.correlation_min_n:
            skipped.append(
                SkippedEntry(
                    column=f"{column_a},{column_b}",
                    reason=f"correlation suppressed: n={n} below minimum {config.correlation_min_n}",
                )
            )
            continue

        x, y = pair[column_a].astype(float), pair[column_b].astype(float)
        if x.nunique() < 2 or y.nunique() < 2:
            continue  # a constant column has no defined correlation

        pearson_r, pearson_p = pearsonr(x, y)
        spearman_r, spearman_p = spearmanr(x, y)

        if abs(pearson_r) >= config.correlation_threshold:
            findings.append(
                _finding(column_a, column_b, CorrelationMethod.PEARSON, pearson_r, n, pearson_p, sampling_seed)
            )
        if abs(spearman_r) >= config.correlation_threshold and (
            abs(spearman_r) - abs(pearson_r) > config.spearman_nonlinearity_delta
        ):
            findings.append(
                _finding(column_a, column_b, CorrelationMethod.SPEARMAN, spearman_r, n, spearman_p, sampling_seed)
            )

    return findings, skipped


def _finding(
    column_a: str, column_b: str, method: CorrelationMethod, coefficient: float, n: int, p_value: float, sampling_seed: int | None
) -> Finding:
    return Finding(
        finding_type=FindingType.CORRELATION,
        columns=[column_a, column_b],
        payload=CorrelationPayload(column_a=column_a, column_b=column_b, method=method, coefficient=float(coefficient)),
        evidence=Evidence(sample_size=n, p_value=float(p_value), sampling_seed=sampling_seed),
    )
