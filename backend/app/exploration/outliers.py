"""Part 2.3: IQR-based outlier clusters. Reported as one finding per column
(count, bounds, capped example indices) - never a per-row dump."""

from __future__ import annotations

import pandas as pd

from app.exploration.config import ExplorationConfig
from app.exploration.findings import Evidence, Finding, FindingType, OutlierClusterPayload

IQR_MULTIPLIER = 1.5


def compute_outlier_clusters(
    df: pd.DataFrame, numeric_columns: list[str], config: ExplorationConfig, sampling_seed: int | None
) -> list[Finding]:
    findings: list[Finding] = []
    for column in sorted(numeric_columns):
        series = df[column].dropna().astype(float)
        if series.empty:
            continue
        q1, q3 = series.quantile(0.25), series.quantile(0.75)
        iqr = q3 - q1
        if iqr == 0:
            continue  # no meaningful bounds on a degenerate (near-constant) distribution

        lower = q1 - IQR_MULTIPLIER * iqr
        upper = q3 + IQR_MULTIPLIER * iqr
        outliers = series[(series < lower) | (series > upper)]
        if outliers.empty:
            continue

        examples = [str(idx) for idx in list(outliers.index[: config.outlier_max_examples])]
        findings.append(
            Finding(
                finding_type=FindingType.OUTLIER_CLUSTER,
                columns=[column],
                payload=OutlierClusterPayload(
                    column=column,
                    lower_bound=float(lower),
                    upper_bound=float(upper),
                    count=int(len(outliers)),
                    example_indices=examples,
                ),
                evidence=Evidence(sample_size=int(len(series)), sampling_seed=sampling_seed),
            )
        )
    return findings
