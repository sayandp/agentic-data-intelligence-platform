"""Deterministic orchestration of every exploration analysis (Part 2), with
the scale/determinism guards from Part 3 applied in one place: a row cap
(seeded sampling if hit, seed recorded on every finding computed from the
sample) for the analyses expensive enough to need it, and a column cap for
correlation specifically (O(columns^2)).

NO LLM ANYWHERE IN THIS MODULE. Every analysis here is a closed-form
statistical computation - same input, same output, always. That determinism
is the whole point of this agent: it is what makes byte-reproducibility
even a coherent thing to test for.

Summary statistics and missing-pattern detection run on the FULL frame -
"count" and "null co-occurrence" must reflect the real data, not a sample.
The row cap only ever applies to the four analyses that are genuinely
expensive at scale: correlation, outliers, trend, distribution shape.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from app.exploration.columns import datetime_columns, numeric_columns
from app.exploration.config import ExplorationConfig
from app.exploration.correlation_analysis import compute_correlations
from app.exploration.distribution import compute_distribution_shape
from app.exploration.findings import CURRENT_SCHEMA_VERSION, DataQualityContext, ExplorationFindings, Finding
from app.exploration.missingness import compute_missing_patterns
from app.exploration.outliers import compute_outlier_clusters
from app.exploration.stats import compute_cardinality_notes, compute_summary_stats
from app.exploration.trend import compute_trends


class ExplorationEngine:
    def __init__(self, config: ExplorationConfig | None = None):
        self.config = config or ExplorationConfig()

    def run(
        self,
        df: pd.DataFrame,
        run_id: str,
        data_quality_context: DataQualityContext,
        generated_at: datetime | None = None,
    ) -> ExplorationFindings:
        cfg = self.config
        findings: list[Finding] = []
        skipped = []

        findings += compute_summary_stats(df, cfg)
        findings += compute_cardinality_notes(df, cfg)
        findings += compute_missing_patterns(df, cfg)

        working_df, sampling_seed = _apply_row_cap(df, cfg.row_cap, cfg.row_cap_seed)
        numeric_cols = numeric_columns(working_df)
        datetime_cols = datetime_columns(working_df)

        corr_findings, corr_skipped = compute_correlations(working_df, numeric_cols, cfg, sampling_seed)
        findings += corr_findings
        skipped += corr_skipped

        findings += compute_outlier_clusters(working_df, numeric_cols, cfg, sampling_seed)

        trend_findings, trend_skipped = compute_trends(working_df, datetime_cols, numeric_cols, cfg, sampling_seed)
        findings += trend_findings
        skipped += trend_skipped

        findings += compute_distribution_shape(working_df, numeric_cols, cfg, sampling_seed)

        # Assigned here, once, over the FINAL list - deterministic given the
        # already-deterministic construction order above, and the only
        # place that can assign an id relative to the whole run's output
        # rather than one analysis module's own slice of it.
        for i, finding in enumerate(findings):
            finding.id = f"{finding.finding_type.value}-{i}"

        kwargs = dict(
            schema_version=CURRENT_SCHEMA_VERSION,
            run_id=run_id,
            findings=findings,
            skipped=skipped,
            data_quality_context=data_quality_context,
        )
        if generated_at is not None:
            kwargs["generated_at"] = generated_at
        return ExplorationFindings(**kwargs)


def _apply_row_cap(df: pd.DataFrame, row_cap: int, seed: int) -> tuple[pd.DataFrame, int | None]:
    if len(df) <= row_cap:
        return df, None
    return df.sample(n=row_cap, random_state=seed), seed
