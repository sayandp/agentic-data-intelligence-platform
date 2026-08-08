"""Deterministic validation: compares an incoming DataContract against the
active baseline and reports failures. Detects and logs only - no diagnosis,
no fixes. That's Part 4 (Diagnostic Agent) and Part 5 (Action Executor).

THREE-STATE RULE OUTCOMES. Every rule this engine knows how to check is
evaluated to exactly one of passed / failed / not_applicable
(`evaluate()` -> `RuleOutcome`) - never silently omitted. `not_applicable`
covers rules that could not be evaluated at all (drift on a column that
isn't numeric-typed right now, categorical drift above the cardinality
ceiling, a dataset-level check with no baseline to compare against). This
distinction is load-bearing for `app/repair.py`'s do-no-harm check: a rule
that flips from not_applicable to failed after a fix was REVEALED by it,
not caused by it, and the two must never be confused with each other by
being collapsed into "absent == passed".

`validate()` is the pre-existing, narrower view - just the FAILED subset,
in the same `ValidationFailure` shape every other module in this codebase
already consumes (correlation, diagnosis, the gate). It is implemented as a
filter over `evaluate()`, not a second independent set of checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd

from app.connectors.file_connector import encodings_are_equivalent
from app.contract import DataContract
from app.profiling import TOP_N_CATEGORIES

SCHEMA_CONFORMANCE = "schema_conformance"
NULL_THRESHOLD = "null_threshold"
DISTRIBUTION_DRIFT = "distribution_drift"
CATEGORICAL_DRIFT = "categorical_drift"
LOW_CONFIDENCE_ENCODING = "low_confidence_encoding"
ENCODING_FALLBACK_OVERRULED = "encoding_fallback_overruled"
CONNECTOR_WARNING = "connector_warning"
DATETIME_PARTIAL_PARSE = "datetime_partial_parse"

# Reused verbatim for every rule family's NOT_APPLICABLE outcome on a
# baseline-excluded (identifier-shaped) column - app/profiling.py excludes
# such a column before this engine ever sees it, so it can never be
# drift-checked or dtype-checked; this is what makes that explicit rather
# than a silent absence from the outcome list (Phase 7.5).
_EXCLUDED_COLUMN_REASON = "column excluded from baseline profiling (identifier-shaped)"

DEFAULT_NULL_TOLERANCE = 0.05
DEFAULT_PSI_THRESHOLD = 0.2
DEFAULT_ROW_COUNT_DROP_TOLERANCE = 0.1
DEFAULT_ENCODING_CONFIDENCE_THRESHOLD = 0.5
# The baseline only ever stores the top-N categorical values (app/profiling.py),
# so above that true cardinality the stored set is a truncated sample, not the
# full value set - exact-set-equality can never succeed even on clean data.
# See the cardinality-ceiling note on _check_categorical_drift below.
DEFAULT_CATEGORICAL_DRIFT_CARDINALITY_CEILING = TOP_N_CATEGORIES

# PSI compares two probability distributions via a log-ratio; a zero-count bin
# on either side makes that ratio 0 or infinite, so empty bins are floored to
# this instead of being treated as "no data here".
PSI_EPSILON = 1e-4


class RuleStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


@dataclass
class RuleOutcome:
    """One rule, evaluated against one subject (a column, or the dataset as
    a whole), exactly once. `rule_id` is the same string `ValidationFailure`
    has always used as `rule_failed` - stable across a pre-fix/post-fix pair
    of `evaluate()` calls against the same baseline, which is what makes it
    usable as a do-no-harm comparison key."""

    rule_id: str
    status: RuleStatus
    column: str | None = None
    detail: dict | None = None


@dataclass
class ValidationFailure:
    rule_failed: str
    column: str | None = None
    detail: dict | None = None


class ValidationEngine:
    def __init__(
        self,
        null_tolerance: float = DEFAULT_NULL_TOLERANCE,
        psi_threshold: float = DEFAULT_PSI_THRESHOLD,
        row_count_drop_tolerance: float = DEFAULT_ROW_COUNT_DROP_TOLERANCE,
        encoding_confidence_threshold: float = DEFAULT_ENCODING_CONFIDENCE_THRESHOLD,
        drift_test: str = "psi",
        categorical_drift_cardinality_ceiling: int = DEFAULT_CATEGORICAL_DRIFT_CARDINALITY_CEILING,
    ):
        if drift_test not in ("psi", "ks"):
            raise ValueError(f"drift_test must be 'psi' or 'ks', got {drift_test!r}")
        self.null_tolerance = null_tolerance
        self.psi_threshold = psi_threshold
        self.row_count_drop_tolerance = row_count_drop_tolerance
        self.encoding_confidence_threshold = encoding_confidence_threshold
        self.drift_test = drift_test
        self.categorical_drift_cardinality_ceiling = categorical_drift_cardinality_ceiling

    def evaluate(self, contract: DataContract, baseline_profile: dict) -> list[RuleOutcome]:
        """Every rule this engine knows how to check, each evaluated to
        exactly one of passed/failed/not_applicable. Never omits a rule
        instance just because it happened to not apply this time."""
        schema_outcomes = self._check_schema(contract, baseline_profile)
        null_outcomes = self._check_nulls(contract, baseline_profile)
        drift_outcomes = self._check_drift(contract, baseline_profile)
        categorical_outcomes = self._check_categorical_drift(contract, baseline_profile)
        encoding_outcomes = self._check_encoding(contract)
        datetime_parse_outcomes = self._check_datetime_partial_parse(contract)

        # A NaN insertion or an arithmetic scale/offset forces pandas to upcast
        # an int column to float64 as a mechanical side effect - it is not an
        # independent schema finding, and treating it as one both doubles
        # downstream diagnosis calls and risks a spurious "safe_type_cast"
        # auto-fix sitting next to the real (and correctly escalated) failure.
        schema_outcomes = self._fold_derived_dtype_mismatches(schema_outcomes, null_outcomes, drift_outcomes)

        return schema_outcomes + null_outcomes + drift_outcomes + categorical_outcomes + encoding_outcomes + datetime_parse_outcomes

    def validate(self, contract: DataContract, baseline_profile: dict) -> list[ValidationFailure]:
        """The FAILED subset of evaluate(), in the shape every other module
        already consumes. Every caller except app/repair.py's do-no-harm
        check should keep using this - it is not being replaced, only
        derived from a wider evaluation instead of independently computed."""
        return [
            ValidationFailure(rule_failed=outcome.rule_id, column=outcome.column, detail=outcome.detail)
            for outcome in self.evaluate(contract, baseline_profile)
            if outcome.status == RuleStatus.FAILED
        ]

    @staticmethod
    def check_connector_warnings(contract: DataContract) -> list[ValidationFailure]:
        """Surfaces connector-level warnings (e.g. FileConnector's multi-sheet
        Excel notice) as validation_events, without blocking ingestion and
        without needing a baseline. Callable even on a source's first-ever
        ingest, before any baseline exists. Informational, not a rule with a
        pass/fail/not_applicable identity - kept outside evaluate()."""
        warnings = contract.connector_metadata.get("warnings") or []
        return [
            ValidationFailure(rule_failed=f"{CONNECTOR_WARNING}:{i}", detail={"message": message})
            for i, message in enumerate(warnings)
        ]

    @staticmethod
    def _fold_derived_dtype_mismatches(
        schema_outcomes: list[RuleOutcome],
        null_outcomes: list[RuleOutcome],
        drift_outcomes: list[RuleOutcome],
    ) -> list[RuleOutcome]:
        null_failed_by_column = {o.column: o for o in null_outcomes if o.column and o.status == RuleStatus.FAILED}
        drift_failed_by_column = {o.column: o for o in drift_outcomes if o.column and o.status == RuleStatus.FAILED}

        kept: list[RuleOutcome] = []
        for outcome in schema_outcomes:
            detail = outcome.detail or {}
            is_int_to_float = (
                outcome.status == RuleStatus.FAILED
                and ":dtype_mismatch:" in outcome.rule_id
                and str(detail.get("expected_dtype", "")).lower().startswith("int")
                and detail.get("actual_dtype") == "float64"
            )
            if not is_int_to_float or not outcome.column:
                kept.append(outcome)
                continue

            if outcome.column in null_failed_by_column:
                target = null_failed_by_column[outcome.column]
                reason = "null values inserted into an integer column force a float64 upcast"
            elif outcome.column in drift_failed_by_column:
                target = drift_failed_by_column[outcome.column]
                reason = "an arithmetic scale/offset on an integer column forces a float64 upcast"
            else:
                kept.append(outcome)
                continue

            target.detail = {
                **(target.detail or {}),
                "dtype_change": {
                    "expected_dtype": detail.get("expected_dtype"),
                    "actual_dtype": detail.get("actual_dtype"),
                    "note": f"derived artifact, not an independent schema failure: {reason}",
                },
            }
        return kept

    # -- helper: NOT_APPLICABLE for every baseline-excluded (identifier-shaped) column --

    @staticmethod
    def _excluded_column_outcomes(baseline_profile: dict, rule_prefix: str) -> list[RuleOutcome]:
        """One explicit NOT_APPLICABLE per excluded column, for whichever
        rule family is calling (rule_prefix already includes any
        sub-family, e.g. f"{SCHEMA_CONFORMANCE}:dtype_mismatch"). A column
        excluded from the baseline cannot be drift-checked or
        dtype-checked - this is what makes that a recorded fact rather than
        a silent absence from the outcome list."""
        return [
            RuleOutcome(f"{rule_prefix}:{entry['column']}", RuleStatus.NOT_APPLICABLE, column=entry["column"], detail={"reason": _EXCLUDED_COLUMN_REASON})
            for entry in baseline_profile.get("excluded_columns", [])
        ]

    # -- schema conformance: columns present, no extras, dtypes match, row count in range --

    def _check_schema(self, contract: DataContract, baseline_profile: dict) -> list[RuleOutcome]:
        outcomes: list[RuleOutcome] = []
        baseline_columns: dict = baseline_profile.get("columns", {})
        current_columns = contract.column_types
        excluded_columns = {entry["column"] for entry in baseline_profile.get("excluded_columns", [])}

        missing = set(baseline_columns) - set(current_columns)
        # A column excluded from the baseline as identifier-shaped is
        # neither "missing" (never expected in the first place) nor
        # "unexpected" (it's a perfectly normal live column, just not one
        # this baseline has an opinion about) - it gets its own explicit
        # NOT_APPLICABLE below instead.
        unexpected = set(current_columns) - set(baseline_columns) - excluded_columns

        # missing_column: one instance per baseline column - always
        # evaluable as long as a baseline exists, so never not_applicable.
        for col in sorted(baseline_columns):
            rule_id = f"{SCHEMA_CONFORMANCE}:missing_column:{col}"
            if col in missing:
                outcomes.append(
                    RuleOutcome(rule_id, RuleStatus.FAILED, column=col, detail={"expected_dtype": baseline_columns[col]["dtype"]})
                )
            else:
                outcomes.append(RuleOutcome(rule_id, RuleStatus.PASSED, column=col))
        outcomes += self._excluded_column_outcomes(baseline_profile, f"{SCHEMA_CONFORMANCE}:missing_column")

        # unexpected_column: only a coherent rule instance for a column that
        # actually exists right now - there's no meaningful "passed" state
        # to enumerate ahead of time for every column name that isn't there.
        for col in sorted(unexpected):
            outcomes.append(
                RuleOutcome(
                    f"{SCHEMA_CONFORMANCE}:unexpected_column:{col}",
                    RuleStatus.FAILED,
                    column=col,
                    detail={"actual_dtype": current_columns[col]},
                )
            )

        # dtype_mismatch: one instance per baseline column - not_applicable
        # when the column isn't present at all (dtype of nothing can't be
        # compared; that's what makes a rename able to REVEAL a dtype
        # mismatch that a missing_column failure was masking).
        for col in sorted(baseline_columns):
            rule_id = f"{SCHEMA_CONFORMANCE}:dtype_mismatch:{col}"
            if col not in current_columns:
                outcomes.append(
                    RuleOutcome(rule_id, RuleStatus.NOT_APPLICABLE, column=col, detail={"reason": "column not present"})
                )
                continue
            expected_dtype = baseline_columns[col]["dtype"]
            actual_dtype = current_columns[col]
            if expected_dtype != actual_dtype:
                outcomes.append(
                    RuleOutcome(
                        rule_id, RuleStatus.FAILED, column=col, detail={"expected_dtype": expected_dtype, "actual_dtype": actual_dtype}
                    )
                )
            else:
                outcomes.append(
                    RuleOutcome(
                        rule_id, RuleStatus.PASSED, column=col, detail={"expected_dtype": expected_dtype, "actual_dtype": actual_dtype}
                    )
                )
        outcomes += self._excluded_column_outcomes(baseline_profile, f"{SCHEMA_CONFORMANCE}:dtype_mismatch")

        # row_count_drop: dataset-level, not_applicable when the baseline
        # itself has no row count to compare against.
        rule_id = f"{SCHEMA_CONFORMANCE}:row_count_drop"
        baseline_row_count = baseline_profile.get("row_count", 0)
        if baseline_row_count > 0:
            drop_fraction = (baseline_row_count - contract.row_count) / baseline_row_count
            detail = {
                "baseline_row_count": baseline_row_count,
                "current_row_count": contract.row_count,
                "drop_fraction": drop_fraction,
                "tolerance": self.row_count_drop_tolerance,
            }
            status = RuleStatus.FAILED if drop_fraction > self.row_count_drop_tolerance else RuleStatus.PASSED
            outcomes.append(RuleOutcome(rule_id, status, detail=detail))
        else:
            outcomes.append(RuleOutcome(rule_id, RuleStatus.NOT_APPLICABLE, detail={"reason": "baseline row_count is 0"}))

        return outcomes

    # -- null thresholds: per-column null rate must stay within baseline + tolerance --

    def _check_nulls(self, contract: DataContract, baseline_profile: dict) -> list[RuleOutcome]:
        outcomes: list[RuleOutcome] = []
        baseline_columns: dict = baseline_profile.get("columns", {})
        df = contract.data

        for col, base_entry in baseline_columns.items():
            rule_id = f"{NULL_THRESHOLD}:{col}"
            if col not in df.columns:
                # already reported as missing_column by _check_schema - not
                # a null-rate question until the column exists again.
                outcomes.append(RuleOutcome(rule_id, RuleStatus.NOT_APPLICABLE, column=col, detail={"reason": "column not present"}))
                continue
            current_null_rate = float(df[col].isna().mean()) if len(df) else 0.0
            baseline_null_rate = base_entry.get("null_rate", 0.0)
            detail = {
                "baseline_null_rate": baseline_null_rate,
                "current_null_rate": current_null_rate,
                "tolerance": self.null_tolerance,
            }
            status = RuleStatus.FAILED if current_null_rate > baseline_null_rate + self.null_tolerance else RuleStatus.PASSED
            outcomes.append(RuleOutcome(rule_id, status, column=col, detail=detail))
        outcomes += self._excluded_column_outcomes(baseline_profile, NULL_THRESHOLD)
        return outcomes

    # -- distribution drift: PSI (default) or KS-test per numeric column --

    def _check_drift(self, contract: DataContract, baseline_profile: dict) -> list[RuleOutcome]:
        outcomes: list[RuleOutcome] = []
        baseline_columns: dict = baseline_profile.get("columns", {})
        df = contract.data

        for col, base_entry in baseline_columns.items():
            rule_id = f"{DISTRIBUTION_DRIFT}:{col}"
            if base_entry.get("kind") != "numeric":
                outcomes.append(
                    RuleOutcome(rule_id, RuleStatus.NOT_APPLICABLE, column=col, detail={"reason": "baseline column is not numeric"})
                )
                continue
            if col not in df.columns:
                outcomes.append(RuleOutcome(rule_id, RuleStatus.NOT_APPLICABLE, column=col, detail={"reason": "column not present"}))
                continue
            if not pd.api.types.is_numeric_dtype(df[col]):
                # dtype mismatch already reported by _check_schema - drift
                # on a non-numeric column can't be scored. If a later fix
                # makes this column numeric again, THIS is the check that
                # can newly reveal a drift that was always there but
                # unobservable until that instant (see app/repair.py).
                outcomes.append(
                    RuleOutcome(
                        rule_id, RuleStatus.NOT_APPLICABLE, column=col, detail={"reason": "column is not numeric-typed in the current data"}
                    )
                )
                continue

            current_values = df[col].dropna().astype(float)
            if current_values.empty:
                outcomes.append(RuleOutcome(rule_id, RuleStatus.NOT_APPLICABLE, column=col, detail={"reason": "no non-null values to compare"}))
                continue

            if self.drift_test == "ks":
                score = self._ks_score(base_entry, current_values)
                method = "ks_statistic"
            else:
                score = self._psi_score(base_entry, current_values)
                method = "psi"

            if score is None:
                outcomes.append(
                    RuleOutcome(rule_id, RuleStatus.NOT_APPLICABLE, column=col, detail={"reason": f"{method} could not be computed"})
                )
                continue

            detail = {"method": method, "score": score, "threshold": self.psi_threshold}
            status = RuleStatus.FAILED if score > self.psi_threshold else RuleStatus.PASSED
            outcomes.append(RuleOutcome(rule_id, status, column=col, detail=detail))
        outcomes += self._excluded_column_outcomes(baseline_profile, DISTRIBUTION_DRIFT)
        return outcomes

    @staticmethod
    def _psi_score(base_entry: dict, current_values: pd.Series) -> float | None:
        histogram = base_entry.get("histogram") or {}
        bin_edges = histogram.get("bin_edges")
        baseline_counts = histogram.get("counts")
        if not bin_edges or not baseline_counts:
            return None

        bin_edges = np.array(bin_edges, dtype=float)
        baseline_counts = np.array(baseline_counts, dtype=float)

        # Values outside the baseline's observed range would otherwise be
        # dropped by np.histogram entirely; capping the outer edges at +/-inf
        # routes them into the extreme bins instead, so a distribution that
        # has moved off the baseline's range still registers as drift.
        current_edges = bin_edges.copy()
        current_edges[0] = -np.inf
        current_edges[-1] = np.inf
        current_counts, _ = np.histogram(current_values, bins=current_edges)

        baseline_total = baseline_counts.sum()
        current_total = current_counts.sum()
        if baseline_total == 0 or current_total == 0:
            return None

        baseline_pct = np.where(baseline_counts == 0, PSI_EPSILON, baseline_counts / baseline_total)
        current_pct = np.where(current_counts == 0, PSI_EPSILON, current_counts / current_total)

        psi = np.sum((current_pct - baseline_pct) * np.log(current_pct / baseline_pct))
        return float(psi)

    @staticmethod
    def _ks_score(base_entry: dict, current_values: pd.Series) -> float | None:
        from scipy.stats import ks_2samp

        sample = base_entry.get("sample_values")
        if not sample:
            return None
        statistic, _p_value = ks_2samp(sample, current_values)
        return float(statistic)

    # -- categorical drift: whitespace/case variance, detected at high precision --
    #
    # Scoped narrowly on purpose: this does NOT attempt to catch "a genuinely
    # new category appeared" (that's a real distribution change and much
    # noisier to call correctly). It only fires when the current value set
    # differs from baseline's top-N *raw* but is identical to it after
    # normalizing (strip whitespace + casefold) - which can only happen from
    # whitespace/case drift, not from new data. That's what makes it safe to
    # auto-fix downstream (strip_whitespace / normalize_case) rather than
    # escalate: the rule's own precision is the safety argument.
    #
    # CARDINALITY CEILING: the baseline only stores the top-N values (default
    # N=10, app/profiling.py), not the full distinct set. Exact-set-equality
    # can only ever succeed when baseline cardinality <= N - above that, even
    # a totally clean re-ingest has more distinct values than the stored
    # top-N, so `current_values == baseline_values` never holds and the check
    # silently never fires. This is a coverage gap, not a false-positive risk:
    # reporting not_applicable above the ceiling makes that gap visible in
    # the outcome list instead of it being an unexplained property of top-N
    # truncation. Do NOT widen the match logic to compensate (e.g. subset
    # checks) - that trades away the exact-match precision that makes
    # auto-fixing this rule's findings safe in the first place.

    def _check_categorical_drift(self, contract: DataContract, baseline_profile: dict) -> list[RuleOutcome]:
        outcomes: list[RuleOutcome] = []
        baseline_columns: dict = baseline_profile.get("columns", {})
        df = contract.data

        for col, base_entry in baseline_columns.items():
            rule_id = f"{CATEGORICAL_DRIFT}:{col}"
            if base_entry.get("kind") != "categorical":
                outcomes.append(
                    RuleOutcome(rule_id, RuleStatus.NOT_APPLICABLE, column=col, detail={"reason": "baseline column is not categorical"})
                )
                continue
            if col not in df.columns:
                outcomes.append(RuleOutcome(rule_id, RuleStatus.NOT_APPLICABLE, column=col, detail={"reason": "column not present"}))
                continue
            if pd.api.types.is_numeric_dtype(df[col]):
                # dtype mismatch already reported by _check_schema
                outcomes.append(
                    RuleOutcome(rule_id, RuleStatus.NOT_APPLICABLE, column=col, detail={"reason": "column is numeric-typed in the current data"})
                )
                continue
            if base_entry.get("cardinality", 0) > self.categorical_drift_cardinality_ceiling:
                outcomes.append(
                    RuleOutcome(
                        rule_id,
                        RuleStatus.NOT_APPLICABLE,
                        column=col,
                        detail={
                            "reason": "baseline cardinality exceeds the top-N ceiling; stored top_values is a truncated sample",
                            "cardinality": base_entry.get("cardinality"),
                            "ceiling": self.categorical_drift_cardinality_ceiling,
                        },
                    )
                )
                continue

            baseline_values = set(base_entry.get("top_values", {}).keys())
            if not baseline_values:
                outcomes.append(RuleOutcome(rule_id, RuleStatus.NOT_APPLICABLE, column=col, detail={"reason": "no baseline top_values recorded"}))
                continue
            current_values = set(df[col].dropna().astype(str).unique())

            def _normalize(value: str) -> str:
                return value.strip().casefold()

            if current_values == baseline_values:
                outcomes.append(RuleOutcome(rule_id, RuleStatus.PASSED, column=col))
                continue

            if {_normalize(v) for v in current_values} == {_normalize(v) for v in baseline_values}:
                outcomes.append(
                    RuleOutcome(
                        rule_id,
                        RuleStatus.FAILED,
                        column=col,
                        detail={
                            "drift_kind": "whitespace_or_case",
                            "baseline_values": sorted(baseline_values),
                            "current_values": sorted(current_values),
                        },
                    )
                )
            else:
                # A genuinely different value set (new categories, not just
                # whitespace/case) is out of scope for this rule by design -
                # still evaluated, still passes, deliberately not flagged.
                outcomes.append(RuleOutcome(rule_id, RuleStatus.PASSED, column=col))
        outcomes += self._excluded_column_outcomes(baseline_profile, CATEGORICAL_DRIFT)
        return outcomes

    # -- encoding integrity: two independent checks, not one --
    #
    # (i) low confidence: chardet itself wasn't sure of its guess.
    # (ii) fallback overruled: chardet's guess didn't actually decode and the
    #      fallback chain (Phase 2 Part 0 FileConnector._resolve_encoding) had
    #      to use a different encoding than the one chardet detected. This is
    #      the higher-precision signal - a file that only decoded under the
    #      latin-1 last resort is a strong corruption indicator regardless of
    #      how confident chardet claimed to be.
    #
    # Both are properties of the connector fetch itself (contract.detected_
    # encoding / encoding_confidence), never touched by any allowlisted fix
    # action - their outcome is invariant across a single attempt_fix call.

    def _check_encoding(self, contract: DataContract) -> list[RuleOutcome]:
        outcomes: list[RuleOutcome] = []

        confidence_rule_id = f"{LOW_CONFIDENCE_ENCODING}:{contract.detected_encoding or 'unknown'}"
        if contract.detected_encoding is None or contract.encoding_confidence is None:
            outcomes.append(
                RuleOutcome(confidence_rule_id, RuleStatus.NOT_APPLICABLE, detail={"reason": "no detected_encoding/confidence on this contract"})
            )
        else:
            detail = {
                "detected_encoding": contract.detected_encoding,
                "confidence": contract.encoding_confidence,
                "threshold": self.encoding_confidence_threshold,
            }
            status = RuleStatus.FAILED if contract.encoding_confidence < self.encoding_confidence_threshold else RuleStatus.PASSED
            outcomes.append(RuleOutcome(confidence_rule_id, status, detail=detail))

        encoding_used = contract.connector_metadata.get("encoding_used")
        fallback_rule_id = f"{ENCODING_FALLBACK_OVERRULED}:{contract.detected_encoding or 'unknown'}->{encoding_used or 'unknown'}"
        if contract.detected_encoding is None or encoding_used is None:
            outcomes.append(
                RuleOutcome(fallback_rule_id, RuleStatus.NOT_APPLICABLE, detail={"reason": "no detected_encoding/encoding_used on this contract"})
            )
        else:
            detail = {
                "detected_encoding": contract.detected_encoding,
                "encoding_used": encoding_used,
                "confidence": contract.encoding_confidence,
            }
            # Equivalence, not string identity. The connector widens an
            # `ascii` detection to utf-8 because chardet only ever saw the
            # first sample bytes - and utf-8 decodes ASCII identically, so
            # nothing was overruled and nothing can be corrupt. Comparing
            # the names literally flagged every plain-ASCII-prefixed file as
            # a corruption suspect. A genuine substitution (latin-1 last
            # resort) still fails here, which is the case this rule is for.
            equivalent = encodings_are_equivalent(contract.detected_encoding, encoding_used)
            detail["equivalent_encoding"] = equivalent
            status = RuleStatus.PASSED if equivalent else RuleStatus.FAILED
            outcomes.append(RuleOutcome(fallback_rule_id, status, detail=detail))

        return outcomes

    # -- datetime partial parse: a column that's SOME dates and SOME not --
    #
    # app/datetime_coercion.py already made the coerce-or-leave-alone
    # decision at connector fetch time and recorded every NONZERO-parse-
    # rate column it examined in
    # connector_metadata["datetime_parse_attempts"] (a column that never
    # looked like a date at all - parse_rate == 0 - isn't recorded there;
    # it was never a candidate, nothing to report). A column that WAS
    # coerced cleared the parseability floor (PASSED - successfully
    # typed). A column that was NOT coerced despite a nonzero parse rate
    # is a genuine partial parse - most values look like dates, some don't
    # - and that is a data-quality signal, not an ambiguous type: FAILED,
    # diagnosable like any other detected failure, never silently null'd
    # out with errors="coerce".
    #
    # Baseline-independent, same shape as _check_encoding above - a
    # property of THIS contract's own connector fetch, not a comparison
    # against a prior ingest.

    def _check_datetime_partial_parse(self, contract: DataContract) -> list[RuleOutcome]:
        outcomes: list[RuleOutcome] = []
        attempts: dict = contract.connector_metadata.get("datetime_parse_attempts") or {}
        for column, attempt in attempts.items():
            rule_id = f"{DATETIME_PARTIAL_PARSE}:{column}"
            detail = {"parse_rate": attempt.get("parse_rate"), "coerced": attempt.get("coerced")}
            status = RuleStatus.PASSED if attempt.get("coerced") else RuleStatus.FAILED
            outcomes.append(RuleOutcome(rule_id, status, column=column, detail=detail))
        return outcomes
