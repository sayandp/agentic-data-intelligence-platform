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

#: The share of total value the top 20% of entities must hold for this
#: method's premise to hold. Below it the bands still compute - the numbers
#: are real - but "ABC" and "Pareto" both name an assumption of steep
#: concentration that this data does not exhibit, and a reader must be told
#: so rather than left to infer it from a shallow curve.
#:
#: Same treatment as the trend R-squared floor (app/exploration/config.py)
#: and the k-means silhouette floor (app/analytics/segmentation.py): a
#: named, configurable threshold, with the measured number always reported
#: alongside the verdict. Those two SKIP the finding when the premise
#: fails; this one reports it and flags it, because a concentration curve
#: is still a truthful description of the data even when it is flat.
DEFAULT_CONCENTRATION_FLOOR = 0.5

#: The quantile the headline figure is taken at. 20% is the Pareto
#: convention and is not configurable - changing it would make the reported
#: number incomparable to every other statement of the "80/20 rule".
TOP_SHARE_QUANTILE = 0.2


def _concentration_note(top_share: float, entity_label: str, taken_over: int, entity_count: int, floor: float, weak: bool) -> str:
    """The sentence stating the shape, reported either way.

    Names the real denominator when the top 20% does not land on a whole
    entity, so a figure computed over 1 of 5 is never presented as though it
    came from a fifth of a large population.
    """
    actual_share = taken_over / entity_count if entity_count else 0.0
    scope = f"top {TOP_SHARE_QUANTILE:.0%} of {entity_label}"
    if abs(actual_share - TOP_SHARE_QUANTILE) > 0.02:
        scope += f" (the top {taken_over} of {entity_count}, {actual_share:.0%})"
    headline = f"The {scope} hold {top_share:.1%} of total value."
    if not weak:
        return f"{headline} Concentration clears the {floor:.0%} floor this check uses."
    return (
        f"{headline} That is below the {floor:.0%} floor this check uses, so concentration is weak for this "
        "data and the A/B/C bands separate it less sharply than the method's name implies."
    )


def run_abc_pareto(
    df: pd.DataFrame,
    detection: RoleDetection,
    band_cutoffs: tuple[float, float] = DEFAULT_BAND_CUTOFFS,
    concentration_floor: float = DEFAULT_CONCENTRATION_FLOOR,
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

    # How many entities the headline covers. At least one, never more than
    # all of them - and the count is carried into the payload so the share
    # is read against its real denominator.
    top_entity_count = min(entity_count, max(1, round(entity_count * TOP_SHARE_QUANTILE)))
    top_20_share = float(cumulative_value.iloc[top_entity_count - 1])
    concentration_is_weak = top_20_share < concentration_floor

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
                top_20_percent_entity_count=top_entity_count,
                top_20_percent_entity_share=round(top_entity_count / entity_count, 6),
                concentration_floor=concentration_floor,
                concentration_is_weak=concentration_is_weak,
                concentration_note=_concentration_note(
                    top_20_share, entity_label, top_entity_count, entity_count, concentration_floor, concentration_is_weak
                ),
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
            # Echoed here too, not only on the curve payload: the threshold
            # that decided the flag has to be visible next to the other
            # knobs, or the verdict is not reproducible from the result.
            "concentration_floor": concentration_floor,
            "top_20_percent_value_share": round(top_20_share, 6),
            "concentration_is_weak": concentration_is_weak,
        },
    )
