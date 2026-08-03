"""Part 2.5: normality indication, skew, and a modality hint per numeric
column."""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import normaltest

from app.exploration.config import ExplorationConfig
from app.exploration.findings import DistributionShapePayload, Evidence, Finding, FindingType, ModalityHint, NormalityIndication

NORMALITY_P_THRESHOLD = 0.05
# A histogram bin counts as a peak only if it clears this fraction of the
# tallest bin - guards against noise-driven single-count bumps registering
# as a second mode.
PEAK_PROMINENCE_RATIO = 0.05
# Two candidate peaks only count as separate modes if the valley between
# them dips to at most this fraction of the smaller of the two - otherwise
# it's histogram binning noise inside a single real mode, not a second one.
VALLEY_DEPTH_RATIO = 0.4


def compute_distribution_shape(
    df: pd.DataFrame, numeric_columns: list[str], config: ExplorationConfig, sampling_seed: int | None
) -> list[Finding]:
    findings: list[Finding] = []
    for column in sorted(numeric_columns):
        series = df[column].dropna().astype(float)
        n = len(series)
        if n == 0:
            continue

        skew = float(series.skew()) if n >= 3 else None

        p_value: float | None = None
        if n < config.min_rows_for_normality:
            normality = NormalityIndication.INSUFFICIENT_DATA
        else:
            _statistic, p_value = normaltest(series)
            p_value = float(p_value)
            normality = NormalityIndication.LIKELY_NORMAL if p_value >= NORMALITY_P_THRESHOLD else NormalityIndication.LIKELY_NON_NORMAL

        if n < config.min_rows_for_modality:
            modality = ModalityHint.INSUFFICIENT_DATA
        else:
            modality = _modality_hint(series, config.modality_bins)

        findings.append(
            Finding(
                finding_type=FindingType.DISTRIBUTION_SHAPE,
                columns=[column],
                payload=DistributionShapePayload(column=column, skew=skew, normality_indication=normality, modality_hint=modality),
                evidence=Evidence(sample_size=n, p_value=p_value, sampling_seed=sampling_seed),
            )
        )
    return findings


def _modality_hint(series: pd.Series, bins: int) -> ModalityHint:
    counts, _edges = np.histogram(series, bins=bins)
    counts = counts.astype(float)
    if counts.max() == 0:
        return ModalityHint.INSUFFICIENT_DATA

    prominence_floor = counts.max() * PEAK_PROMINENCE_RATIO
    peaks = [
        i
        for i in range(len(counts))
        if counts[i] > (counts[i - 1] if i > 0 else -1)
        and counts[i] > (counts[i + 1] if i < len(counts) - 1 else -1)
        and counts[i] >= prominence_floor
    ]
    if len(peaks) <= 1:
        return ModalityHint.UNIMODAL

    # Merge peaks that aren't separated by a genuine valley - a bin-to-bin
    # wiggle inside one real mode must not count as a second mode.
    genuine_peaks = [peaks[0]]
    for peak in peaks[1:]:
        previous = genuine_peaks[-1]
        valley = counts[previous : peak + 1].min()
        smaller_peak = min(counts[previous], counts[peak])
        if valley <= smaller_peak * VALLEY_DEPTH_RATIO:
            genuine_peaks.append(peak)
        elif counts[peak] > counts[previous]:
            genuine_peaks[-1] = peak  # same mode - keep the taller of the two

    return ModalityHint.MULTIMODAL_SUSPECTED if len(genuine_peaks) > 1 else ModalityHint.UNIMODAL
