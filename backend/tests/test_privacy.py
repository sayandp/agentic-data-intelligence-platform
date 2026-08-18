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

from app.models import Run
from tests.golden_scenarios import ingest_and_wait
from app.privacy.classification import PIIConfidence, PIIKind, classify_frame
from app.privacy.config import PrivacyConfig
from app.privacy.detectors import is_email, is_ip_address, is_payment_card, is_phone_number, luhn_valid
from app.privacy.redaction import RedactedSample, RedactionPolicy, redact_records

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
    assert found[column].is_redactable(RedactionPolicy.STRICT)


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
    assert not found[column].verified_personal, "a name heuristic must never count as verified"
    # DEFAULT-DENY: masked on the strict paths, let through where masking was
    # measured to cost real narrative content.
    assert found[column].is_redactable(RedactionPolicy.STRICT)
    assert not found[column].is_redactable(RedactionPolicy.PERMISSIVE)


def test_a_candidate_column_is_masked_under_STRICT_and_clear_under_PERMISSIVE():
    """The per-path split, on the actual output."""
    frame = pii_frame()
    classification = classify_frame(frame)
    records = frame.head(3).to_dict(orient="records")

    strict = redact_records(records, classification, RedactionPolicy.STRICT)
    permissive = redact_records(records, classification, RedactionPolicy.PERMISSIVE)

    assert strict.rows[0]["customer_name"].startswith("<NAME_")
    assert permissive.rows[0]["customer_name"] == frame.iloc[0]["customer_name"]
    # Verified PII is masked under BOTH - permissive is not "off".
    assert permissive.rows[0]["email"].startswith("<EMAIL_")


def test_a_confirmed_column_becomes_redactable():
    """The other half of the bargain: a human can say so, and then it is."""
    frame = pii_frame()
    confirmed = classify_frame(frame, confirmed={"customer_name": "person_name"})
    entry = next(c for c in confirmed.classifications if c.column == "customer_name")

    assert entry.confidence is PIIConfidence.CONFIRMED
    assert entry.is_redactable(RedactionPolicy.STRICT)
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


# ---- the cache key must change when the prompt does ----


def test_the_diagnosis_cache_key_changes_with_the_prompt_version():
    """Otherwise a pre-redaction answer is replayed for a redacted prompt -
    which is exactly what happened, and made a measurement compare a cached
    answer against itself."""
    from app.diagnosis.cache import cache_key_for_group
    from app.validation.engine import ValidationFailure

    members = [ValidationFailure(rule_failed="schema_conformance:missing_column:email", column="email", detail={})]
    v1 = cache_key_for_group(members, provider="P", model="m", prompt_version=1)
    v2 = cache_key_for_group(members, provider="P", model="m", prompt_version=2)

    assert v1 != v2, "the same group produced the same key across prompt versions"


def test_the_query_cache_key_changes_with_the_prompt_version():
    from app.query.cache import cache_key_for_query

    v1 = cache_key_for_query("P", "m", {"a": "int64"}, "how many?", prompt_version=1)
    v2 = cache_key_for_query("P", "m", {"a": "int64"}, "how many?", prompt_version=2)

    assert v1 != v2


# ---- the per-path policy split ----


def test_the_paths_use_the_policies_the_measurements_justified():
    """Strict where over-redacting was measured free (diagnosis, query,
    modeling); permissive only where it was measured to cost findings."""
    from app.privacy.redaction import POLICY_BY_PATH

    assert POLICY_BY_PATH["diagnosis"] is RedactionPolicy.STRICT
    assert POLICY_BY_PATH["query"] is RedactionPolicy.STRICT
    assert POLICY_BY_PATH["modeling"] is RedactionPolicy.STRICT
    assert POLICY_BY_PATH["narrative"] is RedactionPolicy.PERMISSIVE


def test_verified_pii_is_masked_on_every_path_including_the_permissive_one():
    """Permissive relaxes the treatment of GUESSES, never of verified PII."""
    frame = pii_frame()
    classification = classify_frame(frame)

    for policy in (RedactionPolicy.STRICT, RedactionPolicy.PERMISSIVE):
        masked = classification.redactable_columns(policy)
        for column in ("email", "phone", "card_number", "client_ip", "ssn"):
            assert column in masked, f"{column} was not masked under {policy.value}"


def test_a_column_marked_not_personal_is_never_redacted():
    """The mirror of confirm-a-role: a human can switch default-deny off for
    one column, and that decision holds on every path."""
    from app.privacy.classification import ColumnClassification, PrivacyClassification

    classification = PrivacyClassification(
        classifications=[
            ColumnClassification(
                column="Description",
                kind=PIIKind.FREE_TEXT,
                confidence=PIIConfidence.NOT_PERSONAL,
                matched_fraction=None,
                reason="marked not personal",
                confirmed_by="local operator",
            )
        ]
    )

    for policy in (RedactionPolicy.STRICT, RedactionPolicy.PERMISSIVE):
        assert classification.redactable_columns(policy) == {}


def test_the_classification_reports_both_policies_and_the_split():
    """The Privacy section must be able to state which columns are masked on
    which path without inferring it."""
    payload = classify_frame(pii_frame()).to_dict()

    assert "customer_name" in payload["redacted_strict"]
    assert "customer_name" not in payload["redacted_permissive"]
    assert payload["policy_by_path"]["narrative"] == "permissive"
    assert payload["policy_by_path"]["diagnosis"] == "strict"


def test_who_marked_a_column_is_recorded():
    from app.privacy.classification import ColumnClassification

    entry = ColumnClassification(
        column="notes",
        kind=PIIKind.FREE_TEXT,
        confidence=PIIConfidence.NOT_PERSONAL,
        matched_fraction=None,
        reason="marked not personal",
        confirmed_by="local operator",
        confirmed_at="2026-08-18T10:00:00Z",
    )

    assert entry.to_dict()["confirmed_by"] == "local operator"
    assert entry.to_dict()["confirmed_at"] == "2026-08-18T10:00:00Z"


# ---- the mark-not-personal flow, over HTTP ----


def _completed_run_with_candidates(client, tmp_path):
    """A real run over a frame with one verified PII column and two
    candidates, through the ordinary ingest path."""
    csv = tmp_path / "people.csv"
    rows = "\n".join(
        f"Person {i},p{i}@example.com,some free text about order {i},{i * 10}" for i in range(40)
    )
    csv.write_text(f"customer_name,email,notes,amount\n{rows}\n")

    source_id = client.post(
        "/sources", json={"type": "file", "connection_config": {"path": str(csv)}}
    ).json()["id"]
    return ingest_and_wait(client, source_id)["run_id"]


def test_a_person_can_clear_a_candidate_and_it_stops_being_redacted(client, tmp_path):
    run_id = _completed_run_with_candidates(client, tmp_path)

    before = client.get(f"/privacy/{run_id}").json()
    assert "notes" in before["redacted_strict"], "default-deny should mask an unconfirmed candidate"

    after = client.post(
        f"/privacy/{run_id}/decisions",
        json={"column": "notes", "decision": "not_personal", "marked_by": "sayan"},
    )
    assert after.status_code == 200, after.text
    payload = after.json()

    assert "notes" not in payload["redacted_strict"]
    assert "notes" in payload["not_personal_columns"]
    # Clearing one column must not clear the others.
    assert "customer_name" in payload["redacted_strict"]
    assert "email" in payload["redacted_permissive"]


def test_the_privacy_record_says_who_marked_a_column(client, tmp_path):
    run_id = _completed_run_with_candidates(client, tmp_path)
    payload = client.post(
        f"/privacy/{run_id}/decisions",
        json={"column": "notes", "decision": "not_personal", "marked_by": "sayan"},
    ).json()

    entry = next(c for c in payload["classifications"] if c["column"] == "notes")
    assert entry["confirmed_by"] == "sayan"
    assert entry["confirmed_at"]


def test_verified_pii_cannot_be_marked_not_personal(client, tmp_path):
    """The one refusal in this flow. A Luhn-valid card column or a column of
    real email addresses is not something confirm-a-role was built to
    resolve, and honouring the request would remove protection the machine
    can prove is needed."""
    run_id = _completed_run_with_candidates(client, tmp_path)

    response = client.post(
        f"/privacy/{run_id}/decisions",
        json={"column": "email", "decision": "not_personal", "marked_by": "sayan"},
    )

    assert response.status_code == 400
    assert "cannot be marked not personal" in response.json()["detail"]
    # And it really was not stored.
    assert "email" in client.get(f"/privacy/{run_id}").json()["redacted_permissive"]


def test_a_decision_can_be_withdrawn(client, tmp_path):
    """Marking a column safe removes protection, so it must be reversible."""
    run_id = _completed_run_with_candidates(client, tmp_path)
    client.post(
        f"/privacy/{run_id}/decisions",
        json={"column": "notes", "decision": "not_personal", "marked_by": "sayan"},
    )

    payload = client.delete(f"/privacy/{run_id}/decisions/notes").json()

    assert "notes" in payload["redacted_strict"], "withdrawing returns the column to default-deny"
    assert "notes" not in payload["not_personal_columns"]


def test_a_decision_survives_re_ingest_of_the_same_source(client, tmp_path):
    """Source-scoped, like every other confirmation: the question is asked
    once, not on every ingest of the same file."""
    csv = tmp_path / "people.csv"
    rows = "\n".join(f"Person {i},p{i}@example.com,free text {i},{i * 10}" for i in range(40))
    csv.write_text(f"customer_name,email,notes,amount\n{rows}\n")
    source_id = client.post(
        "/sources", json={"type": "file", "connection_config": {"path": str(csv)}}
    ).json()["id"]

    first = ingest_and_wait(client, source_id)["run_id"]
    client.post(
        f"/privacy/{first}/decisions",
        json={"column": "notes", "decision": "not_personal", "marked_by": "sayan"},
    )

    second = ingest_and_wait(client, source_id)["run_id"]
    payload = client.get(f"/privacy/{second}").json()

    assert "notes" not in payload["redacted_strict"], "the second run should inherit the decision"
    assert "notes" in payload["not_personal_columns"]


def test_a_stale_not_personal_cannot_unmask_a_column_that_now_holds_pii():
    """A source can change shape. A column truthfully marked safe last month
    may hold email addresses today, and detection must win over the stale
    decision rather than the decision silently disabling the detector."""
    from app.privacy.confirmations import NOT_PERSONAL, PrivacyDecision

    frame = pd.DataFrame({"notes": [f"person{i}@example.com" for i in range(20)]})
    decisions = {"notes": PrivacyDecision("notes", NOT_PERSONAL, "sayan")}

    classification = classify_frame(frame, confirmed=decisions)
    entry = classification.classifications[0]

    assert entry.confidence is PIIConfidence.HIGH
    assert entry.kind is PIIKind.EMAIL
    assert "notes" in classification.redactable_columns(RedactionPolicy.PERMISSIVE)
    # And it says so, rather than dropping the decision on the floor.
    assert "marked this column not personal" in entry.reason


def test_a_privacy_decision_is_not_mistaken_for_a_semantic_role(client, tmp_path):
    """Both live in confirmed_column_roles. A `pii:` row reaching the role
    detector would be a column role nobody confirmed."""
    from app.analytics.pipeline import confirmed_roles_for_source
    from app.db import SessionLocal

    run_id = _completed_run_with_candidates(client, tmp_path)
    client.post(
        f"/privacy/{run_id}/decisions",
        json={"column": "notes", "decision": "not_personal", "marked_by": "sayan"},
    )

    db = SessionLocal()
    try:
        run = db.query(Run).filter(Run.id == run_id).one()
        assert confirmed_roles_for_source(db, run.source_id) == {}
    finally:
        db.close()
