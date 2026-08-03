"""Every threshold and cap the Exploration Agent uses, in one place and all
configurable - mirrors ValidationEngine's constructor-configurable pattern
(app/validation/engine.py) rather than scattering magic numbers through the
analysis modules."""

from __future__ import annotations

from dataclasses import dataclass

from app.baseline_sanity import MIN_ROWS_FOR_CARDINALITY_FLOOR
from app.profiling import TOP_N_CATEGORIES

# Reused as-is (Part 3): the baseline only ever stores this many categorical
# values, so top-N frequency reporting here uses the exact same ceiling
# rather than a second, independently-tunable constant that could drift out
# of sync with what a baseline profile can actually support.
CATEGORICAL_TOP_N = TOP_N_CATEGORIES

DEFAULT_CORRELATION_THRESHOLD = 0.3
DEFAULT_CORRELATION_MIN_N = 30
DEFAULT_MAX_CORRELATION_COLUMNS = 50
DEFAULT_SPEARMAN_NONLINEARITY_DELTA = 0.1  # |spearman| - |pearson| beyond this => report spearman too

DEFAULT_OUTLIER_MAX_EXAMPLES = 10

DEFAULT_TREND_R_SQUARED_FLOOR = 0.3
DEFAULT_SEASONALITY_ACF_THRESHOLD = 0.5
DEFAULT_SEASONALITY_CANDIDATE_LAGS = (7, 12, 24, 30, 52, 365)

DEFAULT_MIN_ROWS_FOR_NORMALITY = 8
DEFAULT_MIN_ROWS_FOR_MODALITY = 20
DEFAULT_MODALITY_BINS = 12

DEFAULT_MISSING_PATTERN_MIN_SUPPORT = 5
DEFAULT_MISSING_PATTERN_THRESHOLD = 0.95

DEFAULT_CARDINALITY_NOTE_UNIQUE_RATIO = 0.9
DEFAULT_CARDINALITY_NOTE_NEAR_UNIQUE_RATIO = 0.99
# Below this many non-null values, "90% distinct" is trivially true and not
# an identifier signal - same guard baseline_sanity applies to the
# equivalent "cardinality == row_count" floor.
CARDINALITY_NOTE_MIN_ROWS = MIN_ROWS_FOR_CARDINALITY_FLOOR

DEFAULT_ROW_CAP = 50_000
DEFAULT_ROW_CAP_SEED = 0


@dataclass
class ExplorationConfig:
    correlation_threshold: float = DEFAULT_CORRELATION_THRESHOLD
    correlation_min_n: int = DEFAULT_CORRELATION_MIN_N
    max_correlation_columns: int = DEFAULT_MAX_CORRELATION_COLUMNS
    spearman_nonlinearity_delta: float = DEFAULT_SPEARMAN_NONLINEARITY_DELTA

    outlier_max_examples: int = DEFAULT_OUTLIER_MAX_EXAMPLES

    trend_r_squared_floor: float = DEFAULT_TREND_R_SQUARED_FLOOR
    seasonality_acf_threshold: float = DEFAULT_SEASONALITY_ACF_THRESHOLD
    seasonality_candidate_lags: tuple[int, ...] = DEFAULT_SEASONALITY_CANDIDATE_LAGS

    min_rows_for_normality: int = DEFAULT_MIN_ROWS_FOR_NORMALITY
    min_rows_for_modality: int = DEFAULT_MIN_ROWS_FOR_MODALITY
    modality_bins: int = DEFAULT_MODALITY_BINS

    missing_pattern_min_support: int = DEFAULT_MISSING_PATTERN_MIN_SUPPORT
    missing_pattern_threshold: float = DEFAULT_MISSING_PATTERN_THRESHOLD

    cardinality_note_unique_ratio: float = DEFAULT_CARDINALITY_NOTE_UNIQUE_RATIO
    cardinality_note_near_unique_ratio: float = DEFAULT_CARDINALITY_NOTE_NEAR_UNIQUE_RATIO
    cardinality_note_min_rows: int = CARDINALITY_NOTE_MIN_ROWS

    categorical_top_n: int = CATEGORICAL_TOP_N

    row_cap: int = DEFAULT_ROW_CAP
    row_cap_seed: int = DEFAULT_ROW_CAP_SEED
