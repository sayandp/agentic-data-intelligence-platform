"""RFM scoring and named segments - analysis 2.

Quintile scores per dimension, then names from a rule table that is DATA,
declared at the top of this module and overridable by the caller. The brief's
requirement was explicit: "a DOCUMENTED, configurable rule table - not magic
thresholds buried in code". A reader must be able to see exactly why a
customer was called "at risk" without reading the implementation.

NO LLM. Scoring is quintiles; naming is a table lookup.

NO CAUSAL VOCABULARY. A segment ACCOUNTS FOR a share of value. It does not
drive, produce, or influence it.
"""

from __future__ import annotations

import pandas as pd

from app.analytics.applicability import AnalysisKind
from app.analytics.entity_features import EntityFeatures, build_entity_features
from app.analytics.findings import (
    AnalysisEvidence,
    AnalysisFinding,
    AnalysisFindingType,
    BusinessAnalysisResult,
    SegmentProfilePayload,
)
from app.analytics.roles import ColumnRole, RoleDetection

#: Quintiles: 5 is always "best" on every dimension. Recency is inverted
#: (fewer days since last seen is better), which is the one place an RFM
#: implementation usually gets silently wrong.
SCORE_LEVELS = 5

#: THE RULE TABLE. Ordered - the first matching rule wins, so more specific
#: segments must come first. Each rule is (name, predicate description,
#: r_range, f_range, m_range) with ranges INCLUSIVE on both ends.
#:
#: Kept as plain data so it can be printed, tested, and overridden without
#: touching any logic. The ranges below are the conventional definitions;
#: they are a starting point a business is expected to tune, not a claim
#: that these particular cuts are correct for every dataset.
DEFAULT_SEGMENT_RULES: tuple[tuple[str, tuple[int, int], tuple[int, int], tuple[int, int]], ...] = (
    # name,              recency,  frequency, monetary
    ("champions",        (4, 5),   (4, 5),    (4, 5)),
    ("loyal",            (3, 5),   (3, 5),    (3, 5)),
    ("promising",        (4, 5),   (1, 2),    (1, 3)),
    ("at_risk",          (1, 2),   (3, 5),    (3, 5)),
    ("hibernating",      (1, 2),   (2, 3),    (1, 3)),
    ("lost",             (1, 1),   (1, 2),    (1, 2)),
)

#: Anything matching no rule. Named explicitly rather than dropped: a
#: customer who does not fit a tidy segment is still a customer, and
#: silently omitting them would make the segment shares wrong.
UNSEGMENTED_NAME = "unsegmented"


def _quintile_scores(series: pd.Series, ascending: bool) -> pd.Series:
    """Rank into 1..5. `ascending=True` means a HIGHER raw value earns a
    higher score (frequency, monetary); `False` inverts it (recency).

    Uses rank-then-cut rather than qcut because real business data is full
    of ties - half the customers with exactly one order - and qcut raises
    on non-unique bin edges rather than degrading.
    """
    if series.empty:
        return series
    ranked = series.rank(method="first", ascending=ascending)
    # Guard tiny datasets: fewer distinct ranks than levels still has to
    # produce something monotonic rather than raising.
    levels = min(SCORE_LEVELS, max(1, int(ranked.nunique())))
    if levels == 1:
        return pd.Series(SCORE_LEVELS, index=series.index, dtype=int)
    binned = pd.qcut(ranked, q=levels, labels=False, duplicates="drop")
    # Rescale whatever bins survived onto 1..5 so the rule table's ranges
    # keep meaning on a small dataset.
    span = max(1, int(binned.max()))
    return (1 + (binned / span) * (SCORE_LEVELS - 1)).round().astype(int)


def assign_segment(r: int, f: int, m: int, rules=DEFAULT_SEGMENT_RULES) -> str:
    """First matching rule wins. Pure function of three integers - this is
    the whole of the naming logic, deliberately small enough to read."""
    for name, (r_lo, r_hi), (f_lo, f_hi), (m_lo, m_hi) in rules:
        if r_lo <= r <= r_hi and f_lo <= f <= f_hi and m_lo <= m <= m_hi:
            return name
    return UNSEGMENTED_NAME


def score_entities(features: EntityFeatures) -> pd.DataFrame:
    """Adds r/f/m scores and a segment name to the entity feature frame."""
    frame = features.frame.copy()
    frame["r_score"] = _quintile_scores(frame["recency_days"], ascending=False)
    frame["f_score"] = _quintile_scores(frame["frequency"], ascending=True)
    frame["m_score"] = _quintile_scores(frame["monetary"], ascending=True)
    frame["segment"] = [
        assign_segment(int(r), int(f), int(m))
        for r, f, m in zip(frame["r_score"], frame["f_score"], frame["m_score"])
    ]
    return frame


def run_rfm(
    df: pd.DataFrame,
    detection: RoleDetection,
    segment_rules=DEFAULT_SEGMENT_RULES,
) -> BusinessAnalysisResult:
    analysis = AnalysisKind.RFM.value
    entity = detection.best(ColumnRole.ENTITY_ID)
    date = detection.best(ColumnRole.EVENT_DATE)
    monetary = detection.best(ColumnRole.MONETARY)

    missing = []
    if entity is None:
        missing.append("a customer-like identifier")
    if date is None:
        missing.append("a date column")
    if monetary is None:
        missing.append("a non-negative monetary column")
    if missing:
        return BusinessAnalysisResult(
            analysis=analysis, ran=False, not_run_reason=f"needs {', '.join(missing)}; not detected"
        )

    features = build_entity_features(df, entity.column, date.column, monetary.column)
    if features is None or features.entity_count == 0:
        return BusinessAnalysisResult(
            analysis=analysis, ran=False, not_run_reason="no rows with both an entity and a usable date"
        )

    scored = score_entities(features)
    total_value = float(scored["monetary"].sum())
    entity_count = int(len(scored))
    # RFM RANKS entities, it does not decompose a total, so a net-negative
    # entity is meaningful here: it scores in the lowest monetary quintile,
    # which is exactly where a customer who returned everything belongs.
    # `value_share` is the one figure that stops meaning anything when the
    # OVERALL total is zero or below - dividing by a negative denominator
    # would report a net-negative segment as holding a positive share. In
    # that case shares are reported as 0.0 and the parameters say why.
    shares_are_meaningful = total_value > 0

    findings: list[AnalysisFinding] = []
    # Deterministic order: largest segment first, ties by name.
    counts = scored["segment"].value_counts()
    for segment in sorted(counts.index, key=lambda s: (-int(counts[s]), str(s))):
        members = scored[scored["segment"] == segment]
        value_total = float(members["monetary"].sum())
        findings.append(
            AnalysisFinding(
                analysis=analysis,
                finding_type=AnalysisFindingType.SEGMENT_PROFILE,
                columns=[entity.column, date.column, monetary.column],
                payload=SegmentProfilePayload(
                    segment=str(segment),
                    method="rfm_rule",
                    entity_count=int(len(members)),
                    entity_share=round(len(members) / entity_count, 6),
                    value_total=round(value_total, 6),
                    value_share=round(value_total / total_value, 6) if shares_are_meaningful else 0.0,
                    centre={
                        "recency_days": round(float(members["recency_days"].mean()), 3),
                        "frequency": round(float(members["frequency"].mean()), 3),
                        "monetary": round(float(members["monetary"].mean()), 3),
                        "r_score": round(float(members["r_score"].mean()), 3),
                        "f_score": round(float(members["f_score"].mean()), 3),
                        "m_score": round(float(members["m_score"].mean()), 3),
                    },
                ),
                evidence=AnalysisEvidence(
                    sample_size=int(len(df)),
                    entity_count=entity_count,
                    total_value=round(total_value, 6),
                    parameters={"score_levels": SCORE_LEVELS, "observation_end": str(features.observation_end)},
                ),
            )
        )

    return BusinessAnalysisResult(
        analysis=analysis,
        ran=True,
        findings=findings,
        parameters={
            "entity_column": entity.column,
            "date_column": date.column,
            "value_column": monetary.column,
            "score_levels": SCORE_LEVELS,
            "observation_end": str(features.observation_end),
            # The rule table is echoed IN FULL. A segment name with no
            # visible definition is exactly the "magic threshold" the brief
            # ruled out.
            "segment_rules": [
                {"segment": n, "recency": list(r), "frequency": list(f), "monetary": list(m)}
                for n, r, f, m in segment_rules
            ],
            "unsegmented_name": UNSEGMENTED_NAME,
            "total_value": round(total_value, 6),
            # Says so rather than quietly emitting zeros. Only ever False on
            # data whose monetary column nets to zero or below overall.
            "value_shares_meaningful": shares_are_meaningful,
            "net_negative_entity_count": int((scored["monetary"] < 0).sum()),
        },
    )
