"""PII detection and egress redaction.

The tests that matter most are at the bottom: no unredacted personal value
may reach ANY LLM call site, asserted on the ACTUAL outbound payload for
every path, and the stored frame must be byte-identical after detection.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from app.privacy.classification import PIIConfidence, PIIKind, classify_frame
from app.privacy.config import PrivacyConfig
from app.privacy.detectors import is_email, is_ip_address, is_payment_card, is_phone_number, luhn_valid
from app.privacy.redaction import RedactedSample, redact_records

# Luhn-valid test numbers published by the card networks for exactly this.
VALID_CARDS = ["4539578763621486", "6011000990139424", "5500005555555559", "4111111111111111"]


def pii_frame(rows: int = 60) -> pd.DataFrame:
    rng = np.random.default_rng(4)
    return pd.DataFrame(
        {
            "order_id": [f"ORD-{100000 + i}" for i in range(rows)],
            "customer_name": [f"Person {i}" for i in range(rows)],
            "email": [f"user{i}@example.com" for i in range(rows)],
            "phone": [f"+44 20 7{100000 + i}" for i in range(rows)],
            "card_number": rng.choice(VALID_CARDS, rows),
            "client_ip": [f"10.{i % 255}.{(i * 3) % 255}.{(i * 7) % 254 + 1}" for i in range(rows)],
            "ssn": [f"{100 + i % 800}-{10 + i % 89}-{1000 + i}" for i in range(rows)],
            "notes": ["called back"] * rows,
            "amount": np.round(rng.uniform(5, 500, rows), 2),
        }
    )


# ---- Luhn: the check that separates a card from an order id ----


def test_luhn_accepts_real_card_numbers():
    for card in VALID_CARDS:
        assert luhn_valid(card), f"{card} should pass Luhn"


@pytest.mark.parametrize("identifier", ["1234567890123456", "9999999999999999", "1111111111111112"])
def test_luhn_rejects_a_sixteen_digit_non_card_identifier(identifier):
    """The whole point of the checksum. A system that redacts order ids
    teaches its operator to switch the privacy layer off."""
    assert not luhn_valid(identifier)
    assert not is_payment_card(identifier)


def test_a_sixteen_digit_order_id_column_is_not_classified_as_a_card():
    df = pd.DataFrame({"order_ref": [str(1234567890123450 + i) for i in range(60)]})
    kinds = {c.column: c.kind for c in classify_frame(df).classifications}

    assert kinds.get("order_ref") != PIIKind.PAYMENT_CARD


# ---- each high-confidence type ----


@pytest.mark.parametrize(
    ("column", "kind"),
    [
        ("email", PIIKind.EMAIL),
        ("phone", PIIKind.PHONE_NUMBER),
        ("card_number", PIIKind.PAYMENT_CARD),
        ("client_ip", PIIKind.IP_ADDRESS),
        ("ssn", PIIKind.GOVERNMENT_ID),
    ],
)
def test_each_high_confidence_type_is_detected(column, kind):
    found = {c.column: c for c in classify_frame(pii_frame()).classifications}

    assert column in found, f"{column} was not classified at all"
    assert found[column].kind is kind
    assert found[column].confidence is PIIConfidence.HIGH
    assert found[column].redactable


def test_a_classification_carries_its_evidence():
    """Reported, never silent: the fraction that matched and why."""
    email = next(c for c in classify_frame(pii_frame()).classifications if c.column == "email")

    assert email.matched_fraction == 1.0
    assert "match" in email.reason and "email" in email.reason


@pytest.mark.parametrize("value", ["a@b.co", "first.last@sub.example.com"])
def test_email_detector_accepts_real_addresses(value):
    assert is_email(value)


@pytest.mark.parametrize("value", ["not an email", "@example.com", "user@", "user@example"])
def test_email_detector_rejects_non_addresses(value):
    assert not is_email(value)


def test_a_bare_long_integer_is_not_a_phone_number():
    """A phone number and an id are indistinguishable by digits alone, so a
    separator or a country prefix is required."""
    assert not is_phone_number("12345678901")
    assert is_phone_number("+44 20 7123 4567")


@pytest.mark.parametrize("value", ["999.999.999.999", "1.2.3", "hello"])
def test_ip_detector_range_checks(value):
    assert not is_ip_address(value)


# ---- low confidence is NEVER auto-classified ----


@pytest.mark.parametrize("column", ["customer_name", "notes"])
def test_low_confidence_types_are_candidates_never_classified(column):
    """A name heuristic both misses and over-fires, so it may raise a
    question but never make a decision."""
    found = {c.column: c for c in classify_frame(pii_frame()).classifications}

    assert found[column].confidence is PIIConfidence.CANDIDATE
    assert not found[column].redactable


def test_a_candidate_column_is_not_redacted():
    frame = pii_frame()
    classification = classify_frame(frame)
    redacted = redact_records(frame.head(3).to_dict(orient="records"), classification)

    # The name is still there - reported as a candidate, not masked on a guess.
    assert redacted.rows[0]["customer_name"] == frame.iloc[0]["customer_name"]
    assert "customer_name" not in redacted.redacted_columns


def test_a_confirmed_column_becomes_redactable():
    """The other half of the bargain: a human can say so, and then it is."""
    frame = pii_frame()
    confirmed = classify_frame(frame, confirmed={"customer_name": "person_name"})
    entry = next(c for c in confirmed.classifications if c.column == "customer_name")

    assert entry.confidence is PIIConfidence.CONFIRMED
    assert entry.redactable
    redacted = redact_records(frame.head(2).to_dict(orient="records"), confirmed)
    assert redacted.rows[0]["customer_name"].startswith("<NAME_")


# ---- the stored frame is never mutated ----


def test_the_stored_frame_is_identical_after_detection_and_redaction():
    frame = pii_frame()
    before = frame.copy(deep=True)

    classification = classify_frame(frame)
    redact_records(frame.head(20).to_dict(orient="records"), classification)

    pd.testing.assert_frame_equal(frame, before)


# ---- tokenisation ----


def test_the_same_value_gets_the_same_token_within_a_column():
    """So the model can still see that two rows share a value - which is what
    it needs to spot a duplicate or a join key - without seeing the value."""
    frame = pd.DataFrame({"email": ["a@x.com", "b@x.com", "a@x.com", "c@x.com"] * 15})
    redacted = redact_records(frame.to_dict(orient="records"), classify_frame(frame))
    tokens = [row["email"] for row in redacted.rows[:4]]

    assert tokens[0] == tokens[2], "the same address got two different tokens"
    assert len({tokens[0], tokens[1], tokens[3]}) == 3, "different addresses collapsed to one token"


def test_column_names_are_never_redacted():
    """The model needs the schema to do its job, and a column name is not
    personal data."""
    frame = pii_frame()
    redacted = redact_records(frame.head(2).to_dict(orient="records"), classify_frame(frame))

    assert set(redacted.rows[0]) == set(frame.columns)


def test_nulls_stay_null():
    """Masking an absent value would tell the model a value exists where none
    does - and null-ness is frequently the thing being diagnosed."""
    frame = pd.DataFrame({"email": ([f"u{i}@x.com" for i in range(30)] + [None] * 10)})
    redacted = redact_records(frame.to_dict(orient="records"), classify_frame(frame))

    # pandas stores the missing value as NaN in an object column, so the
    # assertion is "still missing", not "is None" - what matters is that it
    # did not acquire a token.
    missing = redacted.rows[-1]["email"]
    assert missing is None or pd.isna(missing)
    assert not str(missing).startswith("<"), "a null was given a token, implying a value exists"


def test_a_run_with_no_classification_passes_rows_through_unchanged():
    """An older run has no classification. It must keep working."""
    records = [{"email": "a@x.com"}]
    assert redact_records(records, None).rows == records


# ---- the outbound payloads: no unredacted value may reach a call site ----


class _CapturingClient:
    """Records the prompt instead of sending it."""

    model_name = "test-model"

    def __init__(self):
        self.prompts: list[str] = []

    def complete(self, system, user, response_schema):
        self.prompts.append(user)
        return response_schema.model_construct()


def _secrets(frame: pd.DataFrame) -> list[str]:
    """The actual personal values in the fixture - what must not appear."""
    values = []
    for column in ("email", "phone", "card_number", "client_ip", "ssn"):
        values.extend(str(v) for v in frame[column].head(20).unique())
    return values


def test_no_unredacted_value_reaches_the_QUERY_call_site():
    from app.query.agent import QueryAgent
    from app.query.models import QueryKind

    frame = pii_frame()
    sample = redact_records(frame.head(20).to_dict(orient="records"), classify_frame(frame))
    client = _CapturingClient()
    agent = QueryAgent(llm_client=client, sleep=lambda _s: None)
    try:
        agent.generate(QueryKind.PANDAS, "how many orders?", {"amount": "float"}, sample, [])
    except Exception:  # noqa: BLE001 - the prompt is what is under test
        pass

    assert client.prompts, "no prompt was captured"
    for secret in _secrets(frame):
        assert secret not in client.prompts[0], f"{secret!r} reached the query prompt unredacted"


def test_no_unredacted_value_reaches_the_MODELING_call_site():
    from app.modeling.agent import ModelingAgent

    frame = pii_frame()
    sample = redact_records(frame.head(20).to_dict(orient="records"), classify_frame(frame))
    client = _CapturingClient()
    agent = ModelingAgent(llm_client=client, sleep=lambda _s: None)
    try:
        agent.classify_intent("forecast amount", {"amount": "float"}, sample)
    except Exception:  # noqa: BLE001
        pass

    assert client.prompts, "no prompt was captured"
    for secret in _secrets(frame):
        assert secret not in client.prompts[0], f"{secret!r} reached the modeling prompt unredacted"


def test_no_unredacted_value_reaches_the_DIAGNOSIS_call_site():
    from app.contract import DataContract, SourceType
    from app.correlation import correlate_events
    from app.diagnosis.agent import DiagnosticAgent
    from app.profiling import BaselineProfiler
    from app.validation.engine import ValidationEngine
    from tests.fakes import InMemoryDiagnosisCache

    clean = pii_frame()
    corrupted = clean.rename(columns={"email": "email_address"})
    baseline = BaselineProfiler().profile(clean)
    contract = DataContract(data=corrupted, source_type=SourceType.FILE, source_id="privacy-test")
    failures = ValidationEngine().validate(contract, baseline)
    groups = correlate_events(failures, baseline, contract)
    assert groups, "the fixture produced no validation failure to diagnose"

    client = _CapturingClient()
    agent = DiagnosticAgent(llm_client=client, cache=InMemoryDiagnosisCache(), sleep=lambda _s: None)
    try:
        agent.diagnose_group(groups[0], baseline, contract, privacy=classify_frame(corrupted))
    except Exception:  # noqa: BLE001
        pass

    assert client.prompts, "no prompt was captured"
    for secret in _secrets(clean):
        assert secret not in client.prompts[0], f"{secret!r} reached the diagnosis prompt unredacted"


def test_no_unredacted_value_reaches_the_NARRATIVE_call_site():
    """The narrative path sends findings, not rows - but those findings carry
    a categorical column's mode and top frequencies, which are real values."""
    from app.exploration.engine import ExplorationEngine
    from app.exploration.findings import DataQualityContext
    from app.narrative.agent import NarrativeAgent

    frame = pii_frame()
    findings = ExplorationEngine().run(frame, run_id="r", data_quality_context=DataQualityContext(total_events=0))

    client = _CapturingClient()
    agent = NarrativeAgent(llm_client=client, sleep=lambda _s: None)
    try:
        agent.generate_claims(findings, privacy=classify_frame(frame))
    except Exception:  # noqa: BLE001
        pass

    assert client.prompts, "no prompt was captured"
    for secret in _secrets(frame):
        assert secret not in client.prompts[0], f"{secret!r} reached the narrative prompt unredacted"


def test_the_narrative_prompt_WOULD_have_leaked_without_redaction():
    """Proves the previous test is not passing vacuously - the values really
    are in that payload when redaction is off."""
    from app.exploration.engine import ExplorationEngine
    from app.exploration.findings import DataQualityContext
    from app.narrative.agent import NarrativeAgent

    frame = pii_frame()
    findings = ExplorationEngine().run(frame, run_id="r", data_quality_context=DataQualityContext(total_events=0))

    client = _CapturingClient()
    agent = NarrativeAgent(llm_client=client, sleep=lambda _s: None)
    try:
        agent.generate_claims(findings, privacy=None)
    except Exception:  # noqa: BLE001
        pass

    leaked = [s for s in _secrets(frame) if s in client.prompts[0]]
    assert leaked, "the narrative payload carried no personal values even unredacted - the guard test proves nothing"


def test_the_redacted_sample_reports_what_it_masked_without_the_values():
    frame = pii_frame()
    redacted = redact_records(frame.head(10).to_dict(orient="records"), classify_frame(frame))
    reported = json.dumps({"columns": redacted.redacted_columns, "counts": redacted.masked_value_counts})

    assert "email" in reported
    for secret in _secrets(frame):
        assert secret not in reported, "the redaction report leaked a value it was meant to protect"


def test_an_empty_sample_is_still_a_redacted_sample():
    """The type is the guarantee: a call site cannot receive a bare list."""
    assert isinstance(redact_records([], classify_frame(pii_frame())), RedactedSample)


# ---- configurability ----


def test_the_match_threshold_is_configurable():
    """Half a column of emails is not an email column at the default, and is
    at a lower threshold."""
    frame = pd.DataFrame({"mixed": [f"u{i}@x.com" if i % 2 else f"plain-{i}" for i in range(40)]})

    assert not classify_frame(frame, PrivacyConfig(match_threshold=0.8)).redactable_columns()
    assert classify_frame(frame, PrivacyConfig(match_threshold=0.4)).redactable_columns()


def test_government_id_locales_are_configurable():
    frame = pd.DataFrame({"nino": [f"AB{10 + i}{20 + i}{30 + i}C" for i in range(30)]})

    assert classify_frame(frame, PrivacyConfig(government_id_locales=("uk_nino",))).redactable_columns()
    assert not classify_frame(frame, PrivacyConfig(government_id_locales=())).redactable_columns()


def test_a_column_with_too_few_values_is_not_classified_either_way():
    """Three values that happen to look like phone numbers are not evidence."""
    frame = pd.DataFrame({"email": ["a@x.com", "b@x.com"]})

    assert classify_frame(frame).classifications == []


# ---- regression: a date column is not a phone number ----


@pytest.mark.parametrize("value", ["2026-01-01", "2026/01/01", "01-01-2026", "1/1/26", "2026-01-01 14:30:00"])
def test_a_date_is_never_a_phone_number(value):
    """Found on a real ads export: `Reporting starts` was classified as a
    HIGH-confidence phone column and would have been redacted, which would
    have destroyed every trend prompt in the system. A date has the same
    shape as a phone number - digits with separators - so it is rejected
    explicitly."""
    assert not is_phone_number(value)


def test_a_date_column_is_not_classified_as_personal():
    frame = pd.DataFrame({"Reporting starts": pd.date_range("2026-01-01", periods=40).strftime("%Y-%m-%d")})

    assert classify_frame(frame).redactable_columns() == {}


def test_a_typed_datetime_column_is_skipped_entirely():
    frame = pd.DataFrame({"ordered_at": pd.date_range("2026-01-01", periods=40)})

    assert classify_frame(frame).classifications == []
