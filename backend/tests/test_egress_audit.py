"""The egress audit trail: what left, recorded without recording what left.

The central test in this file is `test_no_record_anywhere_contains_a_data_value`.
Everything else describes the trail; that one is the reason it is safe to
have. An audit log that proves protection by storing the data it protected is
a second unclassified copy of exactly the wrong thing.
"""

import json

import pandas as pd

from app.models import EgressEvent, ValidationEvent
from app.privacy.classification import classify_frame
from app.privacy.egress_log import EgressRecord, record_for_findings, record_for_sample
from app.privacy.redaction import POLICY_BY_PATH, RedactionPolicy, redact_records
from app.modeling.models import IntentClassification
from app.narrative.models import ClaimValue, GroundedClaim, GroundedClaimsResponse, NarrativeProse
from app.query.models import GeneratedQuery
from tests.fakes import modeling_llm_override, narrative_llm_override, query_llm_override
from tests.corruption.suite import CorruptionSuite
from tests.golden_scenarios import (
    _clean_df,
    _resolve_id_for_run,
    _write_csv,
    ingest_and_wait,
    resolve_and_wait,
)

#: Values that must never appear in any record, in any encoding.
SECRETS = [
    "p3@example.com",
    "Person 3",
    "handwritten note about order 7",
]


def pii_csv(rows: int = 60) -> str:
    body = "\n".join(
        f"Person {i},p{i}@example.com,handwritten note about order {i},{(i + 1) * 10}" for i in range(rows)
    )
    return f"customer_name,email,notes,amount\n{body}\n"


def _run_with_pii(client, tmp_path) -> str:
    csv = tmp_path / "people.csv"
    csv.write_text(pii_csv())
    source_id = client.post(
        "/sources", json={"type": "file", "connection_config": {"path": str(csv)}}
    ).json()["id"]
    return ingest_and_wait(client, source_id)["run_id"]


def _egress_rows(db, run_id):
    return db.query(EgressEvent).filter(EgressEvent.run_id == run_id).all()


# ---- the guarantee ----


def _assert_no_data_in(blob: str, where: str) -> None:
    """The single standard, applied wherever a record is serialised.

    Two things are forbidden. RAW values, obviously. And masked TOKENS:
    a token is derived from a value and belongs in a prompt, not in a
    shape-only record - a log full of <EMAIL_1>..<EMAIL_2000> discloses
    the cardinality of a column it was never meant to describe."""
    for secret in SECRETS:
        assert secret not in blob, f'{where} leaked a raw data value'
    for token in ('<EMAIL_', '<NAME_', '<TEXT_', '<CARD_', '<PHONE_', '<IP_', '<GOV_ID_', '<ADDRESS_'):
        assert token not in blob, f'{where} leaked a redaction token'




def _pii_frame(rows: int = 60) -> pd.DataFrame:
    """A frame the redactor has real work to do on: one verified PII
    column, two candidates, one plain numeric. The corruption suite runs
    over this same frame, so the diagnosis path sees it too."""
    return pd.DataFrame(
        {
            'customer_name': [f'Person {i}' for i in range(rows)],
            'email': [f'p{i}@example.com' for i in range(rows)],
            'notes': [f'handwritten note about order {i}' for i in range(rows)],
            'amount': [float((i + 1) * 10) for i in range(rows)],
        }
    )


def _run_exercising_every_path(client, tmp_path, db_session) -> str:
    """One run that actually calls out on ALL FOUR paths.

    This exists because the leak scan is only worth as much as its
    coverage. Scanning a run that happened to make one narrative call
    would pass while three other paths wrote whatever they liked - which
    is exactly what an earlier version of this file did, and it survived a
    deliberate leak planted in the sample-path record.

    Every path is asserted present before the scan runs."""
    clean = _pii_frame()
    csv = tmp_path / 'egress_all_paths.csv'
    _write_csv(csv, clean)
    source_id = client.post(
        '/sources', json={'type': 'file', 'connection_config': {'path': str(csv)}}
    ).json()['id']
    ingest_and_wait(client, source_id)  # establishes the baseline

    # A real corruption, so validation fails and the diagnosis path runs.
    corrupted, _truth = CorruptionSuite().apply(clean, 'inject_nulls', seed=100, column='amount')
    _write_csv(csv, corrupted)
    # Nulls escalate, so the run pauses for a human before it ever reaches
    # the narrative stage. Resolving inside the override lets the resumed
    # graph make its narrative call too - which is the only way one run
    # exercises both the diagnosis and the narrative path.
    with narrative_llm_override(responses=_claims_pair()):
        result = ingest_and_wait(client, source_id)
        run_id = result['run_id']
        if result['status'] == 'awaiting_approval':
            resolve_and_wait(client, _resolve_id_for_run(client, run_id), 'reject_fix', 'tester')

    generated = GeneratedQuery(
        query_kind='pandas',
        code='result = df.shape[0]',
        columns_referenced=[],
        assumptions=[],
        confidence=0.9,
    )
    with query_llm_override(responses=[generated]):
        client.post('/ask', json={'run_id': run_id, 'question': 'how many rows are there?'})

    intent = IntentClassification(
        intent='prediction', target_column='amount', confidence=0.9, reasoning='asked for a prediction'
    )
    with modeling_llm_override(responses=[intent]):
        client.post('/predict', json={'run_id': run_id, 'question': 'predict amount'})

    agents = {row.agent for row in _egress_rows(db_session, run_id)}
    missing = {'diagnosis', 'narrative', 'query', 'modeling'} - agents
    assert not missing, (
        f'these paths never called out, so scanning this run would prove nothing about them: {sorted(missing)}'
    )
    return run_id




def test_no_record_anywhere_contains_a_data_value(client, tmp_path, db_session):
    """The whole point. Every stored egress row is serialised and searched for
    the actual values of the run's data. A record may carry column NAMES (the
    model receives the schema anyway) and counts, and nothing else."""
    run_id = _run_exercising_every_path(client, tmp_path, db_session)

    rows = _egress_rows(db_session, run_id)
    assert rows, "the run made model calls, so it must have recorded them"

    for row in rows:
        blob = json.dumps(
            {
                "agent": row.agent,
                "provider": row.provider,
                "model": row.model,
                "policy": row.policy,
                "columns": row.columns_json,
                "row_count": row.row_count,
                "unit": row.unit,
                "redacted_columns": row.redacted_columns_json,
                "masked_value_counts": row.masked_value_counts_json,
            }
        )
        _assert_no_data_in(blob, f'the {row.agent} egress record')


def test_the_record_has_no_field_that_could_hold_a_payload():
    """Structural, not a search. With no field to put rows in, no later edit
    can quietly start putting rows in one."""
    fields = set(EgressRecord.__dataclass_fields__)

    assert fields == {
        "agent",
        "provider",
        "model",
        "policy",
        "columns",
        "row_count",
        "unit",
        "redacted_columns",
        "masked_value_counts",
    }
    for forbidden in ("rows", "payload", "sample", "prompt", "data", "values"):
        assert forbidden not in fields


# ---- the record describes what actually happened ----


def test_the_record_is_built_from_the_redactor_not_from_the_callers_intent():
    """`redacted_columns` is copied off the RedactedSample, so a record cannot
    claim a column was masked that the redactor left alone."""
    frame = pd.DataFrame(
        {"email": [f"p{i}@example.com" for i in range(20)], "amount": list(range(20))}
    )
    classification = classify_frame(frame)
    sample = redact_records(frame.to_dict(orient="records"), classification, RedactionPolicy.STRICT)

    record = record_for_sample(
        sample,
        agent="diagnosis",
        provider="FakeLLMClient",
        model="fake-1",
        policy=RedactionPolicy.STRICT,
        columns=["email", "amount"],
    )

    assert record.redacted_columns == {"email": "email"}
    assert "amount" not in record.redacted_columns
    assert record.masked_value_counts == {"email": 20}
    assert record.row_count == 20
    assert record.was_redacted


def test_a_call_that_masked_nothing_is_still_recorded():
    """Sending unclassified data is still a disclosure."""
    frame = pd.DataFrame({"amount": list(range(20)), "region": ["north"] * 20})
    sample = redact_records(frame.to_dict(orient="records"), classify_frame(frame))

    record = record_for_sample(
        sample,
        agent="query",
        provider="FakeLLMClient",
        model="fake-1",
        policy=RedactionPolicy.STRICT,
        columns=["amount", "region"],
    )

    assert record.redacted_columns == {}
    assert not record.was_redacted
    assert record.row_count == 20


def test_the_narrative_record_counts_findings_not_rows():
    """The narrative path sends findings, not rows. Recording them as a bare
    `row_count` would leave a reader comparing incomparable numbers."""
    record = record_for_findings(
        {"Description": 3},
        agent="narrative",
        provider="FakeLLMClient",
        model="fake-1",
        policy=POLICY_BY_PATH["narrative"],
        columns=["Description"],
        finding_count=7,
        redacted_columns={"Description": "free_text"},
    )

    assert record.unit == "findings"
    assert record.row_count == 7
    assert record.policy == "permissive"


def test_a_redactable_column_absent_from_the_payload_is_not_claimed_as_masked():
    """The same invariant the sample path gets structurally: a record names
    only what the redactor actually did. A column the policy WOULD mask but
    which appears in no finding was never masked on this call, and saying
    otherwise would overstate the protection - which is the one direction an
    audit trail must never be wrong in."""
    record = record_for_findings(
        {'notes': 3},  # what the redactor actually masked
        agent='narrative',
        provider='FakeLLMClient',
        model='fake-1',
        policy=POLICY_BY_PATH['narrative'],
        columns=['notes', 'email'],
        finding_count=7,
        # `email` is redactable for this run, but carried no value here.
        redacted_columns={'notes': 'free_text', 'email': 'email'},
    )

    assert record.redacted_columns == {'notes': 'free_text'}
    assert 'email' not in record.redacted_columns
    # It is still listed as a column that was SENT - that part is true.
    assert 'email' in record.columns


# ---- every path is covered, end to end ----
#
# The `client` fixture deliberately configures NO narrative, query or modeling
# LLM, so a default run calls nothing on those paths and records nothing -
# correctly. Each test below opts into the real path with the matching
# override, because a test asserting "the trail has a row" against a run that
# never called out would prove nothing at all.


def _claims_pair():
    return [
        GroundedClaimsResponse(
            claims=[
                GroundedClaim(
                    claim_text="This dataset was explored.",
                    finding_ids=["summary_stat-0"],
                    values=[ClaimValue(label="x", value=1.0)],
                )
            ]
        ),
        NarrativeProse(report_text="This dataset was explored.", recommendations=[]),
    ]


def _source_with_pii(client, tmp_path):
    csv = tmp_path / "people.csv"
    csv.write_text(pii_csv())
    source_id = client.post(
        "/sources", json={"type": "file", "connection_config": {"path": str(csv)}}
    ).json()["id"]
    return source_id, csv


def _run_that_called_a_model(client, tmp_path) -> str:
    """A run whose narrative stage actually called a model."""
    source_id, _ = _source_with_pii(client, tmp_path)
    ingest_and_wait(client, source_id)
    with narrative_llm_override(responses=_claims_pair()):
        return ingest_and_wait(client, source_id)["run_id"]


def test_the_narrative_path_records_its_call(client, tmp_path, db_session):
    run_id = _run_that_called_a_model(client, tmp_path)

    rows = [r for r in _egress_rows(db_session, run_id) if r.agent == "narrative"]
    assert rows, "the narrative path called a model without recording it"
    record = rows[0]
    assert record.unit == "findings"
    assert record.row_count > 0
    assert record.provider == "FakeLLMClient"
    # The narrative path is the permissive one, and the record says so.
    assert record.policy == "permissive"


def test_the_diagnosis_path_records_its_call(client, tmp_path, db_session):
    """Needs a real validation failure, which needs a baseline and then a
    corrupted file - a first ingest has nothing to diagnose. The corruption
    comes from the suite the rest of the tests use rather than being
    hand-rolled, so this cannot quietly stop corrupting anything."""
    clean = _clean_df()
    csv = tmp_path / 'diagnosis.csv'
    _write_csv(csv, clean)
    source_id = client.post(
        '/sources', json={'type': 'file', 'connection_config': {'path': str(csv)}}
    ).json()['id']
    ingest_and_wait(client, source_id)  # establishes the baseline

    corrupted, _truth = CorruptionSuite().apply(clean, 'inject_nulls', seed=100, column='amount')
    _write_csv(csv, corrupted)
    run_id = ingest_and_wait(client, source_id)['run_id']

    assert (
        db_session.query(ValidationEvent).filter(ValidationEvent.run_id == run_id).count() > 0
    ), 'the corruption produced no validation failure, so this test would prove nothing'

    rows = [r for r in _egress_rows(db_session, run_id) if r.agent == 'diagnosis']
    assert rows, 'the diagnosis path called a model without recording it'
    assert rows[0].policy == 'strict'
    assert rows[0].provider == 'FakeLLMClient'
    assert rows[0].unit == 'rows'


def test_the_query_path_records_its_call(client, tmp_path, db_session):
    run_id = _run_with_pii(client, tmp_path)
    generated = GeneratedQuery(
        query_kind="pandas",
        code="result = df.shape[0]",
        columns_referenced=[],
        assumptions=[],
        confidence=0.9,
    )

    with query_llm_override(responses=[generated]):
        client.post("/ask", json={"run_id": run_id, "question": "how many rows are there?"})

    rows = [r for r in _egress_rows(db_session, run_id) if r.agent == "query"]
    assert rows, "the query path called a model without recording it"
    assert rows[0].policy == "strict"


def test_the_modeling_path_records_its_call(client, tmp_path, db_session):
    run_id = _run_with_pii(client, tmp_path)
    intent = IntentClassification(
        intent="prediction", target_column="amount", confidence=0.9, reasoning="asked for a forecast"
    )

    with modeling_llm_override(responses=[intent]):
        client.post("/predict", json={"run_id": run_id, "question": "forecast amount over time"})

    rows = [r for r in _egress_rows(db_session, run_id) if r.agent == "modeling"]
    assert rows, "the modeling path called a model without recording it"
    assert rows[0].policy == "strict"


def test_both_narrative_calls_are_recorded_not_folded_into_one(client, tmp_path, db_session):
    """The narrative agent calls a model TWICE - claims, then prose - and an
    auditor counting how many times this run reached a third party has to
    get the true number. The two are distinguishable by their unit,
    because 12 findings and 5 claims are not the same measurement and a
    shared label would invite comparing them."""
    run_id = _run_that_called_a_model(client, tmp_path)

    narrative = [r for r in _egress_rows(db_session, run_id) if r.agent == 'narrative']
    units = sorted(r.unit for r in narrative)

    assert 'findings' in units, 'the claims call was not recorded'
    assert 'claims' in units, 'the prose call was not recorded'
    # Stage 2 sends the claims stage 1 produced, not column values, so it
    # names no columns and masks nothing - and says so rather than
    # repeating stage 1's list as though it re-sent the data.
    prose = next(r for r in narrative if r.unit == 'claims')
    assert prose.columns_json == []
    assert prose.redacted_columns_json == {}


def test_a_run_that_called_nothing_records_nothing(client, tmp_path, db_session):
    """The trail is worth having only if its numbers are true both ways.
    With no narrative LLM configured and nothing to diagnose, this run
    disclosed nothing, and the log must not invent a disclosure."""
    csv = tmp_path / "clean.csv"
    rows = "\n".join(f"north,{i}" for i in range(60))
    csv.write_text(f"region,amount\n{rows}\n")
    source_id = client.post(
        "/sources", json={"type": "file", "connection_config": {"path": str(csv)}}
    ).json()["id"]
    run_id = ingest_and_wait(client, source_id)["run_id"]

    assert _egress_rows(db_session, run_id) == []


# ---- the audit surface ----


def test_the_audit_endpoint_exposes_the_egress_trail(client, tmp_path):
    run_id = _run_that_called_a_model(client, tmp_path)

    audit = client.get(f"/audit/{run_id}").json()

    assert "egress" in audit
    assert audit["egress"], "a run that called a model must show its calls"
    assert set(audit["egress"][0]) == {
        "id",
        "agent",
        "provider",
        "model",
        "policy",
        "columns",
        "row_count",
        "unit",
        "redacted_columns",
        "masked_value_counts",
        "created_at",
    }


def test_the_audit_endpoint_never_serialises_a_data_value(client, tmp_path, db_session):
    """The same guarantee as the stored row, at the surface that leaves the
    process - two separate places to get it wrong."""
    run_id = _run_exercising_every_path(client, tmp_path, db_session)

    body = json.dumps(client.get(f"/audit/{run_id}").json()["egress"])

    _assert_no_data_in(body, "the /audit egress payload")
