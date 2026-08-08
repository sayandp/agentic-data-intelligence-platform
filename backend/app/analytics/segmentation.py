"""Behavioural segmentation - k-means over standardized RFM features.

k is chosen by silhouette over a small range. If no k clears the floor, the
answer is "no stable segmentation found" - a first-class result, not a
failure. That mirrors the Modeling Agent's baseline gate exactly: a model
that cannot beat its trivial baseline is not reported as a model, and a
clustering that does not separate is not reported as segments.

SEEDED. The seed is fixed and echoed into the output, because a segmentation
a reader cannot reproduce is not evidence.

NO LLM in choosing k or in profiling. NO CAUSAL VOCABULARY.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.analytics.applicability import AnalysisKind
from app.analytics.entity_features import build_entity_features
from app.analytics.findings import (
    AnalysisEvidence,
    AnalysisFinding,
    AnalysisFindingType,
    BusinessAnalysisResult,
    SegmentProfilePayload,
)
from app.analytics.roles import ColumnRole, RoleDetection

#: Deliberately small. Beyond a handful of behavioural segments a business
#: cannot act differently on each one, so more k is more noise.
DEFAULT_K_RANGE: tuple[int, ...] = (2, 3, 4, 5)

#: Below this, the clusters overlap enough that calling them segments would
#: overstate the evidence. Configurable; always echoed.
DEFAULT_SILHOUETTE_FLOOR = 0.25

DEFAULT_SEED = 42

#: k-means on a very small sample is arithmetic, not evidence.
MIN_ENTITIES = 20

_FEATURES = ("recency_days", "frequency", "monetary")


def run_behavioural_segmentation(
    df: pd.DataFrame,
    detection: RoleDetection,
    k_range: tuple[int, ...] = DEFAULT_K_RANGE,
    silhouette_floor: float = DEFAULT_SILHOUETTE_FLOOR,
    seed: int = DEFAULT_SEED,
) -> BusinessAnalysisResult:
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score
    from sklearn.preprocessing import StandardScaler

    analysis = AnalysisKind.BEHAVIOURAL_SEGMENTATION.value
    entity = detection.best(ColumnRole.ENTITY_ID)
    date = detection.best(ColumnRole.EVENT_DATE)
    monetary = detection.best(ColumnRole.MONETARY)

    missing = [
        label
        for label, found in (
            ("a customer-like identifier", entity),
            ("a date column", date),
            ("a non-negative monetary column", monetary),
        )
        if found is None
    ]
    if missing:
        return BusinessAnalysisResult(
            analysis=analysis, ran=False, not_run_reason=f"needs {', '.join(missing)}; not detected"
        )

    features = build_entity_features(df, entity.column, date.column, monetary.column)
    if features is None or features.entity_count < MIN_ENTITIES:
        found = 0 if features is None else features.entity_count
        return BusinessAnalysisResult(
            analysis=analysis,
            ran=False,
            not_run_reason=f"needs at least {MIN_ENTITIES} entities to cluster; found {found}",
            parameters={"minimum_entities": MIN_ENTITIES},
        )

    frame = features.frame[list(_FEATURES)].astype(float)
    scaled = StandardScaler().fit_transform(frame.values)

    scores: dict[int, float] = {}
    labels_by_k: dict[int, np.ndarray] = {}
    for k in k_range:
        if k >= len(frame):
            continue
        model = KMeans(n_clusters=k, random_state=seed, n_init=10)
        labels = model.fit_predict(scaled)
        if len(set(labels)) < 2:
            continue
        scores[k] = float(silhouette_score(scaled, labels))
        labels_by_k[k] = labels

    if not scores:
        return BusinessAnalysisResult(
            analysis=analysis,
            ran=False,
            not_run_reason="no candidate k produced more than one cluster",
            parameters={"k_range": list(k_range), "seed": seed},
        )

    best_k = max(sorted(scores), key=lambda k: scores[k])
    best_score = scores[best_k]

    if best_score < silhouette_floor:
        # The Modeling Agent's baseline gate, applied to clustering: an
        # honest "nothing separates here" beats invented segments.
        return BusinessAnalysisResult(
            analysis=analysis,
            ran=False,
            not_run_reason=(
                f"no stable segmentation found - best silhouette {best_score:.3f} at k={best_k} "
                f"is below the {silhouette_floor} floor, so the clusters overlap too much to name"
            ),
            parameters={
                "k_range": list(k_range),
                "silhouette_floor": silhouette_floor,
                "silhouette_by_k": {str(k): round(v, 6) for k, v in sorted(scores.items())},
                "best_k": best_k,
                "best_silhouette": round(best_score, 6),
                "seed": seed,
            },
        )

    labels = labels_by_k[best_k]
    profiled = features.frame.assign(cluster=labels)
    total_value = float(profiled["monetary"].sum())
    entity_count = int(len(profiled))

    findings: list[AnalysisFinding] = []
    # Order by size so the largest segment reads first; ties by label.
    sizes = profiled["cluster"].value_counts()
    for cluster in sorted(sizes.index, key=lambda c: (-int(sizes[c]), int(c))):
        members = profiled[profiled["cluster"] == cluster]
        value_total = float(members["monetary"].sum())
        findings.append(
            AnalysisFinding(
                analysis=analysis,
                finding_type=AnalysisFindingType.SEGMENT_PROFILE,
                columns=[entity.column, date.column, monetary.column],
                payload=SegmentProfilePayload(
                    # Numbered, not named. A cluster has no business meaning
                    # the data supports naming it with - the RFM analysis is
                    # where named segments come from, on a documented rule.
                    segment=f"cluster {int(cluster)}",
                    method="kmeans",
                    entity_count=int(len(members)),
                    entity_share=round(len(members) / entity_count, 6),
                    value_total=round(value_total, 6),
                    value_share=round(value_total / total_value, 6) if total_value else 0.0,
                    centre={f: round(float(members[f].mean()), 3) for f in _FEATURES},
                    silhouette=round(best_score, 6),
                ),
                evidence=AnalysisEvidence(
                    sample_size=int(len(df)),
                    entity_count=entity_count,
                    total_value=round(total_value, 6),
                    parameters={"k": best_k, "seed": seed, "silhouette": round(best_score, 6)},
                ),
            )
        )

    return BusinessAnalysisResult(
        analysis=analysis,
        ran=True,
        findings=findings,
        parameters={
            "entity_column": entity.column,
            "features": list(_FEATURES),
            "standardized": True,
            "k_range": list(k_range),
            "silhouette_floor": silhouette_floor,
            "silhouette_by_k": {str(k): round(v, 6) for k, v in sorted(scores.items())},
            "chosen_k": best_k,
            "silhouette": round(best_score, 6),
            "seed": seed,
        },
    )
