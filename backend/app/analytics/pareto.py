"""ABC / Pareto concentration - analysis 1, and the pipeline proof.

Ranks entities by contributed value, computes the cumulative contribution
curve, and cuts A/B/C bands at configurable cumulative-share thresholds
(80/95 by default). Wholly deterministic: sort, cumsum, threshold. No
randomness, no model, no LLM.

CAUTION ON VOCABULARY. This measures CONCENTRATION - that a small share of
entities accounts for a large share of the total. It says nothing about why,
and nothing here may be phrased as though it does. "Contributes" and
"accounts for", never "drives" or "is responsible for".
"""

from __future__ import annotations

import pandas as pd

from app.analytics.applicability import AnalysisKind
from app.analytics.findings import (
    AnalysisEvidence,
    AnalysisFinding,
    AnalysisFindingType,
    BusinessAnalysisResult,
    ConcentrationBandPayload,
    ConcentrationCurvePayload,
)
from app.analytics.roles import ColumnRole, RoleDetection

#: Cumulative value share at which each band closes. A closes at 80%, B at
#: 95%, C is the remainder. Configurable per the brief; the values actually
#: used are always echoed into the result's `parameters`.
DEFAULT_BAND_CUTOFFS: tuple[float, float] = (0.8, 0.95)

#: The curve is for plotting. Beyond this many points it is downsampled -
#: recorded in parameters so a reader knows the curve is a sample of itself,
#: never silently.
MAX_CURVE_POINTS = 500

#: Entities named in a band payload, so a reader sees WHO is in band A
#: without loading the whole frame.
TOP_ENTITIES_PER_BAND = 10


def run_abc_pareto(
    df: pd.DataFrame,
    detection: RoleDetection,
    band_cutoffs: tuple[float, float] = DEFAULT_BAND_CUTOFFS,
) -> BusinessAnalysisResult:
    """Requires a monetary column. Groups by an entity/item identifier when
    one was detected; otherwise ranks individual rows, which is still a
    truthful concentration statement about the rows themselves."""
    analysis = AnalysisKind.ABC_PARETO.value

    lower, upper = band_cutoffs
    if not (0 < lower < upper < 1):
        raise ValueError(f"band cutoffs must satisfy 0 < lower < upper < 1, got {band_cutoffs}")

    monetary = detection.best(ColumnRole.MONETARY)
    if monetary is None:
        return BusinessAnalysisResult(
            analysis=analysis,
            ran=False,
            not_run_reason="needs a non-negative monetary column; none detected",
            parameters={"band_cutoffs": list(band_cutoffs)},
        )

    # Prefer an item identifier (what sells) over a customer one (who buys),
    # then a transaction key - all three are legitimate ABC subjects, but when
    # a dataset offers several, "which products account for most revenue" is
    # the conventional reading. Falling through to per-row ranking is a last
    # resort: it is still truthful, but "row 7" is a poor thing to name.
    grouping = (
        detection.best(ColumnRole.ITEM_ID)
        or detection.best(ColumnRole.ENTITY_ID)
        or detection.best(ColumnRole.TRANSACTION_ID)
    )
    value_column = monetary.column

    frame = df[[value_column]].copy() if grouping is None else df[[grouping.column, value_column]].copy()
    frame = frame.dropna(subset=[value_column])
    if frame.empty:
        return BusinessAnalysisResult(
            analysis=analysis,
            ran=False,
            not_run_reason=f"`{value_column}` has no non-null values to rank",
            parameters={"band_cutoffs": list(band_cutoffs), "value_column": value_column},
        )

    if grouping is None:
        totals = frame[value_column].reset_index(drop=True)
        totals.index = totals.index.map(lambda i: f"row {i}")
        entity_label = "row"
        entity_column = None
    else:
        entity_column = grouping.column
        totals = frame.groupby(entity_column, observed=True)[value_column].sum()
        entity_label = entity_column

    total_value = float(totals.sum())
    if total_value <= 0:
        return BusinessAnalysisResult(
            analysis=analysis,
            ran=False,
            not_run_reason=f"`{value_column}` sums to {total_value:g}; concentration is undefined without positive total value",
            parameters={"band_cutoffs": list(band_cutoffs), "value_column": value_column},
        )

    # Deterministic order: value descending, ties broken by entity name so
    # the same frame always produces the same ranking.
    ranked = totals.sort_index().sort_values(ascending=False, kind="mergesort")
    cumulative_value = ranked.cumsum() / total_value
    entity_count = int(len(ranked))
    cumulative_entity = pd.Series(range(1, entity_count + 1), index=ranked.index) / entity_count

    # Band assignment: an entity belongs to the band its CUMULATIVE share
    # falls within, so band A is "the smallest set accounting for <=80%".
    def band_of(cum_share: float) -> str:
        if cum_share <= lower:
            return "A"
        if cum_share <= upper:
            return "B"
        return "C"

    bands = cumulative_value.map(band_of)
    # The first entity past a cutoff still belongs to the band that crosses
    # it - otherwise a single dominant entity could empty band A entirely.
    if entity_count and bands.iloc[0] != "A":
        bands.iloc[0] = "A"

    findings: list[AnalysisFinding] = []
    columns_used = [c for c in (entity_column, value_column) if c]

    running_share = 0.0
    for band in ("A", "B", "C"):
        members = ranked[bands == band]
        if members.empty:
            continue
        band_value = float(members.sum())
        band_share = band_value / total_value
        running_share += band_share
        findings.append(
            AnalysisFinding(
                analysis=analysis,
                finding_type=AnalysisFindingType.CONCENTRATION_BAND,
                columns=columns_used,
                payload=ConcentrationBandPayload(
                    band=band,
                    entity_count=int(len(members)),
                    entity_share=round(len(members) / entity_count, 6),
                    value_total=round(band_value, 6),
                    value_share=round(band_share, 6),
                    cumulative_value_share=round(min(running_share, 1.0), 6),
                    top_entities=[str(i) for i in members.head(TOP_ENTITIES_PER_BAND).index],
                ),
                evidence=AnalysisEvidence(
                    sample_size=int(len(frame)),
                    entity_count=entity_count,
                    total_value=round(total_value, 6),
                    parameters={"band_cutoffs": [lower, upper], "grouped_by": entity_label},
                ),
            )
        )

    # The curve, downsampled by even index stride so the shape survives and
    # the last point (100%) is always kept.
    stride = max(1, entity_count // MAX_CURVE_POINTS)
    keep = list(range(0, entity_count, stride))
    if keep and keep[-1] != entity_count - 1:
        keep.append(entity_count - 1)

    top_20_share = float(cumulative_value.iloc[min(entity_count, max(1, round(entity_count * 0.2))) - 1])

    findings.append(
        AnalysisFinding(
            analysis=analysis,
            finding_type=AnalysisFindingType.CONCENTRATION_CURVE,
            columns=columns_used,
            payload=ConcentrationCurvePayload(
                entity_rank=[i + 1 for i in keep],
                cumulative_entity_share=[round(float(cumulative_entity.iloc[i]), 6) for i in keep],
                cumulative_value_share=[round(float(cumulative_value.iloc[i]), 6) for i in keep],
                top_20_percent_value_share=round(top_20_share, 6),
            ),
            evidence=AnalysisEvidence(
                sample_size=int(len(frame)),
                entity_count=entity_count,
                total_value=round(total_value, 6),
                parameters={
                    "band_cutoffs": [lower, upper],
                    "grouped_by": entity_label,
                    "curve_points": len(keep),
                    "downsampled": stride > 1,
                },
            ),
        )
    )

    return BusinessAnalysisResult(
        analysis=analysis,
        ran=True,
        findings=findings,
        parameters={
            "band_cutoffs": [lower, upper],
            "value_column": value_column,
            "grouped_by": entity_label,
            "entity_count": entity_count,
            "total_value": round(total_value, 6),
        },
    )
