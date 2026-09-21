"""A template fallback must say WHY, not just that it happened.

A real report fell back with `stage 1 (grounding) unavailable:
escalated_parse_failure` - which records the outcome and destroys the
evidence. At least four different causes produced that identical string:

  1. the response was truncated at max_output_tokens (gemini-3.5-flash
     spends thinking tokens from the same budget - see MAX_OUTPUT_TOKENS
     in app/llm/gemini_client.py),
  2. the response was complete but schema-invalid (e.g. a claim citing no
     finding at all - GroundedClaim.finding_ids has min_length=1, so an
     empty list fails at Pydantic parse time rather than in grounding),
  3. a weaker model served the call,
  4. the provider returned HTTP 5xx and there was no response at all.

(4) turned out to be the actual cause of the reported incident,
reproduced live: a 503 "this model is currently experiencing high demand"
was classified as a malformed-response error, so an OUTAGE was recorded
as a parse failure - and sent down the repair path, re-asking a server
that was down to fix JSON it had never sent. Fixed in
app/llm/base.py::LLMUnavailableError.

Distinguishing all of them after the fact is the point of these tests.
None of this loosens grounding validation: rejecting an ungrounded claim
stays a correct rejection, and the tests below assert it still happens.
"""

from __future__ import annotations

import pydantic
import pytest

from app.llm.base import LLMRateLimitError, LLMResponseError, LLMUnavailableError
from app.narrative.agent import NarrativeAgent
from app.narrative.models import GenerationMode, GroundedClaim, GroundedClaimsResponse
from app.narrative.pipeline import generate_narrative_report
from tests.fakes import FakeLLMClient
from tests.test_narrative_pipeline import _clean_findings


def _truncated_response_error() -> LLMResponseError:
    """What app/llm/gemini_client.py raises when the model was cut off
    mid-JSON: a validation error PLUS the finish_reason proving why."""
    return LLMResponseError(
        "response did not match GroundedClaimsResponse: unterminated string",
        finish_reason="MAX_TOKENS",
        raw_text='{"claims": [{"claim_text": "revenue rose stead',
        model_name="gemini-3.5-flash",
        schema_name="GroundedClaimsResponse",
        usage={"prompt_tokens": 3000, "output_tokens": 4096, "thoughts_tokens": 3900, "total_tokens": 7096},
    )


def _schema_rejection_error() -> LLMResponseError:
    """A COMPLETE response that the schema correctly refused - a claim
    citing nothing. finish_reason is STOP, not MAX_TOKENS."""
    return LLMResponseError(
        "response did not match GroundedClaimsResponse: claims.0.finding_ids List should have at least 1 item",
        finish_reason="STOP",
        raw_text='{"claims": [{"claim_text": "revenue rose", "finding_ids": []}]}',
        model_name="gemini-3.5-flash-lite",
        schema_name="GroundedClaimsResponse",
    )


# ---- the error object carries the evidence ----


def test_truncation_is_distinguishable_from_a_schema_rejection():
    truncated = _truncated_response_error()
    rejected = _schema_rejection_error()

    assert truncated.truncated is True
    assert rejected.truncated is False
    assert truncated.cause_summary() != rejected.cause_summary()
    assert "cut off" in truncated.cause_summary()


def test_diagnostic_detail_carries_finish_reason_model_tokens_and_raw_body():
    detail = _truncated_response_error().diagnostic_detail()

    assert "finish_reason=MAX_TOKENS" in detail
    assert "model=gemini-3.5-flash" in detail
    assert "schema=GroundedClaimsResponse" in detail
    # The thinking-token count is the number that actually explains a
    # truncation on this provider.
    assert "thoughts_tokens=3900" in detail
    assert "output_tokens=4096" in detail
    # The raw body shows WHERE it stopped.
    assert "revenue rose stead" in detail


def test_a_bare_response_error_still_summarises_without_provider_detail():
    """A provider that reports no finish_reason must still produce a usable
    message rather than crashing the diagnostics path."""
    bare = LLMResponseError("boom")

    assert bare.truncated is False
    assert bare.cause_summary()
    assert "finish_reason=unknown" in bare.diagnostic_detail()


# ---- the cause reaches the report a human reads ----


def _report_for_stage1_error(error: Exception):
    findings, df = _clean_findings()
    agent = NarrativeAgent(llm_client=FakeLLMClient(default=error), max_attempts=2, sleep=lambda _s: None)
    return generate_narrative_report(findings, df, agent)


def test_truncated_stage1_says_the_response_was_cut_off():
    report = _report_for_stage1_error(_truncated_response_error())

    assert report.generation_mode == GenerationMode.TEMPLATE
    assert "couldn't ground the narrative" in report.fallback_reason
    assert "cut off" in report.fallback_reason
    # The machine-readable code stays available for log correlation.
    assert "stage 1" in report.fallback_reason
    assert "escalated_parse_failure" in report.fallback_reason
    # And it reaches the rendered report, not just the model object.
    assert "cut off" in report.rendered_text()


def test_schema_rejected_stage1_does_not_claim_truncation():
    report = _report_for_stage1_error(_schema_rejection_error())

    assert report.generation_mode == GenerationMode.TEMPLATE
    assert "did not match the required format" in report.fallback_reason
    assert "cut off" not in report.fallback_reason


def test_quota_exhaustion_reads_as_quota_not_as_a_parse_failure():
    findings, df = _clean_findings()
    agent = NarrativeAgent(
        llm_client=FakeLLMClient(default=LLMRateLimitError("429 RESOURCE_EXHAUSTED")), max_attempts=2, sleep=lambda _s: None
    )

    report = generate_narrative_report(findings, df, agent)

    assert "quota" in report.fallback_reason
    assert "escalated_quota_exhausted" in report.fallback_reason


def test_a_model_that_returns_zero_claims_says_so_rather_than_reading_as_a_failure():
    """Parses cleanly, cites nothing - source is "llm", so this previously
    rendered as the uninformative "stage 1 (grounding) unavailable: llm"."""
    findings, df = _clean_findings()
    agent = NarrativeAgent(llm_client=FakeLLMClient(default=GroundedClaimsResponse(claims=[])), sleep=lambda _s: None)

    report = generate_narrative_report(findings, df, agent)

    assert report.generation_mode == GenerationMode.TEMPLATE
    assert "produced no claims" in report.fallback_reason


def test_the_failing_model_name_is_recorded_for_correlating_with_key_rotation():
    """Candidate 3: if parse failures track a particular model, the model
    that served the failing call has to be recoverable."""
    findings, _df = _clean_findings()
    agent = NarrativeAgent(
        llm_client=FakeLLMClient(default=_schema_rejection_error(), model_name="gemini-3.5-flash-lite"),
        max_attempts=2,
        sleep=lambda _s: None,
    )

    outcome = agent.generate_claims(findings)

    assert outcome.model_name == "gemini-3.5-flash-lite"
    assert any("model=gemini-3.5-flash-lite" in reason for reason in outcome.rejected_reasons)


# ---- a provider outage is not a parse failure ----
#
# This was the ACTUAL cause of the reported fallback. Reproduced live: a
# 503 "model is currently experiencing high demand" came back as
# source=escalated_parse_failure with the summary "the model's response did
# not match the required format" - describing a response that never
# existed.


def test_a_transient_outage_is_reported_as_unavailable_not_a_parse_failure():
    findings, df = _clean_findings()
    agent = NarrativeAgent(
        llm_client=FakeLLMClient(default=LLMUnavailableError("503 UNAVAILABLE. model is experiencing high demand")),
        max_attempts=2,
        sleep=lambda _s: None,
    )

    report = generate_narrative_report(findings, df, agent)

    assert report.generation_mode == GenerationMode.TEMPLATE
    assert "temporarily unavailable" in report.fallback_reason
    assert "escalated_unavailable" in report.fallback_reason
    # The old, wrong story must not be told any more.
    assert "escalated_parse_failure" not in report.fallback_reason
    assert "did not match the required format" not in report.fallback_reason


def test_a_dropped_connection_through_the_real_client_still_produces_a_report():
    """End to end through the REAL GeminiClient, not a fake that raises the
    platform's own error type - the fake is exactly what hid this. The SDK
    raising httpx.ConnectError used to escape the client, the agent and the
    node, leaving a completed run with no report at all. It must degrade to
    the template, with the reason stated."""
    import types as pytypes

    import httpx

    from app.llm.gemini_client import GeminiClient

    client = GeminiClient(api_key="fake-key", model="gemini-3.5-flash")

    class DroppedConnection:
        def generate_content(self, **kwargs):
            raise httpx.ConnectError("[WinError 10054] An existing connection was forcibly closed by the remote host")

    client._client = pytypes.SimpleNamespace(models=DroppedConnection())

    findings, df = _clean_findings()
    agent = NarrativeAgent(llm_client=client, max_attempts=2, sleep=lambda _s: None)
    report = generate_narrative_report(findings, df, agent)

    assert report.generation_mode == GenerationMode.TEMPLATE
    assert report.narrative_text, "a dropped connection must still leave a report to read"
    assert "temporarily unavailable" in report.fallback_reason
    assert "escalated_parse_failure" not in report.fallback_reason


def test_an_outage_is_retried_with_backoff_rather_than_sent_to_the_repair_path():
    """The repair path re-asks the model to "respond again with ONLY the
    JSON object". Against a 5xx that is meaningless - there is no
    malformed output to repair - and it spent a call to learn nothing.
    max_attempts calls, all of them plain retries, is the correct shape."""
    findings, _df = _clean_findings()
    client = FakeLLMClient(default=LLMUnavailableError("503 UNAVAILABLE"))
    slept: list[float] = []
    agent = NarrativeAgent(llm_client=client, max_attempts=3, sleep=slept.append)

    outcome = agent.generate_claims(findings)

    assert outcome.source == "escalated_unavailable"
    assert client.call_count == 3
    # Backed off between attempts (2 gaps for 3 attempts), never a
    # tight loop against a struggling server.
    assert len(slept) == 2
    # No repair prompt was ever sent.
    assert all("could not be parsed" not in user for _system, user in client.calls)


def test_a_genuine_parse_failure_still_uses_the_repair_path():
    """The repair retry is right for a malformed response and must stay:
    one original call plus one repair attempt."""
    findings, _df = _clean_findings()
    client = FakeLLMClient(default=_schema_rejection_error())
    agent = NarrativeAgent(llm_client=client, max_attempts=3, sleep=lambda _s: None)

    outcome = agent.generate_claims(findings)

    assert outcome.source == "escalated_parse_failure"
    assert client.call_count == 2
    assert any("could not be parsed" in user for _system, user in client.calls)


# ---- grounding validation itself is unchanged ----


def test_an_empty_finding_ids_list_is_still_refused_by_the_schema():
    """The whole point of stage 1. This must keep raising - a claim that
    cites nothing can never be admitted just to make parsing succeed."""
    with pytest.raises(pydantic.ValidationError):
        GroundedClaim(claim_text="revenue rose", finding_ids=[], values=[])
