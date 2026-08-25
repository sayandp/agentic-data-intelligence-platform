"""Part 2: the Session Summary Agent.

The constraints the brief called non-negotiable are what this file is about:
the summary reads PERSISTED ARTIFACTS and never the deck, stage 2 never sees a
raw artifact, the post-checks are deterministic, quality context comes first,
and the template fallback states its reason.
"""

import inspect
import json

import pytest

from app.models import EgressEvent, SessionSummary
from app.narrative.models import ClaimValue, GroundedClaim
from app.summary import agent as agent_module
from app.summary.config import SummaryConfig
from app.summary.facts import FactGroup, SummaryFact, build_summary_facts
from app.summary.models import SessionSummaryProse, SummaryClaimsResponse
from app.summary.pipeline import rendered_summary, run_summary_for_run
from app.summary.postchecks import check_length, count_sentences, run_summary_post_checks
from app.summary.template import render_template_summary
from tests.fakes import summary_llm_override
from tests.golden_scenarios import ingest_and_wait

CAUSAL_WORDS = ("because", "caused", "due to", "drove", "led to", "resulted in")


def _ingest(client, tmp_path, name="summary.csv", rows=60):
    csv = tmp_path / name
    csv.write_text("region,amount\n" + "\n".join(f"north,{i}" for i in range(rows)) + "\n")
    source_id = client.post(
        "/sources", json={"type": "file", "connection_config": {"path": str(csv)}}
    ).json()["id"]
    return source_id, ingest_and_wait(client, source_id)


#: Prose that COVERS its claim. The claim-coverage post-check requires every
#: surviving claim to be represented in the prose, so a queued response whose
#: text shares nothing with its claim is correctly rejected - the pipeline
#: then regenerates and the fake runs out of responses mid-run, which reads as
#: an unrelated failure. Keeping the shared word here is what makes these
#: fixtures exercise the happy path they claim to.
_COVERING_PROSE = "The run completed cleanly. Nothing needed a decision. No analysis raised a concern. Nothing changed since the previous run."


def _claims_and_prose(fact_id: str, text: str = _COVERING_PROSE):
    return [
        SummaryClaimsResponse(
            claims=[
                GroundedClaim(
                    claim_text="The run completed cleanly.",
                    finding_ids=[fact_id],
                    values=[ClaimValue(label="events", value=0.0)],
                )
            ]
        ),
        SessionSummaryProse(summary_text=text),
    ]


# ---- it summarises artifacts, never the deck ----


def test_the_agent_never_reads_the_exported_deck():
    """Structural. Reading our own rendered output back would mean the summary
    silently changes whenever deck rendering changes, and two artifacts would
    have to agree forever.

    Checked by parsing IMPORTS rather than searching the text. A first version
    searched for the word "deck" and matched the modules' own docstrings
    explaining why they do not read it - flagging the documentation of the
    guarantee as a violation of it."""
    import ast
    from pathlib import Path

    package = Path(agent_module.__file__).parent
    forbidden_roots = {"pptx", "app.export"}

    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
            elif isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            for name in names:
                assert not any(name == root or name.startswith(root + ".") for root in forbidden_roots), (
                    f"{path.name} imports {name} - the summary must read persisted artifacts, not the deck"
                )


def test_facts_are_built_from_the_persisted_rows(client, tmp_path, db_session):
    from app.models import Run

    _source_id, result = _ingest(client, tmp_path)
    run = db_session.query(Run).filter(Run.id == result["run_id"]).one()

    facts = build_summary_facts(db_session, run)

    assert facts, "a completed run must produce citable facts"
    assert {f.group for f in facts} & {FactGroup.QUALITY, FactGroup.CHANGE}
    for fact in facts:
        assert fact.id and fact.text, "every fact needs an id and a statement"
        assert fact.origin, "every fact must name the artifact it came from"


def test_fact_ids_are_unique(client, tmp_path, db_session):
    """The grounding filter matches claims against these ids; a duplicate
    would let a claim cite one fact and be validated against another."""
    from app.models import Run

    _source_id, result = _ingest(client, tmp_path)
    run = db_session.query(Run).filter(Run.id == result["run_id"]).one()

    ids = [f.id for f in build_summary_facts(db_session, run)]

    assert len(ids) == len(set(ids))


# ---- stage 2 never sees a raw artifact ----


def test_stage_two_has_no_parameter_that_could_carry_an_artifact():
    """Structural rather than a prompt instruction: the function that builds
    stage 2's prompt takes claims and nothing else."""
    signature = inspect.signature(agent_module.SessionSummaryAgent._build_stage2_prompt)

    assert list(signature.parameters) == ["self", "claims"]


def test_stage_two_prompt_contains_only_claim_text(client, tmp_path):
    from app.llm.base import LLMClient

    captured: list[str] = []

    class _Capturing(LLMClient):
        model_name = "capture"
        temperature = 0.0

        def complete(self, system, user, response_model):
            captured.append(user)
            if response_model is SummaryClaimsResponse:
                return SummaryClaimsResponse(
                    claims=[GroundedClaim(claim_text="Something happened.", finding_ids=["fact-quality-0"], values=[])]
                )
            return SessionSummaryProse(summary_text="One. Two. Three. Four.")

    agent = agent_module.SessionSummaryAgent(llm_client=_Capturing(), sleep=lambda _s: None)
    facts = [
        SummaryFact(id="fact-quality-0", group=FactGroup.QUALITY, text="No issues were detected.", origin="x"),
        SummaryFact(id="fact-finding-0", group=FactGroup.FINDING, text="A correlation was reported.", origin="y"),
    ]
    outcome = agent.generate_claims(facts)
    agent.generate_prose(outcome.claims)

    stage2_prompt = captured[-1]
    assert "Something happened." in stage2_prompt
    # No fact statement, no marker, no artifact structure.
    assert "A correlation was reported." not in stage2_prompt
    assert agent_module.FACTS_START_MARKER not in stage2_prompt


# ---- deterministic post-checks ----


def test_a_fabricated_number_fails_the_post_checks():
    claims = [GroundedClaim(claim_id="claim-0", claim_text="12 issues were found.", finding_ids=["fact-quality-0"], values=[ClaimValue(label="issues", value=12.0)])]

    attempt = run_summary_post_checks(claims, "There were 12 issues. Also 9999 records were dropped. Third. Fourth.", 1)

    assert not all(o.passed for o in attempt.outcomes)
    details = " ".join(i.detail for o in attempt.outcomes for i in o.issues)
    assert "9999" in details


def test_causal_language_fails_the_post_checks():
    claims = [GroundedClaim(claim_id="claim-0", claim_text="Nulls appeared.", finding_ids=["fact-quality-0"], values=[])]

    attempt = run_summary_post_checks(claims, "Nulls appeared. This was caused by the upstream feed. Third. Fourth.", 1)

    assert not all(o.passed for o in attempt.outcomes)


def test_a_summary_that_is_too_short_fails():
    """A one-sentence summary has dropped most of what the run found while
    still reading as complete."""
    outcome = check_length("Everything was fine.", SummaryConfig())

    assert not outcome.passed
    assert "below the minimum" in outcome.issues[0].detail


def test_a_summary_that_is_too_long_fails():
    text = " ".join(f"Sentence number {i}." for i in range(20))

    outcome = check_length(text, SummaryConfig())

    assert not outcome.passed
    assert "above the maximum" in outcome.issues[0].detail


def test_the_length_check_is_actually_wired_into_the_post_check_run():
    """The other length tests call check_length directly, so removing its
    wiring from run_summary_post_checks would leave them all green while the
    bound stopped being enforced. Falsification caught exactly that gap."""
    claims = [GroundedClaim(claim_id="claim-0", claim_text="The run completed.", finding_ids=["fact-quality-0"], values=[])]

    attempt = run_summary_post_checks(claims, "The run completed.", 1)

    details = " ".join(i.detail for o in attempt.outcomes for i in o.issues)
    assert "below the minimum" in details, "a one-sentence summary must fail the full post-check run"


def test_a_summary_in_range_passes_length():
    assert check_length("One. Two. Three. Four. Five.", SummaryConfig()).passed


def test_sentence_counting_handles_the_obvious_forms():
    assert count_sentences("One. Two. Three.") == 3
    assert count_sentences("Is it? Yes! Fine.") == 3
    assert count_sentences("") == 0
    assert count_sentences("No terminator") == 1


# ---- template fallback ----


def test_the_template_states_why_it_was_used():
    facts = [SummaryFact(id="fact-quality-0", group=FactGroup.QUALITY, text="No issues were detected.", origin="x")]

    text = render_template_summary(facts, "no language model is configured for this run")

    assert "without a language model" in text
    assert "no language model is configured for this run" in text


def test_the_template_uses_no_causal_language():
    facts = [
        SummaryFact(id="fact-quality-0", group=FactGroup.QUALITY, text="3 issues were detected.", origin="x"),
        SummaryFact(id="fact-change-0", group=FactGroup.CHANGE, text="Nothing measurable changed.", origin="y"),
    ]

    text = render_template_summary(facts, "reason").lower()

    for word in CAUSAL_WORDS:
        assert word not in text


def test_a_run_with_no_llm_gets_a_template_summary_with_the_reason(client, tmp_path, db_session):
    _source_id, result = _ingest(client, tmp_path)

    record = db_session.query(SessionSummary).filter(SessionSummary.run_id == result["run_id"]).one()

    assert record.generation_mode == "template"
    assert record.fallback_reason, "a fallback with no stated reason is indistinguishable from a deliberate choice"
    assert "no language model" in record.fallback_reason


# ---- quality context first ----


def test_quality_context_is_stored_separately_and_rendered_first(client, tmp_path, db_session):
    _source_id, result = _ingest(client, tmp_path)

    record = db_session.query(SessionSummary).filter(SessionSummary.run_id == result["run_id"]).one()

    assert record.quality_context, "quality context is never empty"
    rendered = rendered_summary(record)
    assert rendered.startswith(record.quality_context), "the caveat must come first"
    assert rendered.index(record.quality_context) < rendered.index(record.summary_text)


def test_the_api_exposes_quality_context_as_its_own_field(client, tmp_path):
    _source_id, result = _ingest(client, tmp_path)

    body = client.get(f"/summary/{result['run_id']}").json()

    assert body["quality_context"]
    assert body["rendered"].startswith(body["quality_context"])


# ---- the LLM path ----


def test_the_llm_path_produces_a_summary_and_records_its_claims(client, tmp_path, db_session):
    source_id, _first = _ingest(client, tmp_path)

    with summary_llm_override(responses=_claims_and_prose("fact-quality-0")):
        result = ingest_and_wait(client, source_id)

    record = db_session.query(SessionSummary).filter(SessionSummary.run_id == result["run_id"]).one()

    assert record.generation_mode == "llm"
    assert record.fallback_reason is None
    assert record.claims_json


def test_a_failed_post_check_falls_back_to_the_template_with_the_reason(client, tmp_path, db_session):
    """A summary that failed number fidelity is worse than a plain one: it is
    confident, readable and wrong, for a reader who was told they would not
    need to check."""
    source_id, _first = _ingest(client, tmp_path)

    bad_text = "The run completed cleanly. It found 4242 problems. Nothing else changed. Nothing needs a decision."
    # The queue is consumed POSITIONALLY, not by schema: one claims response,
    # then one prose response per generation attempt. Queuing a second claims
    # response here would be handed to the second prose call and fail as a
    # parse error, which is a different failure from the one under test.
    queued = [*_claims_and_prose("fact-quality-0", bad_text), SessionSummaryProse(summary_text=bad_text)]
    with summary_llm_override(responses=queued):
        result = ingest_and_wait(client, source_id)

    record = db_session.query(SessionSummary).filter(SessionSummary.run_id == result["run_id"]).one()

    assert record.generation_mode == "template"
    assert "post-checks" in (record.fallback_reason or "")
    assert record.post_check_results, "the failed attempts are persisted, not just used as a switch"


def test_a_claim_citing_an_unknown_fact_is_dropped():
    from app.llm.base import LLMClient

    class _Fake(LLMClient):
        model_name = "fake"
        temperature = 0.0

        def complete(self, system, user, response_model):
            return SummaryClaimsResponse(
                claims=[
                    GroundedClaim(claim_text="Real.", finding_ids=["fact-quality-0"], values=[]),
                    GroundedClaim(claim_text="Invented.", finding_ids=["fact-does-not-exist"], values=[]),
                ]
            )

    agent = agent_module.SessionSummaryAgent(llm_client=_Fake(), sleep=lambda _s: None)
    facts = [SummaryFact(id="fact-quality-0", group=FactGroup.QUALITY, text="No issues.", origin="x")]

    outcome = agent.generate_claims(facts)

    assert [c.claim_text for c in outcome.claims] == ["Real."]
    assert outcome.rejected_reasons


# ---- the egress boundary ----


def test_the_summary_path_records_its_outbound_calls(client, tmp_path, db_session):
    source_id, _first = _ingest(client, tmp_path)

    with summary_llm_override(responses=_claims_and_prose("fact-quality-0")):
        result = ingest_and_wait(client, source_id)

    agents = {
        row.agent
        for row in db_session.query(EgressEvent).filter(EgressEvent.run_id == result["run_id"]).all()
    }

    assert "session_summary" in agents, "stage 1 sent facts to a model without recording it"
    assert "session_summary_prose" in agents, "stage 2 called a model without recording it"


def test_the_summary_path_uses_the_strict_redaction_policy(client, tmp_path, db_session):
    source_id, _first = _ingest(client, tmp_path)

    with summary_llm_override(responses=_claims_and_prose("fact-quality-0")):
        result = ingest_and_wait(client, source_id)

    rows = [
        row
        for row in db_session.query(EgressEvent).filter(EgressEvent.run_id == result["run_id"]).all()
        if row.agent.startswith("session_summary")
    ]

    assert rows
    assert {row.policy for row in rows} == {"strict"}


def test_a_quoted_column_value_is_masked_before_it_reaches_the_model():
    """A fact's text never embeds a column value; quoted values travel in
    column_values keyed by column, precisely so the redactor can mask them."""
    import pandas as pd

    from app.llm.base import LLMClient
    from app.privacy.classification import classify_frame

    captured: list[str] = []

    class _Capturing(LLMClient):
        model_name = "capture"
        temperature = 0.0

        def complete(self, system, user, response_model):
            captured.append(user)
            return SummaryClaimsResponse(claims=[])

    frame = pd.DataFrame({"email": [f"p{i}@example.com" for i in range(20)]})
    classification = classify_frame(frame)
    facts = [
        SummaryFact(
            id="fact-finding-0",
            group=FactGroup.FINDING,
            text="The analysis produced a concentration result.",
            column_values={"email": ["p1@example.com", "p2@example.com"]},
            origin="x",
        )
    ]

    agent_module.SessionSummaryAgent(llm_client=_Capturing(), sleep=lambda _s: None).generate_claims(
        facts, privacy=classification
    )

    prompt = captured[0]
    assert "p1@example.com" not in prompt
    assert "<EMAIL_" in prompt


# ---- surfacing ----


def test_an_unknown_run_is_still_a_404(client):
    assert client.get("/summary/does-not-exist").status_code in (404, 400)


def test_a_run_with_no_summary_answers_200_with_a_reason(client, tmp_path, db_session):
    """An expected absence is not an error. The run view fetches this for
    every report it opens, so a 404 here logs a browser console error on a
    page that is working correctly - which is exactly what it did, caught by
    the dashboard-flow spec's no-console-errors assertion."""
    from app.models import Run, SessionSummary

    _source_id, result = _ingest(client, tmp_path)
    db_session.query(SessionSummary).filter(SessionSummary.run_id == result["run_id"]).delete()
    db_session.commit()

    response = client.get(f"/summary/{result['run_id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert "no session summary" in body["reason"]


def test_the_summary_is_idempotent(client, tmp_path, db_session):
    from app.models import Run

    _source_id, result = _ingest(client, tmp_path)
    run = db_session.query(Run).filter(Run.id == result["run_id"]).one()
    first = db_session.query(SessionSummary).filter(SessionSummary.run_id == run.id).one()

    again = run_summary_for_run(db_session, run, agent=None)

    assert again.id == first.id
    assert db_session.query(SessionSummary).filter(SessionSummary.run_id == run.id).count() == 1
