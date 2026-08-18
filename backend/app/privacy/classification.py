"""Which COLUMNS hold personal data, and how confident that is.

Two tiers, and the split is the whole design:

  HIGH CONFIDENCE - pattern-verifiable, auto-classified. An email column is
    an email column; a Luhn-valid 16-digit column is a card column. These
    are redacted before any outbound call with no human involved, because
    waiting for confirmation would mean leaking while you wait.

  LOW CONFIDENCE - never auto-classified. Person names, postal addresses and
    free-text notes cannot be detected reliably from values, and a
    `customer_name` name heuristic will both miss (`contact`, `bill_to`,
    `recipient`) and over-fire (`product_name`, `campaign_name`, `file_name`).
    Guessing here is worse than not guessing: a false positive redacts data
    the model needs and trains the operator to stop reading the report, and a
    false negative is a leak dressed up as a green tick. So these surface as
    CANDIDATES for a human to confirm, through the same confirm-a-role flow
    the semantic roles already use, remembered per SOURCE.

The classification is per column and carries its evidence: the detected kind,
the confidence tier, the fraction of non-null values that matched, and the
reason in words. Reported, never silent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import pandas as pd

from app.column_kind import ColumnKind, column_kind

from app.privacy.config import (
    GOVERNMENT_ID_PATTERNS,
    PrivacyConfig,
    compiled_candidate_patterns,
)
from app.privacy.detectors import HIGH_CONFIDENCE_DETECTORS, government_id_matcher


class PIIKind(str, Enum):
    """The closed set. A kind not named here does not exist."""

    EMAIL = "email"
    PHONE_NUMBER = "phone_number"
    PAYMENT_CARD = "payment_card"
    IP_ADDRESS = "ip_address"
    GOVERNMENT_ID = "government_id"
    # Low confidence - candidates only, never auto-classified.
    PERSON_NAME = "person_name"
    POSTAL_ADDRESS = "postal_address"
    FREE_TEXT = "free_text"


class PIIConfidence(str, Enum):
    """CONFIRMED is never machine-produced. A reader can always tell a
    person's decision from a pattern match, exactly as with semantic roles."""

    CONFIRMED = "confirmed"   # a human said so
    HIGH = "high"             # pattern-verifiable, auto-classified
    CANDIDATE = "candidate"   # needs a human; never redacted on its own


#: The kinds that may be auto-classified. Everything else is a candidate.
AUTO_CLASSIFIABLE = frozenset(
    {PIIKind.EMAIL, PIIKind.PHONE_NUMBER, PIIKind.PAYMENT_CARD, PIIKind.IP_ADDRESS, PIIKind.GOVERNMENT_ID}
)


@dataclass(frozen=True)
class ColumnClassification:
    column: str
    kind: PIIKind
    confidence: PIIConfidence
    #: Fraction of non-null values that matched. None for a candidate raised
    #: from a column NAME, where no value evidence exists - and that absence
    #: is itself informative.
    matched_fraction: float | None
    reason: str
    #: Set for GOVERNMENT_ID: which locale's format matched.
    detail: dict = field(default_factory=dict)

    @property
    def redactable(self) -> bool:
        """Only a pattern-verified or human-confirmed column is redacted. A
        candidate is reported and left alone - redacting on a guess is how a
        privacy layer starts destroying data it was not asked to touch."""
        return self.confidence in (PIIConfidence.HIGH, PIIConfidence.CONFIRMED)

    def to_dict(self) -> dict:
        payload = {
            "column": self.column,
            "kind": self.kind.value,
            "confidence": self.confidence.value,
            "matched_fraction": self.matched_fraction,
            "reason": self.reason,
            "redactable": self.redactable,
        }
        if self.detail:
            payload["detail"] = self.detail
        return payload


@dataclass
class PrivacyClassification:
    """Every column's verdict, and the parameters behind them."""

    classifications: list[ColumnClassification] = field(default_factory=list)
    parameters: dict = field(default_factory=dict)

    def redactable_columns(self) -> dict[str, PIIKind]:
        return {c.column: c.kind for c in self.classifications if c.redactable}

    def candidates(self) -> list[ColumnClassification]:
        return [c for c in self.classifications if c.confidence is PIIConfidence.CANDIDATE]

    def to_dict(self) -> dict:
        return {
            "classifications": [c.to_dict() for c in self.classifications],
            "redactable_columns": sorted(self.redactable_columns()),
            "candidate_columns": sorted(c.column for c in self.candidates()),
            "parameters": self.parameters,
        }

    @classmethod
    def from_dict(cls, payload: dict | None) -> "PrivacyClassification":
        """Rebuild from the persisted document. A run from before this
        existed yields an empty classification, which redacts nothing and
        behaves exactly as the system did before."""
        if not payload:
            return cls()
        restored = []
        for entry in payload.get("classifications") or []:
            try:
                restored.append(
                    ColumnClassification(
                        column=entry["column"],
                        kind=PIIKind(entry["kind"]),
                        confidence=PIIConfidence(entry["confidence"]),
                        matched_fraction=entry.get("matched_fraction"),
                        reason=entry.get("reason", ""),
                        detail=entry.get("detail") or {},
                    )
                )
            except (KeyError, ValueError):
                # An unreadable entry is skipped rather than raising: a
                # schema change must not make an old run un-viewable.
                continue
        return cls(classifications=restored, parameters=payload.get("parameters") or {})


def _sampled_strings(series: pd.Series, config: PrivacyConfig) -> list[str]:
    non_null = series.dropna()
    if len(non_null) > config.sample_size:
        non_null = non_null.sample(config.sample_size, random_state=config.sample_seed)
    return [str(v) for v in non_null]


def classify_frame(
    df: pd.DataFrame,
    config: PrivacyConfig | None = None,
    confirmed: dict[str, str] | None = None,
) -> PrivacyClassification:
    """Classify every column. Deterministic: same frame, same verdicts.

    `confirmed` is {column: kind} a human confirmed for this SOURCE. A
    confirmation always outranks detection and makes the column redactable.
    """
    config = config or PrivacyConfig()
    confirmed = confirmed or {}
    candidate_patterns = compiled_candidate_patterns(config)
    government_matchers = {
        locale: (government_id_matcher(GOVERNMENT_ID_PATTERNS[locale][0]), GOVERNMENT_ID_PATTERNS[locale][1])
        for locale in config.government_id_locales
        if locale in GOVERNMENT_ID_PATTERNS
    }

    results: list[ColumnClassification] = []

    for column in df.columns:
        name = str(column)

        # A human's decision outranks everything and needs no evidence.
        if name in confirmed:
            try:
                kind = PIIKind(confirmed[name])
            except ValueError:
                kind = PIIKind.FREE_TEXT
            results.append(
                ColumnClassification(
                    column=name,
                    kind=kind,
                    confidence=PIIConfidence.CONFIRMED,
                    matched_fraction=None,
                    reason="confirmed as personal data by a person for this source",
                )
            )
            continue

        series = df[column]
        # A datetime column cannot hold an email, a card or a phone number.
        # Skipping it outright is cheaper than relying on every text detector
        # to reject date shapes individually - and the ads export that
        # exposed this had its date column typed by the connector.
        if column_kind(series) is ColumnKind.DATETIME:
            continue

        values = _sampled_strings(series, config)
        if len(values) < config.min_non_null:
            # Too little evidence either way. Not classified, not a
            # candidate - saying nothing is the honest output.
            continue

        best = _best_value_match(name, values, config, government_matchers)
        if best is not None:
            results.append(best)
            continue

        # No value evidence. The NAME may still make it worth a human's
        # attention - as a candidate, never as a classification.
        candidate = _name_candidate(name, candidate_patterns)
        if candidate is not None:
            results.append(candidate)

    return PrivacyClassification(classifications=results, parameters=config.as_reported())


def _best_value_match(column: str, values: list[str], config: PrivacyConfig, government_matchers: dict):
    """The highest-matching high-confidence kind, or None.

    Payment card is tested before phone: a Luhn-valid 16-digit run written
    with spaces satisfies the phone shape too, and a card is the more
    dangerous misclassification to miss.
    """
    ordered: list[tuple[PIIKind, callable, dict]] = [
        (PIIKind.PAYMENT_CARD, HIGH_CONFIDENCE_DETECTORS["payment_card"], {}),
        (PIIKind.EMAIL, HIGH_CONFIDENCE_DETECTORS["email"], {}),
        (PIIKind.IP_ADDRESS, HIGH_CONFIDENCE_DETECTORS["ip_address"], {}),
    ]
    for locale, (matcher, label) in government_matchers.items():
        ordered.append((PIIKind.GOVERNMENT_ID, matcher, {"locale": locale, "format": label}))
    ordered.append((PIIKind.PHONE_NUMBER, HIGH_CONFIDENCE_DETECTORS["phone_number"], {}))

    for kind, detector, detail in ordered:
        matched = sum(1 for value in values if detector(value))
        fraction = matched / len(values)
        if fraction >= config.match_threshold:
            described = detail.get("format", kind.value.replace("_", " "))
            return ColumnClassification(
                column=column,
                kind=kind,
                confidence=PIIConfidence.HIGH,
                matched_fraction=round(fraction, 4),
                reason=(
                    f"{fraction:.0%} of {len(values)} sampled non-null values match {described}"
                    + (" and pass the Luhn checksum" if kind is PIIKind.PAYMENT_CARD else "")
                ),
                detail=detail,
            )
    return None


def _name_candidate(name: str, patterns: dict) -> ColumnClassification | None:
    for kind_name, pattern in patterns.items():
        if pattern.search(name):
            kind = PIIKind(kind_name)
            return ColumnClassification(
                column=name,
                kind=kind,
                confidence=PIIConfidence.CANDIDATE,
                matched_fraction=None,
                reason=(
                    f"the column NAME suggests {kind.value.replace('_', ' ')}, but no value pattern can "
                    "verify it. Nothing is redacted on this basis - confirm it if the column really "
                    "holds personal data."
                ),
            )
    return None
