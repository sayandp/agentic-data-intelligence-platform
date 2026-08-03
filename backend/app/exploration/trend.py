"""Part 2.4: trend detection for datetime x numeric column pairs, and basic
seasonality detection via autocorrelation on the detrended series.

R-squared below the configured floor means no trend is reported at all
(recorded in skipped as insufficient fit, never emitted at low confidence).
Seasonality is always flagged as its own boolean/period on the TrendPayload
rather than folded into slope/direction, so a later agent can't present a
seasonal cycle as a monotonic trend."""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import linregress

from app.exploration.config import ExplorationConfig
from app.exploration.findings import Evidence, Finding, FindingType, SkippedEntry, TrendDirection, TrendPayload

MIN_POINTS_FOR_FIT = 3


def compute_trends(
    df: pd.DataFrame,
    datetime_columns: list[str],
    numeric_columns: list[str],
    config: ExplorationConfig,
    sampling_seed: int | None,
) -> tuple[list[Finding], list[SkippedEntry]]:
    findings: list[Finding] = []
    skipped: list[SkippedEntry] = []

    for dt_col in sorted(datetime_columns):
        for num_col in sorted(numeric_columns):
            label = f"{num_col}@{dt_col}"
            pair = pd.DataFrame({"t": df[dt_col], "y": df[num_col]}).dropna().sort_values("t")
            n = len(pair)
            if n < MIN_POINTS_FOR_FIT:
                skipped.append(SkippedEntry(column=label, reason=f"trend skipped: only {n} paired point(s), need at least {MIN_POINTS_FOR_FIT}"))
                continue

            t0 = pair["t"].iloc[0]
            x = (pair["t"] - t0).dt.total_seconds().to_numpy(dtype=float)
            y = pair["y"].astype(float).to_numpy()
            if x.max() == x.min():
                skipped.append(SkippedEntry(column=label, reason="trend skipped: all timestamps identical"))
                continue

            result = linregress(x, y)
            r_squared = float(result.rvalue**2) if np.isfinite(result.rvalue) else 0.0
            if r_squared < config.trend_r_squared_floor:
                skipped.append(
                    SkippedEntry(
                        column=label,
                        reason=f"trend skipped: R-squared={r_squared:.3f} below floor {config.trend_r_squared_floor}",
                    )
                )
                continue

            try:
                inferred_frequency = pd.infer_freq(pd.DatetimeIndex(pair["t"].unique()))
            except (ValueError, TypeError):
                inferred_frequency = None

            residuals = y - (result.intercept + result.slope * x)
            seasonality_detected, seasonality_period = _detect_seasonality(residuals, y, config)

            findings.append(
                Finding(
                    finding_type=FindingType.TREND,
                    columns=[num_col, dt_col],
                    payload=TrendPayload(
                        numeric_column=num_col,
                        datetime_column=dt_col,
                        slope=float(result.slope),
                        direction=TrendDirection.INCREASING if result.slope > 0 else TrendDirection.DECREASING,
                        start=pair["t"].iloc[0].isoformat(),
                        end=pair["t"].iloc[-1].isoformat(),
                        inferred_frequency=inferred_frequency,
                        seasonality_detected=seasonality_detected,
                        seasonality_period=seasonality_period,
                    ),
                    evidence=Evidence(sample_size=n, p_value=float(result.pvalue), r_squared=r_squared, sampling_seed=sampling_seed),
                )
            )

    return findings, skipped


def _detect_seasonality(residuals: np.ndarray, y: np.ndarray, config: ExplorationConfig) -> tuple[bool, int | None]:
    """Autocorrelation of the linearly-detrended series at a fixed set of
    plausible lags - 'basic' by design (the brief asks for basic seasonality
    detection, not a full periodogram)."""
    y_scale = float(np.std(y))
    if y_scale == 0 or float(np.std(residuals)) < 1e-6 * y_scale:
        # A near-perfect linear fit leaves nothing but floating-point noise
        # in the residuals - autocorrelation on that is numerically
        # meaningless and can spuriously "detect" a period that isn't there.
        return False, None

    n = len(residuals)
    best_lag: int | None = None
    best_acf = 0.0
    for lag in config.seasonality_candidate_lags:
        if lag < 1 or lag >= n // 2:
            continue
        a, b = residuals[:-lag], residuals[lag:]
        if a.std() == 0 or b.std() == 0:
            continue
        acf = float(np.corrcoef(a, b)[0, 1])
        if abs(acf) > abs(best_acf):
            best_acf, best_lag = acf, lag

    if best_lag is not None and abs(best_acf) >= config.seasonality_acf_threshold:
        return True, best_lag
    return False, None
