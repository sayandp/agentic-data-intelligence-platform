"""app/narrative/pipeline.py: the two-stage orchestration end to end -
structural stage separation, deterministic fallback on every kind of LLM
failure, quality context surfaced first in both generation modes, and the
headline adversarial test for the whole phase.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.exploration.engine import ExplorationEngine
from app.exploration.findings import DataQualityContext, ResolutionKind
from app.llm.base import LLMResponseError
from app.narrative.agent import NarrativeAgent
from app.narrative.models import (
    ClaimValue,
    GenerationMode,
    GroundedClaim,
    GroundedClaimsResponse,
    NarrativeProse,
    Recommendation,
)
from app.narrative.pipeline import generate_narrative_report
from tests.fakes import FakeLLMClient


def _clean_findings(run_id="r1", dqc=None):
    rng = np.random.default_rng(0)
    n = 150
    delivery = rng.normal(5, 1, n)
    review = 5 - delivery * 0.6 + rng.normal(0, 0.3, n)
    df = pd.DataFrame({"delivery_time": delivery, "review_score": review})
    dqc = dqc or DataQualityContext(total_events=0)
    findings = ExplorationEngine().run(df, run_id=run_id, data_quality_context=dqc)
    return findings, df


def _correlation_claim(findings):
    corr = next(f for f in findings.findings if f.finding_type == "correlation")
    claim = GroundedClaim(
        claim_text=f"delivery_time and review_score show a pearson correlation of {corr.payload.coefficient:.3f} (n={corr.evidence.sample_size}).",
        finding_ids=[corr.id],
        values=[ClaimValue(label="coefficient", value=corr.payload.coefficient), ClaimValue(label="sample_size", value=float(corr.evidence.sample_size))],
    )
    return corr, claim


# ---- stage 2 receives claims only, never findings ----


def test_stage2_receives_only_claims_never_the_findings_object():
    findings, df = _clean_findings()
    corr, claim = _correlation_claim(findings)
    stage1 = GroundedClaimsResponse(claims=[claim])
    good_prose = NarrativeProse(
        report_text=f"Delivery time and review score correlate at {corr.payload.coefficient:.3f} across {corr.evidence.sample_size} orders.",
        recommendations=[],
    )

    captured_stage2_user_prompts: list[str] = []
    real_client = FakeLLMClient(responses=[stage1, good_prose])
    original_complete = real_client.complete

    def _spying_complete(system, user, response_schema):
        if response_schema is NarrativeProse:
            captured_stage2_user_prompts.append(user)
        return original_complete(system, user, response_schema)

    real_client.complete = _spying_complete
    agent = NarrativeAgent(llm_client=real_client, sleep=lambda _s: None)

    report = generate_narrative_report(findings, df, agent)

    assert report.generation_mode == GenerationMode.LLM
    assert len(captured_stage2_user_prompts) == 1
    stage2_prompt = captured_stage2_user_prompts[0]
    # Only the claim's own text/values reach stage 2 - none of the raw
    # finding-only vocabulary (evidence, payload field names) appears.
    assert claim.claim_text in stage2_prompt
    assert "finding_type" not in stage2_prompt
    assert "evidence" not in stage2_prompt
    assert "data_quality_context" not in stage2_prompt
    assert corr.id not in stage2_prompt.replace(claim.finding_ids[0], "")  # the finding id only appears via the claim's own finding_ids field, not restated elsewhere


# ---- template fallback: LLM unavailable ----


def test_template_fallback_when_narrative_agent_is_none():
    findings, df = _clean_findings()
    report = generate_narrative_report(findings, df, narrative_agent=None)

    assert report.generation_mode == GenerationMode.TEMPLATE
    assert report.fallback_reason == "no LLM configured for this run"
    assert report.grounded_claims  # one claim per finding, still grounded
    assert report.post_check_history == []  # no LLM prose was ever generated to check


def test_template_fallback_when_stage1_raises_on_every_call():
    findings, df = _clean_findings()
    agent = NarrativeAgent(llm_client=FakeLLMClient(default=LLMResponseError("boom")), max_attempts=2, sleep=lambda _s: None)

    report = generate_narrative_report(findings, df, agent)

    assert report.generation_mode == GenerationMode.TEMPLATE
    assert "stage 1" in report.fallback_reason


def test_template_fallback_when_stage1_produces_only_unknown_finding_ids():
    findings, df = _clean_findings()
    bogus_claim = GroundedClaim(claim_text="something about a finding that doesn't exist.", finding_ids=["does-not-exist"], values=[])
    agent = NarrativeAgent(llm_client=FakeLLMClient(default=GroundedClaimsResponse(claims=[bogus_claim])), sleep=lambda _s: None)

    report = generate_narrative_report(findings, df, agent)

    assert report.generation_mode == GenerationMode.TEMPLATE
    # Wording updated with the fallback-diagnosability pass: the reason now
    # leads with the cause in plain words instead of the internal phrase
    # "no usable claims". Asserting the substance rather than the old
    # sentence - the stage, and the specific unknown id that was rejected,
    # which is what makes this diagnosable at all.
    assert "stage 1" in report.fallback_reason
    assert "does-not-exist" in report.fallback_reason


def test_template_fallback_when_stage2_raises_on_every_call():
    findings, df = _clean_findings()
    corr, claim = _correlation_claim(findings)
    stage1 = GroundedClaimsResponse(claims=[claim])
    agent = NarrativeAgent(
        llm_client=FakeLLMClient(responses=[stage1], default=LLMResponseError("boom")), max_attempts=2, sleep=lambda _s: None
    )

    report = generate_narrative_report(findings, df, agent)

    assert report.generation_mode == GenerationMode.TEMPLATE
    assert "stage 2" in report.fallback_reason


def test_template_fallback_after_post_checks_fail_twice():
    """The hallucinated-numeral case, but the point here is the ONE
    regeneration attempt and the subsequent, inevitable fallback."""
    findings, df = _clean_findings()
    corr, claim = _correlation_claim(findings)
    stage1 = GroundedClaimsResponse(claims=[claim])
    hallucinated_prose = NarrativeProse(report_text="Delivery time and review score correlate at 0.999 across 99999 orders.", recommendations=[])

    agent = NarrativeAgent(llm_client=FakeLLMClient(responses=[stage1, hallucinated_prose, hallucinated_prose]), sleep=lambda _s: None)

    report = generate_narrative_report(findings, df, agent)

    assert report.generation_mode == GenerationMode.TEMPLATE
    assert len(report.post_check_history) == 2
    assert not report.post_check_history[0].passed
    assert not report.post_check_history[1].passed
    assert "post-checks failed on 2 attempt(s)" in report.fallback_reason


def test_regeneration_succeeds_on_second_attempt():
    """One failure, one fix - the report should end up in LLM mode, not
    fall back, when the regeneration attempt actually clears the checks."""
    findings, df = _clean_findings()
    corr, claim = _correlation_claim(findings)
    stage1 = GroundedClaimsResponse(claims=[claim])
    bad_prose = NarrativeProse(report_text="Delivery time and review score correlate at 0.999 across 99999 orders.", recommendations=[])
    good_prose = NarrativeProse(
        report_text=f"Delivery time and review score correlate at {corr.payload.coefficient:.3f} across {corr.evidence.sample_size} orders.",
        recommendations=[],
    )

    agent = NarrativeAgent(llm_client=FakeLLMClient(responses=[stage1, bad_prose, good_prose]), sleep=lambda _s: None)

    report = generate_narrative_report(findings, df, agent)

    assert report.generation_mode == GenerationMode.LLM
    assert len(report.post_check_history) == 2
    assert not report.post_check_history[0].passed
    assert report.post_check_history[1].passed


# ---- quality context: present in BOTH modes, always rendered first ----


def test_quality_context_present_and_first_in_llm_mode():
    dqc = DataQualityContext(total_events=2, resolution_counts={ResolutionKind.AUTO_FIXED: 2}, active_baseline_provisional=False)
    findings, df = _clean_findings(dqc=dqc)
    corr, claim = _correlation_claim(findings)
    stage1 = GroundedClaimsResponse(claims=[claim])
    prose = NarrativeProse(
        report_text=f"Delivery time and review score correlate at {corr.payload.coefficient:.3f} across {corr.evidence.sample_size} orders.",
        recommendations=[],
    )
    agent = NarrativeAgent(llm_client=FakeLLMClient(responses=[stage1, prose]), sleep=lambda _s: None)

    report = generate_narrative_report(findings, df, agent)

    assert report.generation_mode == GenerationMode.LLM
    assert "auto" in report.quality_context_summary.lower()
    rendered = report.rendered_text()
    assert rendered.index("Data Quality Context") < rendered.index(report.narrative_text[:20])


def test_quality_context_present_and_first_in_template_mode():
    dqc = DataQualityContext(total_events=1, resolution_counts={ResolutionKind.AUTO_FIX_REVERTED: 1}, active_baseline_provisional=True)
    findings, df = _clean_findings(dqc=dqc)

    report = generate_narrative_report(findings, df, narrative_agent=None)

    assert report.generation_mode == GenerationMode.TEMPLATE
    assert "provisional" in report.quality_context_summary.lower()
    assert "reverted" in report.quality_context_summary.lower()
    rendered = report.rendered_text()
    assert rendered.startswith("## Data Quality Context")


def test_quality_context_is_never_llm_authored():
    """Even when the LLM's own claims/prose say nothing about data
    quality, the summary is still populated - it is rendered
    independently of whatever the LLM produced."""
    dqc = DataQualityContext(total_events=3, resolution_counts={ResolutionKind.AUTO_FIXED: 3}, active_baseline_provisional=False)
    findings, df = _clean_findings(dqc=dqc)
    corr, claim = _correlation_claim(findings)
    stage1 = GroundedClaimsResponse(claims=[claim])
    prose = NarrativeProse(
        report_text=f"Delivery time and review score correlate at {corr.payload.coefficient:.3f} across {corr.evidence.sample_size} orders.",
        recommendations=[],
    )
    agent = NarrativeAgent(llm_client=FakeLLMClient(responses=[stage1, prose]), sleep=lambda _s: None)

    report = generate_narrative_report(findings, df, agent)

    assert "3" in report.quality_context_summary
    assert "auto" in report.quality_context_summary.lower()


# ---- recommendations: confined to their own section ----


def test_recommendations_confined_to_their_own_section_never_inline():
    findings, df = _clean_findings()
    corr, claim = _correlation_claim(findings)
    stage1 = GroundedClaimsResponse(claims=[claim])
    prose = NarrativeProse(
        report_text=f"Delivery time and review score correlate at {corr.payload.coefficient:.3f} across {corr.evidence.sample_size} orders.",
        recommendations=[Recommendation(text="Consider investigating delivery logistics further.", claim_id="claim-0")],
    )
    agent = NarrativeAgent(llm_client=FakeLLMClient(responses=[stage1, prose]), sleep=lambda _s: None)

    report = generate_narrative_report(findings, df, agent)

    assert "Consider investigating" not in report.narrative_text  # never inline in the body
    assert report.recommendations[0].text == "Consider investigating delivery logistics further."
    rendered = report.rendered_text()
    assert "## Recommendations" in rendered
    assert rendered.index("## Recommendations") > rendered.index(report.narrative_text[:20])
    assert "Consider investigating" in rendered  # but present somewhere, in its own section


def test_template_mode_never_has_recommendations():
    """The deterministic fallback never invents suggestions - that would be
    exactly the kind of unwarranted authority Part 4/5 exist to avoid."""
    findings, df = _clean_findings()
    report = generate_narrative_report(findings, df, narrative_agent=None)
    assert report.recommendations == []


# ---- the headline adversarial test ----


def test_adversarial_strong_correlation_does_not_assert_causation():
    """Feed a finding with a strong, tempting correlation (delivery time vs
    review score) and a model that reaches for causal language anyway - the
    system must refuse to ship it, regenerate once, and if the model
    doesn't fix it, fall back to a template that states only correlation."""
    findings, df = _clean_findings()
    corr, claim = _correlation_claim(findings)
    assert abs(corr.payload.coefficient) > 0.5  # a strong, tempting pattern

    stage1 = GroundedClaimsResponse(claims=[claim])
    causal_prose = NarrativeProse(
        report_text=(
            f"Longer delivery times caused lower review scores, with a correlation of "
            f"{corr.payload.coefficient:.3f} across {corr.evidence.sample_size} orders."
        ),
        recommendations=[],
    )
    agent = NarrativeAgent(llm_client=FakeLLMClient(responses=[stage1, causal_prose, causal_prose]), sleep=lambda _s: None)

    report = generate_narrative_report(findings, df, agent)

    full_text = report.rendered_text().lower()
    for banned in ("caused", "causes", "drove", "driver", "led to", "due to", "because of", "impact", "effect", "explains"):
        assert banned not in full_text, f"banned causal term {banned!r} leaked into the final output"
    assert report.generation_mode == GenerationMode.TEMPLATE  # the LLM never got it right, so this MUST be the template
    assert "correlat" in full_text  # the statistical relationship is still reported, just not as causation


# ---- dashboard UX pass, Part 3: NUMBER PRECISION ----


def test_number_fidelity_passes_when_prose_restates_the_rounded_claim_value():
    """Before grounding-time rounding (app/narrative/grounding.py::
    _round_claim_value), a claim value with full float precision (e.g.
    61.271834912) meant the ONLY numeral fidelity would accept in prose was
    that exact long decimal (or its %, /100 counterparts) - a perfectly
    reasonable, readable restatement like "61.27" would fail
    number_fidelity as a hallucinated numeral. This fails BEFORE the fix and
    passes AFTER it, because the claim's own stored value became 61.27 - the
    check itself (app/narrative/postchecks.py) is untouched."""
    findings, df = _clean_findings()
    corr, _claim = _correlation_claim(findings)
    unrounded_claim = GroundedClaim(
        claim_text="delivery_time has a mean value in this run's data.",
        finding_ids=[corr.id],
        values=[ClaimValue(label="mean", value=61.271834912)],
    )
    stage1 = GroundedClaimsResponse(claims=[unrounded_claim])
    prose = NarrativeProse(report_text="The mean delivery time is 61.27.", recommendations=[])
    agent = NarrativeAgent(llm_client=FakeLLMClient(responses=[stage1, prose]), sleep=lambda _s: None)

    report = generate_narrative_report(findings, df, agent)

    assert report.generation_mode == GenerationMode.LLM
    assert report.post_check_history[0].passed
    assert report.grounded_claims[0].values[0].value == 61.27
