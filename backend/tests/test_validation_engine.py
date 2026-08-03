import numpy as np
import pandas as pd
import pytest

from app.contract import DataContract, SourceType
from app.profiling import BaselineProfiler
from app.validation.engine import (
    CATEGORICAL_DRIFT,
    CONNECTOR_WARNING,
    DATETIME_PARTIAL_PARSE,
    DISTRIBUTION_DRIFT,
    ENCODING_FALLBACK_OVERRULED,
    LOW_CONFIDENCE_ENCODING,
    NULL_THRESHOLD,
    SCHEMA_CONFORMANCE,
    RuleStatus,
    ValidationEngine,
)
from tests.corruption import CorruptionSuite

SEED = 42


@pytest.fixture
def clean_df():
    return pd.DataFrame(
        {
            "id": range(300),
            "amount": np.linspace(10.0, 1000.0, 300),
            "city": (["New York", "Los Angeles", "San Francisco", "Chicago"] * 75),
        }
    )


@pytest.fixture
def baseline_profile(clean_df):
    return BaselineProfiler().profile(clean_df)


def _contract(df, **kwargs) -> DataContract:
    return DataContract(data=df, source_type=SourceType.FILE, source_id="src-1", **kwargs)


def test_clean_data_produces_no_failures(clean_df, baseline_profile):
    failures = ValidationEngine().validate(_contract(clean_df), baseline_profile)
    assert failures == []


def test_rename_column_triggers_schema_conformance(clean_df, baseline_profile):
    corrupted, truth = CorruptionSuite().apply(clean_df, "rename_column", seed=SEED, column="city", new_name="town")

    failures = ValidationEngine().validate(_contract(corrupted), baseline_profile)

    families = {f.rule_failed.split(":")[0] for f in failures}
    assert families == {truth.expected_detection}
    assert any("missing_column:city" in f.rule_failed for f in failures)
    assert any("unexpected_column:town" in f.rule_failed for f in failures)


def test_change_dtype_triggers_schema_conformance(clean_df, baseline_profile):
    corrupted, truth = CorruptionSuite().apply(clean_df, "change_dtype", seed=SEED, column="amount")

    failures = ValidationEngine().validate(_contract(corrupted), baseline_profile)

    assert any(f.rule_failed.startswith(f"{SCHEMA_CONFORMANCE}:dtype_mismatch:amount") for f in failures)
    # dtype mismatch already flags the column; drift shouldn't also fire on non-numeric data
    assert not any(f.rule_failed.startswith(DISTRIBUTION_DRIFT) for f in failures)


def test_inject_nulls_above_tolerance_triggers_null_threshold(clean_df, baseline_profile):
    corrupted, truth = CorruptionSuite().apply(clean_df, "inject_nulls", seed=SEED, column="amount", rate=0.3)
    assert truth.expected_risk_level == "high"

    failures = ValidationEngine().validate(_contract(corrupted), baseline_profile)

    assert any(f.rule_failed.startswith(f"{NULL_THRESHOLD}:amount") for f in failures)


def test_inject_nulls_below_tolerance_does_not_trigger(clean_df, baseline_profile):
    corrupted, truth = CorruptionSuite().apply(clean_df, "inject_nulls", seed=SEED, column="amount", rate=0.01)
    assert truth.expected_risk_level == "low"

    failures = ValidationEngine().validate(_contract(corrupted), baseline_profile)

    assert not any(f.rule_failed.startswith(NULL_THRESHOLD) for f in failures)


def test_inject_nulls_on_integer_column_suppresses_derived_dtype_mismatch(clean_df, baseline_profile):
    """NaN has no int64 representation, so pandas upcasts the column to
    float64 as a mechanical side effect of the null injection - that's not an
    independent schema finding and must not surface as its own event. It
    should instead show up as a note on the null_threshold event."""
    corrupted, truth = CorruptionSuite().apply(clean_df, "inject_nulls", seed=SEED, column="id", rate=0.3)
    assert corrupted["id"].dtype == "float64"  # confirms the upcast actually happened

    failures = ValidationEngine().validate(_contract(corrupted), baseline_profile)

    assert not any(f.rule_failed.startswith(f"{SCHEMA_CONFORMANCE}:dtype_mismatch:id") for f in failures)
    null_failures = [f for f in failures if f.rule_failed == f"{NULL_THRESHOLD}:id"]
    assert len(null_failures) == 1
    assert null_failures[0].detail["dtype_change"]["actual_dtype"] == "float64"


def test_drop_column_triggers_schema_conformance(clean_df, baseline_profile):
    corrupted, truth = CorruptionSuite().apply(clean_df, "drop_column", seed=SEED, column="city")

    failures = ValidationEngine().validate(_contract(corrupted), baseline_profile)

    assert any("missing_column:city" in f.rule_failed for f in failures)


def test_shift_distribution_triggers_drift_via_psi(clean_df, baseline_profile):
    corrupted, truth = CorruptionSuite().apply(clean_df, "shift_distribution", seed=SEED, column="amount")

    failures = ValidationEngine().validate(_contract(corrupted), baseline_profile)

    assert any(f.rule_failed.startswith(f"{DISTRIBUTION_DRIFT}:amount") for f in failures)


def test_shift_distribution_triggers_drift_via_ks_alternative(clean_df, baseline_profile):
    corrupted, truth = CorruptionSuite().apply(clean_df, "shift_distribution", seed=SEED, column="amount")

    failures = ValidationEngine(drift_test="ks").validate(_contract(corrupted), baseline_profile)

    assert any(f.rule_failed.startswith(f"{DISTRIBUTION_DRIFT}:amount") for f in failures)


def test_shift_distribution_on_integer_column_suppresses_derived_dtype_mismatch(clean_df, baseline_profile):
    """Scaling an int64 column by a float factor upcasts it to float64 as a
    mechanical side effect - same category of artifact as the null-injection
    case, just via arithmetic instead of NaN. Must fold into the drift event,
    not show up as its own schema_conformance failure."""
    corrupted, truth = CorruptionSuite().apply(
        clean_df, "shift_distribution", seed=SEED, column="id", mode="scale", factor=5.0
    )
    assert corrupted["id"].dtype == "float64"

    failures = ValidationEngine().validate(_contract(corrupted), baseline_profile)

    assert not any(f.rule_failed.startswith(f"{SCHEMA_CONFORMANCE}:dtype_mismatch:id") for f in failures)
    drift_failures = [f for f in failures if f.rule_failed == f"{DISTRIBUTION_DRIFT}:id"]
    assert len(drift_failures) == 1
    assert drift_failures[0].detail["dtype_change"]["actual_dtype"] == "float64"


def test_truncate_rows_triggers_schema_conformance_row_count_drop(clean_df, baseline_profile):
    corrupted, truth = CorruptionSuite().apply(clean_df, "truncate_rows", seed=SEED, fraction=0.5)

    failures = ValidationEngine().validate(_contract(corrupted), baseline_profile)

    assert any("row_count_drop" in f.rule_failed for f in failures)


def test_whitespace_case_corruption_is_detected_via_categorical_drift(clean_df, baseline_profile):
    """Closes the gap flagged in the earlier Part 3 report: the categorical-drift
    rule fires specifically because normalizing (strip + casefold) the mangled
    values collapses them back onto the exact baseline value set."""
    corrupted, truth = CorruptionSuite().apply(
        clean_df, "inject_whitespace_case", seed=SEED, column="city", rate=1.0
    )
    assert truth.expected_detection == "categorical_drift"

    failures = ValidationEngine().validate(_contract(corrupted), baseline_profile)

    assert len(failures) == 1
    assert failures[0].rule_failed == "categorical_drift:city"
    assert failures[0].detail["drift_kind"] == "whitespace_or_case"


def test_categorical_drift_does_not_fire_on_clean_data(clean_df, baseline_profile):
    failures = ValidationEngine().validate(_contract(clean_df), baseline_profile)
    assert not any(f.rule_failed.startswith("categorical_drift") for f in failures)


def test_categorical_drift_does_not_fire_on_genuinely_new_category(clean_df, baseline_profile):
    """A real new category (not a whitespace/case variant of an existing one)
    is NOT what this rule is scoped to catch - it should stay silent rather
    than guess."""
    corrupted = clean_df.copy()
    corrupted.loc[0, "city"] = "Miami"

    failures = ValidationEngine().validate(_contract(corrupted), baseline_profile)

    assert not any(f.rule_failed.startswith("categorical_drift") for f in failures)


def test_categorical_drift_silent_above_cardinality_ceiling():
    """Above the ceiling, baseline's top-N is a truncated sample of the real
    value set, so exact-set-equality can never hold - even on a genuinely
    corrupted column, the rule must stay silent rather than guess. This is
    the documented coverage boundary, not a false negative bug."""
    high_cardinality_values = [f"city-{i}" for i in range(30)]  # cardinality 30 > default ceiling (10)
    clean = pd.DataFrame({"region": high_cardinality_values * 10})
    profile = BaselineProfiler().profile(clean)

    corrupted = clean.copy()
    corrupted["region"] = corrupted["region"].str.upper()  # unambiguous whitespace/case corruption

    failures = ValidationEngine().validate(_contract(corrupted), profile)

    assert not any(f.rule_failed.startswith("categorical_drift") for f in failures)


def test_categorical_drift_ceiling_is_configurable(clean_df, baseline_profile):
    """Confirms the ceiling is an enforced, tunable parameter rather than a
    hardcoded constant: `city`'s cardinality (4) clears the default ceiling
    (10) and gets caught, but a stricter ceiling of 3 suppresses it - useful
    if an operator wants to be more conservative than the profiler's default
    top-N. (Raising the ceiling past 10 wouldn't help in practice: baseline's
    top_values is truncated to TOP_N_CATEGORIES=10 at profiling time
    regardless of what the engine's ceiling allows at validation time.)"""
    corrupted, _truth = CorruptionSuite().apply(
        clean_df, "inject_whitespace_case", seed=SEED, column="city", rate=1.0
    )

    default_failures = ValidationEngine().validate(_contract(corrupted), baseline_profile)
    assert any(f.rule_failed == "categorical_drift:city" for f in default_failures)

    strict_failures = ValidationEngine(categorical_drift_cardinality_ceiling=3).validate(
        _contract(corrupted), baseline_profile
    )
    assert not any(f.rule_failed.startswith("categorical_drift") for f in strict_failures)


def test_composed_rename_and_dtype_corruption_produces_both_schema_failures(clean_df, baseline_profile):
    """A column gets renamed AND dtype-shifts in the same bad export."""
    specs = [
        ("rename_column", {"column": "city", "new_name": "town"}),
        ("change_dtype", {"column": "amount"}),
    ]
    corrupted, truths = CorruptionSuite().compose(clean_df, specs, seed=SEED)

    failures = ValidationEngine().validate(_contract(corrupted), baseline_profile)

    families = {f.rule_failed.split(":")[0] for f in failures}
    assert families == {SCHEMA_CONFORMANCE}
    assert any("missing_column:city" in f.rule_failed for f in failures)
    assert any("unexpected_column:town" in f.rule_failed for f in failures)
    assert any(f.rule_failed.startswith(f"{SCHEMA_CONFORMANCE}:dtype_mismatch:amount") for f in failures)
    # one validation_event per ground truth's worth of underlying breakage,
    # not a 1:1 event-per-corruption - composition doesn't collapse detection
    assert len(truths) == 2


def test_low_confidence_encoding_triggers_failure(clean_df, baseline_profile):
    contract = _contract(
        clean_df,
        detected_encoding="windows-1252",
        encoding_confidence=0.3,
        connector_metadata={"encoding_used": "windows-1252"},
    )

    failures = ValidationEngine().validate(contract, baseline_profile)

    assert any(f.rule_failed.startswith(LOW_CONFIDENCE_ENCODING) for f in failures)
    assert not any(f.rule_failed.startswith(ENCODING_FALLBACK_OVERRULED) for f in failures)


def test_high_confidence_encoding_does_not_trigger(clean_df, baseline_profile):
    contract = _contract(
        clean_df, detected_encoding="utf-8", encoding_confidence=0.99, connector_metadata={"encoding_used": "utf-8"}
    )

    failures = ValidationEngine().validate(contract, baseline_profile)

    assert not any(f.rule_failed.startswith(LOW_CONFIDENCE_ENCODING) for f in failures)


def test_encoding_fallback_overruled_triggers_independently_of_confidence(clean_df, baseline_profile):
    """chardet was confident, but its guess didn't actually decode - the fallback
    chain (Phase 2 Part 0) had to use a different encoding. High-precision signal."""
    contract = _contract(
        clean_df,
        detected_encoding="ascii",
        encoding_confidence=0.95,  # high confidence
        connector_metadata={"encoding_used": "latin-1"},  # but fallback had to kick in
    )

    failures = ValidationEngine().validate(contract, baseline_profile)

    assert any(f.rule_failed.startswith(ENCODING_FALLBACK_OVERRULED) for f in failures)
    assert not any(f.rule_failed.startswith(LOW_CONFIDENCE_ENCODING) for f in failures)


def test_both_encoding_checks_can_fire_together(clean_df, baseline_profile):
    contract = _contract(
        clean_df,
        detected_encoding="ascii",
        encoding_confidence=0.2,
        connector_metadata={"encoding_used": "latin-1"},
    )

    failures = ValidationEngine().validate(contract, baseline_profile)

    families = {f.rule_failed.split(":")[0] for f in failures}
    assert LOW_CONFIDENCE_ENCODING in families
    assert ENCODING_FALLBACK_OVERRULED in families


def test_no_encoding_info_skips_both_encoding_checks(clean_df, baseline_profile):
    failures = ValidationEngine().validate(_contract(clean_df), baseline_profile)
    assert not any(f.rule_failed.startswith(LOW_CONFIDENCE_ENCODING) for f in failures)
    assert not any(f.rule_failed.startswith(ENCODING_FALLBACK_OVERRULED) for f in failures)


def test_check_connector_warnings_surfaces_each_warning(clean_df):
    contract = _contract(clean_df, connector_metadata={"warnings": ["workbook has 2 sheets, read first only"]})

    failures = ValidationEngine.check_connector_warnings(contract)

    assert len(failures) == 1
    assert failures[0].rule_failed == f"{CONNECTOR_WARNING}:0"
    assert failures[0].detail["message"] == "workbook has 2 sheets, read first only"


def test_check_connector_warnings_empty_when_none(clean_df):
    assert ValidationEngine.check_connector_warnings(_contract(clean_df)) == []


def test_check_connector_warnings_does_not_require_a_baseline(clean_df):
    """Unlike every other check, this one is callable with just a contract -
    it must work on a source's first-ever ingest, before any baseline exists."""
    contract = _contract(clean_df, connector_metadata={"warnings": ["multi-sheet workbook"]})
    failures = ValidationEngine.check_connector_warnings(contract)
    assert len(failures) == 1


# ---- three-state rule outcomes: passed / failed / not_applicable ----
#
# Silent absence must not mean "passed" - a rule the engine couldn't
# evaluate has to say so explicitly, distinctly from a rule it evaluated
# and found clean. This is what app/repair.py's do-no-harm check relies on
# to tell "revealed by a fix" (not_applicable -> failed) apart from
# "regressed by a fix" (passed -> failed).


def _outcome(outcomes, rule_id):
    matches = [o for o in outcomes if o.rule_id == rule_id]
    assert len(matches) == 1, f"expected exactly one outcome for {rule_id!r}, got {len(matches)}"
    return matches[0]


def test_clean_data_produces_passed_outcomes_not_just_an_empty_list(clean_df, baseline_profile):
    """validate() returning [] on clean data has always been true - the
    three-state view must show WHY: every rule was actually evaluated and
    passed, not silently skipped."""
    outcomes = ValidationEngine().evaluate(_contract(clean_df), baseline_profile)

    assert outcomes  # something was actually evaluated
    assert all(o.status != RuleStatus.FAILED for o in outcomes)
    assert any(o.status == RuleStatus.PASSED for o in outcomes)
    assert _outcome(outcomes, f"{DISTRIBUTION_DRIFT}:amount").status == RuleStatus.PASSED
    assert _outcome(outcomes, f"{CATEGORICAL_DRIFT}:city").status == RuleStatus.PASSED
    assert _outcome(outcomes, f"{SCHEMA_CONFORMANCE}:dtype_mismatch:amount").status == RuleStatus.PASSED
    assert _outcome(outcomes, f"{NULL_THRESHOLD}:amount").status == RuleStatus.PASSED


def test_drift_on_a_non_numeric_column_is_not_applicable_not_passed(clean_df, baseline_profile):
    """The exact case named in the brief: a dtype corruption makes
    distribution_drift uncheckable for that column. It must show up as
    not_applicable - never silently absent, and never conflated with a
    clean pass."""
    corrupted, _truth = CorruptionSuite().apply(clean_df, "change_dtype", seed=SEED, column="amount")

    outcomes = ValidationEngine().evaluate(_contract(corrupted), baseline_profile)

    drift_outcome = _outcome(outcomes, f"{DISTRIBUTION_DRIFT}:amount")
    assert drift_outcome.status == RuleStatus.NOT_APPLICABLE
    assert drift_outcome.status != RuleStatus.PASSED

    # validate() (the pre-existing FAILED-only view) must not surface this
    # as a failure either - not_applicable is its own state, not a failure.
    failures = ValidationEngine().validate(_contract(corrupted), baseline_profile)
    assert not any(f.rule_failed == f"{DISTRIBUTION_DRIFT}:amount" for f in failures)


def test_categorical_drift_above_cardinality_ceiling_is_not_applicable_not_passed():
    """The other case named in the brief: above the top-N ceiling, the
    baseline's stored value set is a truncated sample, so the rule can't be
    evaluated at all - not_applicable, not a silent pass."""
    high_cardinality_values = [f"city-{i}" for i in range(30)]  # cardinality 30 > default ceiling (10)
    clean = pd.DataFrame({"region": high_cardinality_values * 10})
    profile = BaselineProfiler().profile(clean)
    corrupted = clean.copy()
    corrupted["region"] = corrupted["region"].str.upper()

    outcomes = ValidationEngine().evaluate(_contract(corrupted), profile)

    region_outcome = _outcome(outcomes, f"{CATEGORICAL_DRIFT}:region")
    assert region_outcome.status == RuleStatus.NOT_APPLICABLE
    assert region_outcome.status != RuleStatus.PASSED


def test_dtype_mismatch_not_applicable_when_column_missing(clean_df, baseline_profile):
    """dtype can't be compared for a column that isn't there - not_applicable,
    distinctly from the missing_column failure that already covers it. This
    is exactly what lets a rename REVEAL a dtype mismatch that was masked
    while the column existed under the wrong name."""
    corrupted = clean_df.drop(columns=["amount"])

    outcomes = ValidationEngine().evaluate(_contract(corrupted), baseline_profile)

    assert _outcome(outcomes, f"{SCHEMA_CONFORMANCE}:missing_column:amount").status == RuleStatus.FAILED
    assert _outcome(outcomes, f"{SCHEMA_CONFORMANCE}:dtype_mismatch:amount").status == RuleStatus.NOT_APPLICABLE


def test_row_count_drop_not_applicable_with_empty_baseline(clean_df):
    empty_baseline = {"row_count": 0, "columns": {}}

    outcomes = ValidationEngine().evaluate(_contract(clean_df), empty_baseline)

    assert _outcome(outcomes, f"{SCHEMA_CONFORMANCE}:row_count_drop").status == RuleStatus.NOT_APPLICABLE


# ---------------------------------------------------------------------------
# Phase 7.5 Part 1: a column excluded from the baseline (identifier-shaped)
# cannot be drift-checked or dtype-checked - every rule family that would
# otherwise apply to it returns an EXPLICIT not_applicable, never a silent
# absence from the outcome list, and it is never flagged as "unexpected"
# just for existing in the live data.
# ---------------------------------------------------------------------------


@pytest.fixture
def baseline_profile_with_excluded_column(clean_df):
    df_with_id_string = clean_df.assign(order_ref=[f"order-{i}" for i in range(len(clean_df))])
    return BaselineProfiler().profile(df_with_id_string)


def test_excluded_column_present_in_profile(baseline_profile_with_excluded_column):
    excluded = {e["column"] for e in baseline_profile_with_excluded_column["excluded_columns"]}
    assert "order_ref" in excluded
    assert "order_ref" not in baseline_profile_with_excluded_column["columns"]


def test_excluded_column_is_not_applicable_across_every_rule_family(clean_df, baseline_profile_with_excluded_column):
    current = clean_df.assign(order_ref=[f"order-{i}" for i in range(len(clean_df))])
    outcomes = ValidationEngine().evaluate(_contract(current), baseline_profile_with_excluded_column)

    assert _outcome(outcomes, f"{SCHEMA_CONFORMANCE}:missing_column:order_ref").status == RuleStatus.NOT_APPLICABLE
    assert _outcome(outcomes, f"{SCHEMA_CONFORMANCE}:dtype_mismatch:order_ref").status == RuleStatus.NOT_APPLICABLE
    assert _outcome(outcomes, f"{NULL_THRESHOLD}:order_ref").status == RuleStatus.NOT_APPLICABLE
    assert _outcome(outcomes, f"{DISTRIBUTION_DRIFT}:order_ref").status == RuleStatus.NOT_APPLICABLE
    assert _outcome(outcomes, f"{CATEGORICAL_DRIFT}:order_ref").status == RuleStatus.NOT_APPLICABLE

    # never a spurious FAILED, and never silently absent from the list either
    matches = [o for o in outcomes if o.column == "order_ref"]
    assert matches
    assert all(o.status == RuleStatus.NOT_APPLICABLE for o in matches)


def test_excluded_column_is_never_reported_as_unexpected(clean_df, baseline_profile_with_excluded_column):
    """A column deliberately excluded from the baseline is a perfectly
    normal live column, not one this baseline has an opinion about - it
    must never be flagged as unexpected_column just for existing."""
    current = clean_df.assign(order_ref=[f"order-{i}" for i in range(len(clean_df))])
    outcomes = ValidationEngine().evaluate(_contract(current), baseline_profile_with_excluded_column)
    unexpected_rule_ids = {o.rule_id for o in outcomes if o.rule_id.startswith(f"{SCHEMA_CONFORMANCE}:unexpected_column:")}
    assert f"{SCHEMA_CONFORMANCE}:unexpected_column:order_ref" not in unexpected_rule_ids


# ---------------------------------------------------------------------------
# Phase 7.5 Part 2: datetime_partial_parse - a column that's SOME dates and
# SOME not is a data-quality signal, never silently coerced-and-nulled.
# Baseline-independent, same shape as the encoding checks.
# ---------------------------------------------------------------------------


def test_partial_parse_column_is_failed_not_applicable_elsewhere():
    contract = _contract(
        pd.DataFrame({"maybe_date": ["2022-01-01", "not a date", "2022-01-03"]}),
        connector_metadata={"datetime_parse_attempts": {"maybe_date": {"parse_rate": 2 / 3, "coerced": False}}},
    )
    outcomes = ValidationEngine().evaluate(contract, {"row_count": 0, "columns": {}})
    outcome = _outcome(outcomes, f"{DATETIME_PARTIAL_PARSE}:maybe_date")
    assert outcome.status == RuleStatus.FAILED
    assert outcome.detail["coerced"] is False


def test_fully_coerced_datetime_column_passes():
    contract = _contract(
        pd.DataFrame({"order_date": pd.to_datetime(["2022-01-01", "2022-01-02"])}),
        connector_metadata={"datetime_parse_attempts": {"order_date": {"parse_rate": 1.0, "coerced": True}}},
    )
    outcomes = ValidationEngine().evaluate(contract, {"row_count": 0, "columns": {}})
    outcome = _outcome(outcomes, f"{DATETIME_PARTIAL_PARSE}:order_date")
    assert outcome.status == RuleStatus.PASSED


def test_no_datetime_parse_attempts_produces_no_outcomes_for_this_rule(clean_df, baseline_profile):
    outcomes = ValidationEngine().evaluate(_contract(clean_df), baseline_profile)
    assert not [o for o in outcomes if o.rule_id.startswith(f"{DATETIME_PARTIAL_PARSE}:")]
