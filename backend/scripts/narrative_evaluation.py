"""Phase 5 Part 6: Narrative Agent evaluation, extending the Part 6 harness
pattern (scripts/part6_evaluation.py) - same "no real API key in this
environment, disclose the deterministic fake instead of hiding it" posture,
same markdown-table reporting style.

Three things this script produces:

1. AUTOMATED metrics across a small scenario suite: number-fidelity pass
   rate, causal-language violation count, claim-coverage pass rate,
   template-fallback rate. The suite deliberately includes scenarios
   engineered to fail each check at least once - a script that only ever
   sees clean output proves nothing about whether the checks work.

2. A HUMAN RUBRIC (factual grounding / clarity / actionability, 1-5) with
   the scoring instructions written out, plus a review packet (the actual
   generated reports) for a human panel to score against it. This script
   does NOT fabricate scores - there is no panel available in this
   environment, and inventing numbers here would be exactly the kind of
   ungrounded claim the whole phase exists to prevent.

3. The ADVERSARIAL test: a strong, tempting correlation (delivery time vs
   review score) fed through a model that reaches for causal language
   anyway, confirming the shipped output never asserts causation. This is
   the headline test of the phase, run here as an executable demonstration
   in addition to tests/test_narrative_pipeline.py's pytest version.

LLM PROVIDER NOTE: defaults to the real provider (get_llm_client(), Gemini -
the only provider this deployment runs) if GEMINI_API_KEY is set; otherwise
uses a deterministic SmartFakeNarrativeLLMClient, disclosed below, not
hidden. Set GEMINI_API_KEY to run this against a real model.

Run: .venv/Scripts/python.exe scripts/narrative_evaluation.py
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()  # GEMINI_API_KEY etc. from .env - this script doesn't import app.main, so nothing else loads it

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app.exploration.engine import ExplorationEngine  # noqa: E402
from app.exploration.findings import DataQualityContext, ExplorationFindings, ResolutionKind  # noqa: E402
from app.llm.base import LLMClient  # noqa: E402
from app.narrative.agent import NarrativeAgent  # noqa: E402
from app.narrative.models import (  # noqa: E402
    ClaimValue,
    GenerationMode,
    GroundedClaim,
    GroundedClaimsResponse,
    NarrativeProse,
    PostCheckKind,
)
from app.narrative.pipeline import generate_narrative_report  # noqa: E402

CLEAN_SEED = 0
WIDE_SEED = 1
ADVERSARIAL_SEED = 2


# =============================================================================
# A deterministic fake LLM, used ONLY because this environment has no
# GEMINI_API_KEY. `behavior` controls what it does on each call, keyed by
# response_schema - deliberately including failure modes so the automated
# checks below have something real to catch.
# =============================================================================


class SmartFakeNarrativeLLMClient(LLMClient):
    temperature = 0.0

    def __init__(self, behavior: str, claim_lookup: dict[str, GroundedClaim]):
        self.model_name = f"smart-fake-narrative-eval ({behavior}) - no GEMINI_API_KEY in this environment"
        self.behavior = behavior
        self.claim_lookup = claim_lookup
        self.calls: list[str] = []

    def complete(self, system, user, response_schema):
        self.calls.append(response_schema.__name__)
        if response_schema is GroundedClaimsResponse:
            return GroundedClaimsResponse(claims=list(self.claim_lookup.values()))
        if response_schema is NarrativeProse:
            return self._stage2_response()
        raise AssertionError(f"unexpected response_schema {response_schema!r}")

    def _stage2_response(self) -> NarrativeProse:
        claims = list(self.claim_lookup.values())
        if self.behavior == "clean":
            text = " ".join(c.claim_text for c in claims)
            return NarrativeProse(report_text=text, recommendations=[])
        if self.behavior == "hallucinated_number":
            text = " ".join(c.claim_text for c in claims) + " This corresponds to roughly 99999 affected records."
            return NarrativeProse(report_text=text, recommendations=[])
        if self.behavior == "causal":
            text = " ".join(c.claim_text for c in claims)
            text += " This pattern shows that the first variable directly caused the change in the second."
            return NarrativeProse(report_text=text, recommendations=[])
        if self.behavior == "dropped_claim":
            text = " ".join(c.claim_text for c in claims[:-1]) if len(claims) > 1 else ""
            return NarrativeProse(report_text=text, recommendations=[])
        raise AssertionError(f"unknown behavior {self.behavior!r}")


# =============================================================================
# Scenario fixtures
# =============================================================================


def make_clean_olist_like(n: int = 300, seed: int = CLEAN_SEED) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    cities = ["New York", "Los Angeles", "San Francisco", "Chicago", "Houston"]
    dates = pd.date_range("2022-01-01", periods=n, freq="D")
    price = rng.normal(loc=120.0, scale=35.0, size=n).round(2).clip(min=1.0)
    return pd.DataFrame(
        {
            "order_date": dates,
            "customer_city": rng.choice(cities, size=n),
            "price": price,
            "quantity": rng.integers(1, 6, size=n),
            "freight_value": price * 0.1 + rng.normal(0, 2, n),
        }
    )


def make_wide(n: int = 150, seed: int = WIDE_SEED) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame({f"metric_{i}": rng.normal(0, 1, n) for i in range(60)})


def make_adversarial(n: int = 300, seed: int = ADVERSARIAL_SEED) -> pd.DataFrame:
    """delivery_time vs review_score - a strong, tempting, obviously
    'causal-sounding' correlation that is, structurally, only ever a
    correlation as far as this system is concerned."""
    rng = np.random.default_rng(seed)
    delivery_time = rng.normal(6.0, 1.5, n).clip(min=1.0)
    review_score = (5.2 - delivery_time * 0.5 + rng.normal(0, 0.4, n)).clip(1.0, 5.0)
    return pd.DataFrame({"delivery_time_days": delivery_time, "review_score": review_score})


def _claims_for(findings: ExplorationFindings) -> dict[str, GroundedClaim]:
    """Deterministic, hand-authored grounded claims (standing in for a real
    stage-1 LLM call, since the fake's job here is stage 2's behavior, not
    stage 1's) - always correctly grounded so any failure the automated
    checks catch comes from stage 2 alone.

    Deliberately favors ONE correlation claim plus ONE numeric-summary
    claim over several same-template summary claims: two summary_stat
    claims share enough vocabulary ("has a mean of ... across ... row(s)")
    that dropping one can look "covered" by the keyword fallback matching
    on the shared template words alone, not the dropped claim's actual
    content - picking distinctly-worded claim types is what makes the
    dropped_claim scenario below an honest demonstration.
    """
    claims: dict[str, GroundedClaim] = {}

    # Pass 1: the correlation claim, if any - found first so its columns
    # are known before picking a summary claim (summary_stat findings are
    # constructed BEFORE correlation findings in every run, so scanning in
    # finding order would otherwise pick a summary column before knowing
    # which columns to avoid).
    correlation_columns: set[str] = set()
    for finding in findings.findings:
        if finding.finding_type == "correlation":
            payload = finding.payload
            # Deliberately omits sample_size here (unlike the real
            # app/narrative/template.py, which always includes it) - two
            # claims from the same run legitimately can share a row count,
            # and that coincidence would make the dropped_claim scenario
            # below misleading (a claim can look "covered" by a shared
            # number it never actually stated). Keeping this claim's only
            # number distinctive is what makes that scenario honest.
            text = f"{payload.column_a} and {payload.column_b} show a {payload.method.value} correlation of {payload.coefficient:.3f}."
            values = [ClaimValue(label="coefficient", value=payload.coefficient)]
            claims[finding.id] = GroundedClaim(claim_text=text, finding_ids=[finding.id], values=values)
            correlation_columns = {payload.column_a, payload.column_b}
            break

    # Pass 2: one numeric summary claim, about a column NOT already named
    # in the correlation claim - a summary claim sharing a column name with
    # the correlation claim would make a dropped correlation claim look
    # "covered" by coincidence (the keyword fallback matching on the
    # shared column name alone, not the dropped claim's actual content).
    for finding in findings.findings:
        payload = finding.payload
        if finding.finding_type == "summary_stat" and payload.kind == "numeric" and payload.mean is not None:
            column = finding.columns[0]
            if column in correlation_columns:
                continue
            text = f"{column} has a mean of {payload.mean:.3f} across {payload.count} row(s)."
            values = [ClaimValue(label="mean", value=payload.mean), ClaimValue(label="count", value=float(payload.count))]
            claims[finding.id] = GroundedClaim(claim_text=text, finding_ids=[finding.id], values=values)
            break

    return claims


@dataclass
class ScenarioResult:
    name: str
    behavior: str
    generation_mode: str
    fallback_reason: str | None
    post_check_pass_by_kind: dict[str, list[bool]] = field(default_factory=dict)
    report_excerpt: str = ""


def run_scenario(name: str, df: pd.DataFrame, behavior: str, dqc: DataQualityContext | None = None) -> ScenarioResult:
    """The deterministic-fake scenarios - these test OUR safety net
    (post-checks, grounding, fallback), not a model's behavior, so they run
    identically regardless of whether a real API key is configured. Forcing
    'hallucinated_number' / 'causal' / 'dropped_claim' on demand isn't
    something a real, well-behaved model would reliably reproduce - that's
    the point of using a scripted fake for these specifically."""
    dqc = dqc or DataQualityContext(total_events=0)
    findings = ExplorationEngine().run(df, run_id=f"eval-{name}", data_quality_context=dqc)
    claim_lookup = _claims_for(findings)

    client = SmartFakeNarrativeLLMClient(behavior=behavior, claim_lookup=claim_lookup)
    agent = NarrativeAgent(llm_client=client, sleep=lambda _s: None)
    report = generate_narrative_report(findings, df, agent)

    pass_by_kind: dict[str, list[bool]] = {k.value: [] for k in PostCheckKind}
    for attempt in report.post_check_history:
        for outcome in attempt.outcomes:
            pass_by_kind[outcome.kind.value].append(outcome.passed)

    return ScenarioResult(
        name=name,
        behavior=behavior,
        generation_mode=report.generation_mode.value,
        fallback_reason=report.fallback_reason,
        post_check_pass_by_kind=pass_by_kind,
        report_excerpt=report.rendered_text(),
    )


def run_live_scenario(name: str, df: pd.DataFrame, dqc: DataQualityContext | None = None) -> ScenarioResult:
    """The REAL provider, via get_llm_client() (LLM_PROVIDER/GEMINI_API_KEY
    from the environment, same as production) - only called when a real key
    is actually configured (see main()). Unlike run_scenario, this asks a
    genuine model to behave well on its own; the post-checks are still the
    thing that decides whether the output ships, not a judgment call made
    here."""
    from app.llm.factory import get_llm_client

    dqc = dqc or DataQualityContext(total_events=0)
    findings = ExplorationEngine().run(df, run_id=f"eval-live-{name}", data_quality_context=dqc)

    agent = NarrativeAgent(llm_client=get_llm_client())
    report = generate_narrative_report(findings, df, agent)

    pass_by_kind: dict[str, list[bool]] = {k.value: [] for k in PostCheckKind}
    for attempt in report.post_check_history:
        for outcome in attempt.outcomes:
            pass_by_kind[outcome.kind.value].append(outcome.passed)

    return ScenarioResult(
        name=name,
        behavior="live",
        generation_mode=report.generation_mode.value,
        fallback_reason=report.fallback_reason,
        post_check_pass_by_kind=pass_by_kind,
        report_excerpt=report.rendered_text(),
    )


# =============================================================================
# Reporting
# =============================================================================


def _markdown_table(headers: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)


HUMAN_RUBRIC = """
=== HUMAN RUBRIC (factual grounding / clarity / actionability) ===

Scored 1-5 by a small panel (recommended: at least 3 independent scorers per
report, scores averaged). Each report is scored against ALL THREE criteria
independently - a report can be clear but ungrounded, or grounded but
unreadable, and both are real failure modes this rubric has to distinguish.

FACTUAL GROUNDING (1-5)
  5 - Every number and claim in the report is directly traceable to a
      finding; nothing reads as invented, estimated, or extrapolated beyond
      what was given.
  3 - Mostly traceable, but at least one figure or claim is ambiguous about
      its source (not clearly wrong, just not clearly grounded either).
  1 - Contains at least one number or claim that does not correspond to
      anything in the underlying findings.
  Instruction to scorer: for every numeral and every causal-sounding or
  qualitative claim in the report, try to find the exact finding it comes
  from. If you cannot, mark it and lower the score. This is independent of
  (and a manual cross-check on) the automated number-fidelity/claim-
  coverage checks - score what YOU can verify, not what the system claims
  it verified.

CLARITY (1-5)
  5 - A reader unfamiliar with the underlying data understands what was
      found without needing to re-read any sentence.
  3 - Understandable but requires some effort (dense phrasing, unclear
      referents, awkward merges of multiple findings into one sentence).
  1 - Confusing or ambiguous enough that a reader would misunderstand what
      was actually found.
  Instruction to scorer: read the report exactly once, at normal reading
  speed. Score on that single pass - don't re-read to "figure it out";
  needing to re-read is itself a clarity failure.

ACTIONABILITY (1-5)
  5 - Recommendations (if present) are specific, clearly tied to a stated
      finding, and phrased as suggestions requiring human judgement - never
      as conclusions the data proves.
  3 - Recommendations are present but generic, or their connection to a
      specific finding is unclear.
  1 - No recommendations where a plainly actionable one was warranted, OR a
      recommendation is phrased as fact/certainty rather than a suggestion.
  Instruction to scorer: a report with NO recommendations is not
  automatically a 1 - only score low if a recommendation was clearly
  warranted (e.g. an obvious data quality or outlier issue) and none was
  offered, or if a recommendation overstates its own certainty.

Record: report_id, scorer_id, three scores, and one free-text sentence
justifying the lowest of the three scores given. This is what makes the
scoring reproducible after the fact, not just a number.
"""


def main() -> None:
    have_real_provider = "GEMINI_API_KEY" in os.environ
    print("=== Config ===")
    print(
        f"deterministic-fake scenarios: SmartFakeNarrativeLLMClient (always, regardless of provider - "
        f"these test the post-check safety net, not a model's behavior)"
    )
    print(
        f"live scenarios: {'REAL provider (get_llm_client(), LLM_PROVIDER=' + os.environ.get('LLM_PROVIDER', 'gemini') + ')' if have_real_provider else 'SKIPPED (no GEMINI_API_KEY set)'}"
    )
    print()

    scenarios = [
        ("clean_olist_like", make_clean_olist_like(), "clean", None),
        ("wide_dataset", make_wide(), "clean", None),
        ("hallucinated_number", make_clean_olist_like(seed=CLEAN_SEED + 1), "hallucinated_number", None),
        ("dropped_claim", make_clean_olist_like(seed=CLEAN_SEED + 2), "dropped_claim", None),
        (
            "repaired_data_quality_context",
            make_clean_olist_like(seed=CLEAN_SEED + 3),
            "clean",
            DataQualityContext(total_events=2, resolution_counts={ResolutionKind.AUTO_FIXED: 2}, active_baseline_provisional=False),
        ),
        ("adversarial_correlation_causal", make_adversarial(), "causal", None),
    ]

    results = [run_scenario(name, df, behavior, dqc) for name, df, behavior, dqc in scenarios]

    print("=== Automated: generation mode per scenario ===")
    print(
        _markdown_table(
            ["scenario", "behavior", "generation_mode", "fallback_reason"],
            [[r.name, r.behavior, r.generation_mode, r.fallback_reason or "-"] for r in results],
        )
    )
    print()

    print("=== Automated: post-check pass rate by kind (across all attempts, all scenarios) ===")
    totals: dict[str, list[bool]] = {k.value: [] for k in PostCheckKind}
    for r in results:
        for kind, passes in r.post_check_pass_by_kind.items():
            totals[kind].extend(passes)
    rows = []
    for kind, passes in totals.items():
        rate = f"{sum(passes)}/{len(passes)}" if passes else "n/a (no attempts)"
        rows.append([kind, rate])
    print(_markdown_table(["check", "pass_rate"], rows))
    print()

    causal_violations = sum(1 for r in results for p in r.post_check_pass_by_kind.get("causal_language", []) if not p)
    template_fallback_rate = sum(1 for r in results if r.generation_mode == "template") / len(results)
    print(f"Causal-language violations caught across all attempts: {causal_violations}")
    print(f"Template-fallback rate across scenarios: {template_fallback_rate:.0%} ({sum(1 for r in results if r.generation_mode == 'template')}/{len(results)})")
    print()

    print("=== ADVERSARIAL TEST (headline of the phase): strong correlation, causal-reaching model ===")
    adversarial = next(r for r in results if r.name == "adversarial_correlation_causal")
    print(f"generation_mode: {adversarial.generation_mode} (must be 'template' - the fake model never stops reaching for causal language)")
    banned_terms = ("caused", "causes", "drove", "driver", "led to", "due to", "because of", "impact", "effect", "explains", "directly caused")
    leaked = [t for t in banned_terms if t in adversarial.report_excerpt.lower()]
    print(f"banned terms leaked into final output: {leaked if leaked else 'NONE'}")
    print()
    print("--- final adversarial report (as shipped) ---")
    print(adversarial.report_excerpt)
    print()

    live_results: list[ScenarioResult] = []
    if have_real_provider:
        print("=== LIVE: real provider, two scenarios (clean + adversarial) ===")
        print("Not the full six-scenario suite - hallucinated_number/dropped_claim/causal test OUR")
        print("safety net on demand, which needs a scripted bad actor, not a well-behaved live model.")
        print("Kept to two calls out of respect for free-tier per-minute quota.")
        print()
        live_results = [
            run_live_scenario("clean_olist_like_live", make_clean_olist_like(seed=CLEAN_SEED + 10)),
            run_live_scenario("adversarial_correlation_live", make_adversarial(seed=ADVERSARIAL_SEED + 10)),
        ]
        print(
            _markdown_table(
                ["scenario", "generation_mode", "fallback_reason"],
                [[r.name, r.generation_mode, r.fallback_reason or "-"] for r in live_results],
            )
        )
        print()
        for r in live_results:
            print(f"--- {r.name} (as shipped, generation_mode={r.generation_mode}) ---")
            print(r.report_excerpt)
            print()
            if r.name == "adversarial_correlation_live":
                leaked = [t for t in banned_terms if t in r.report_excerpt.lower()]
                print(f"banned terms leaked into LIVE adversarial output: {leaked if leaked else 'NONE'}")
                print()
    else:
        print("=== LIVE scenarios SKIPPED (no GEMINI_API_KEY set) ===")
        print()

    print(HUMAN_RUBRIC)

    print("=== Review packet for the human panel: clean scenario report (as shipped) ===")
    review_source = live_results[0] if live_results else next(r for r in results if r.name == "clean_olist_like")
    print(f"[report_id={review_source.name}, generation_mode={review_source.generation_mode}]")
    print(review_source.report_excerpt)
    print()
    print("(Scores for the above are NOT fabricated here - no human panel is available in this")
    print(" environment. A real evaluation run attaches this script's printed reports as the")
    print(" review packet and records panel scores separately, per the rubric's instructions.)")


if __name__ == "__main__":
    main()
