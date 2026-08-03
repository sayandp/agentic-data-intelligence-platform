"""Tests for app/resolution.py::process_group - the shared diagnose -> gate
-> apply-and-verify-or-escalate step used by both the ingest auto-apply
queue and the approvals human-approve path.

Focus: Part 0's corrected do-no-harm behavior end to end. A fix that
REVEALS a previously not_applicable rule (rather than regressing a
passing one) is kept, and the reveal is raised and processed as its own
validation event through the exact same pipeline - never a silent drop,
never treated as a reason to revert the fix that revealed it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.contract import DataContract, SourceType
from app.correlation import CorrelatedGroup
from app.db import SessionLocal
from app.diagnosis.agent import DiagnosticAgent
from app.diagnosis.models import CauseCategory, Diagnosis, FixAction, RiskLevel, SuggestedFix
from app.models import Baseline, DataSource, Run, ValidationEvent
from app.profiling import BaselineProfiler
from app.resolution import GroupProcessingOutcome, process_group, process_queue
from app.state_machine import AUTO_FIXED, AWAITING_APPROVAL, DETECTED
from app.validation.engine import ValidationFailure
from tests.fakes import FakeLLMClient, InMemoryDiagnosisCache

SAFE_TYPE_CAST_DIAGNOSIS = Diagnosis(
    cause_category=CauseCategory.DTYPE_CHANGE,
    likely_cause="numeric column re-typed as text upstream",
    suggested_fix=SuggestedFix(action=FixAction.SAFE_TYPE_CAST),
    risk_level=RiskLevel.LOW,
    confidence=0.95,
)


@pytest.fixture
def clean_df():
    return pd.DataFrame(
        {
            "id": range(80),
            "amount": np.linspace(1.0, 100.0, 80),
            "city": (["New York", "Los Angeles", "Chicago"] * 27)[:80],
        }
    )


@pytest.fixture
def baseline_profile(clean_df):
    return BaselineProfiler().profile(clean_df)


def _agent(default: Diagnosis) -> DiagnosticAgent:
    return DiagnosticAgent(llm_client=FakeLLMClient(default=default), cache=InMemoryDiagnosisCache(), sleep=lambda _s: None)


def test_safe_type_cast_unmasking_drift_is_kept_new_event_raised_and_escalates(clean_df, baseline_profile):
    """The scenario named in the brief. 'amount' is corrupted to a string
    dtype AND scaled 1000x - distribution_drift can't be scored while the
    column is non-numeric (not_applicable), so the drift has been sitting
    there invisibly the whole time. safe_type_cast clears its own
    dtype_mismatch; that succeeding is what finally makes the drift
    observable. The fix must be KEPT (this is a reveal, not a regression -
    nothing that was evaluated-and-passing started failing), a fresh
    validation_event must be raised for the drift, and processing THAT
    event must escalate - distribution_drift's applicability matrix permits
    no auto-fix action at all, for any diagnosis, at any confidence."""
    corrupted = clean_df.copy()
    corrupted["amount"] = (clean_df["amount"] * 1000).astype(str)
    contract = DataContract(data=corrupted, source_type=SourceType.FILE, source_id="src-1")

    with SessionLocal() as db:
        source = DataSource(type="file", connection_config={"path": "unused"})
        db.add(source)
        db.flush()
        baseline = Baseline(source_id=source.id, profile_json=baseline_profile, is_active=True, is_provisional=False)
        db.add(baseline)
        run = Run(source_id=source.id, status="running")
        db.add(run)
        db.flush()

        group = CorrelatedGroup(
            members=[
                ValidationFailure(rule_failed="schema_conformance:dtype_mismatch:amount", column="amount", detail={"expected_dtype": "float64"})
            ]
        )
        event = ValidationEvent(
            run_id=run.id,
            rule_failed=group.members[0].rule_failed,
            state=DETECTED,
            column_name="amount",
            detail_json=group.members[0].detail,
            against_provisional_baseline=False,
        )
        db.add(event)
        db.flush()

        diagnostic_agent = _agent(SAFE_TYPE_CAST_DIAGNOSIS)
        result = process_group(db, run, contract, group, [event], baseline, diagnostic_agent, confidence_threshold=0.8)

        # The fix is kept: no revert, no do-no-harm error, own rule cleared.
        assert not result.escalated
        assert result.fix_chain_entry == {"action": "safe_type_cast", "spec": {"column": "amount", "target_dtype": "float64"}}
        assert result.contract.data["amount"].dtype == "float64"
        assert event.state == AUTO_FIXED
        assert event.action_taken == "auto_fixed"

        # A new validation event was raised for the revealed drift.
        assert len(result.revealed) == 1
        revealed_group, revealed_events = result.revealed[0]
        assert revealed_group.members[0].rule_failed == "distribution_drift:amount"
        assert len(revealed_events) == 1
        revealed_event = revealed_events[0]
        assert revealed_event.run_id == run.id
        assert revealed_event.rule_failed == "distribution_drift:amount"
        assert revealed_event.state == DETECTED
        assert revealed_event.action_taken is None  # not yet processed

        # Processing the revealed group exactly like the outer queue would -
        # escalates, since the gate's applicability matrix denies every
        # action for distribution_drift regardless of the diagnosis.
        revealed_result = process_group(
            db, run, result.contract, revealed_group, revealed_events, baseline, diagnostic_agent, confidence_threshold=0.8
        )

        assert revealed_result.escalated
        assert revealed_result.fix_chain_entry is None
        assert revealed_result.revealed == []
        assert revealed_event.state == AWAITING_APPROVAL
        assert revealed_event.action_taken is None  # never attempted - denied before any fix ran
        assert any("not permitted" in reason for reason in (revealed_event.gate_reasons_json or []))


def test_fix_with_no_reveal_leaves_revealed_empty(clean_df, baseline_profile):
    """Sanity check: an ordinary auto-fix with nothing hidden behind it
    produces no revealed groups at all."""
    corrupted = clean_df.copy()
    corrupted["amount"] = corrupted["amount"].astype(str)  # same scale - no drift once cast back
    contract = DataContract(data=corrupted, source_type=SourceType.FILE, source_id="src-1")

    with SessionLocal() as db:
        source = DataSource(type="file", connection_config={"path": "unused"})
        db.add(source)
        db.flush()
        baseline = Baseline(source_id=source.id, profile_json=baseline_profile, is_active=True, is_provisional=False)
        db.add(baseline)
        run = Run(source_id=source.id, status="running")
        db.add(run)
        db.flush()

        group = CorrelatedGroup(
            members=[
                ValidationFailure(rule_failed="schema_conformance:dtype_mismatch:amount", column="amount", detail={"expected_dtype": "float64"})
            ]
        )
        event = ValidationEvent(
            run_id=run.id, rule_failed=group.members[0].rule_failed, state=DETECTED, column_name="amount", against_provisional_baseline=False
        )
        db.add(event)
        db.flush()

        result = process_group(db, run, contract, group, [event], baseline, _agent(SAFE_TYPE_CAST_DIAGNOSIS), confidence_threshold=0.8)

        assert not result.escalated
        assert result.revealed == []
        assert event.state == AUTO_FIXED


def test_reveal_chain_hitting_depth_cap_escalates_cleanly_rather_than_spinning(clean_df, baseline_profile, monkeypatch):
    """Termination of a reveal chain currently holds only because no
    allowlisted action can undo another's precondition - an accident of
    ALLOWLIST_ACTIONS, not a structural guarantee (no real fix chains this
    deep, which is exactly why this test has to simulate one). A
    monkeypatched process_group that ALWAYS reveals a fresh group - an
    infinite generator if nothing bounds it - must still terminate: anything
    still outstanding once depth exceeds the cap escalates directly, without
    being diagnosed further, rather than looping forever."""
    calls = []

    def _always_reveals(db, run, contract, group, events, baseline, diagnostic_agent, confidence_threshold):
        calls.append(group)
        new_event = ValidationEvent(
            run_id=run.id, rule_failed="distribution_drift:amount", state=DETECTED, column_name="amount", against_provisional_baseline=False
        )
        db.add(new_event)
        new_group = CorrelatedGroup(members=[ValidationFailure(rule_failed="distribution_drift:amount", column="amount", detail={})])
        return GroupProcessingOutcome(contract=contract, revealed=[(new_group, [new_event])])

    monkeypatch.setattr("app.resolution.process_group", _always_reveals)

    contract = DataContract(data=clean_df, source_type=SourceType.FILE, source_id="src-1")

    with SessionLocal() as db:
        source = DataSource(type="file", connection_config={"path": "unused"})
        db.add(source)
        db.flush()
        baseline = Baseline(source_id=source.id, profile_json=baseline_profile, is_active=True, is_provisional=False)
        db.add(baseline)
        run = Run(source_id=source.id, status="running")
        db.add(run)
        db.flush()

        group = CorrelatedGroup(
            members=[ValidationFailure(rule_failed="schema_conformance:dtype_mismatch:amount", column="amount", detail={})]
        )
        event = ValidationEvent(run_id=run.id, rule_failed=group.members[0].rule_failed, state=DETECTED, against_provisional_baseline=False)
        db.add(event)
        db.flush()

        outcome = process_queue(db, run, contract, [(group, [event])], baseline, diagnostic_agent=object(), confidence_threshold=0.8, reveal_depth_cap=2)

        # Depths 0, 1, 2 are each processed (3 calls); depth 3 is the first
        # to exceed the cap and escalates WITHOUT a fourth process_group call.
        assert len(calls) == 3
        assert outcome.escalated
        assert outcome.max_depth_reached == 3
        assert run.reveal_depth_reached == 3

        # The final, uncapped-depth event escalated with the cap as its
        # recorded reason, never silently dropped or looped on forever.
        db.flush()
        still_pending = db.query(ValidationEvent).filter(ValidationEvent.state == AWAITING_APPROVAL).all()
        assert len(still_pending) == 1
        assert any("reveal-depth cap" in r for r in (still_pending[0].gate_reasons_json or []))


def test_reveal_chain_within_cap_never_escalates_on_depth_alone(clean_df, baseline_profile, monkeypatch):
    """Sanity check the cap doesn't fire early: a chain exactly as deep as
    the cap allows must be fully processed, not partially escalated."""
    calls = []

    def _reveals_twice_then_stops(db, run, contract, group, events, baseline, diagnostic_agent, confidence_threshold):
        calls.append(group)
        depth_seen = len(calls) - 1
        if depth_seen >= 2:  # depths 0 and 1 reveal further; depth 2 is the last one, reveals nothing
            return GroupProcessingOutcome(contract=contract)
        new_event = ValidationEvent(
            run_id=run.id, rule_failed="distribution_drift:amount", state=DETECTED, column_name="amount", against_provisional_baseline=False
        )
        db.add(new_event)
        new_group = CorrelatedGroup(members=[ValidationFailure(rule_failed="distribution_drift:amount", column="amount", detail={})])
        return GroupProcessingOutcome(contract=contract, revealed=[(new_group, [new_event])])

    monkeypatch.setattr("app.resolution.process_group", _reveals_twice_then_stops)

    contract = DataContract(data=clean_df, source_type=SourceType.FILE, source_id="src-1")

    with SessionLocal() as db:
        source = DataSource(type="file", connection_config={"path": "unused"})
        db.add(source)
        db.flush()
        baseline = Baseline(source_id=source.id, profile_json=baseline_profile, is_active=True, is_provisional=False)
        db.add(baseline)
        run = Run(source_id=source.id, status="running")
        db.add(run)
        db.flush()

        group = CorrelatedGroup(
            members=[ValidationFailure(rule_failed="schema_conformance:dtype_mismatch:amount", column="amount", detail={})]
        )
        event = ValidationEvent(run_id=run.id, rule_failed=group.members[0].rule_failed, state=DETECTED, against_provisional_baseline=False)
        db.add(event)
        db.flush()

        outcome = process_queue(db, run, contract, [(group, [event])], baseline, diagnostic_agent=object(), confidence_threshold=0.8, reveal_depth_cap=2)

        assert len(calls) == 3  # depths 0, 1, 2 - all within the cap, none escalated by the depth guard
        assert not outcome.escalated
        assert outcome.max_depth_reached == 2
        assert run.reveal_depth_reached == 2
