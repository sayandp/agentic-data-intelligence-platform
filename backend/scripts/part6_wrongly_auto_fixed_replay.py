"""Phase 7.5 Part 5: re-verifies the "wrongly_auto_fixed stays zero at every
threshold" figure from an ACTUAL run, using the diagnoses already cached at
the stable path Part 4 fixed (.cache/diagnoses.json, or DIAGNOSIS_CACHE_PATH)
- never a fresh live call. A PoisonPillClient raises immediately if
.complete() is ever invoked, so a cache-key mismatch (case-generation drift
since the cache was populated) aborts loudly on the specific group that
missed, rather than silently spending quota - the same guard an earlier
investigation into this exact question used, this time made a genuine,
reusable part of the codebase's own evaluation tooling instead of a
throwaway.

Run: .venv/Scripts/python.exe scripts/part6_wrongly_auto_fixed_replay.py

Requires .cache/diagnoses.json (or $DIAGNOSIS_CACHE_PATH) to already contain
entries from a real scripts/part6_evaluation.py run against the SAME live
provider/model - run that script first if the cache is empty. This script
makes no network call under any circumstance; a case whose cache key isn't
present is skipped and reported separately, never diagnosed fresh.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

import part6_evaluation as pe  # noqa: E402

from app.correlation import correlate_events  # noqa: E402
from app.diagnosis.agent import DiagnosticAgent  # noqa: E402
from app.diagnosis.cache import DiagnosisCache  # noqa: E402
from app.gate import AUTO_APPLY  # noqa: E402
from app.llm.base import LLMClient  # noqa: E402
from app.profiling import BaselineProfiler  # noqa: E402
from app.validation.engine import ValidationEngine  # noqa: E402


class PoisonPillClient(LLMClient):
    """Class name and model_name must match whatever produced the cache
    being replayed (cache keys are provider=type(client).__name__,
    model=client.model_name) - a genuine cache hit looks identical to a
    live run's; .complete() raises instead of ever calling the network."""

    temperature = 0.0

    def __init__(self, model_name: str):
        self.model_name = model_name

    def complete(self, system, user, response_schema):
        raise RuntimeError(
            "CACHE MISS on this group - refusing to make a live call. "
            "Re-run scripts/part6_evaluation.py against the same provider first "
            "if this case is supposed to have a cached diagnosis."
        )


def main(provider_class_name: str = "GeminiClient", model_name: str = "gemini-3.5-flash") -> None:
    clean_df = pe.make_clean_orders()
    baseline_profile = BaselineProfiler().profile(clean_df)
    scratch_dir = Path(__import__("tempfile").mkdtemp(prefix="part6_replay_"))
    cases = pe.build_cases(clean_df, scratch_dir)
    engine = ValidationEngine()

    cache = DiagnosisCache()
    print(f"replaying against cache: {cache._path}")

    # PoisonPillClient's class is renamed dynamically to match the exact
    # provider the cache was populated under - cache_key_for_group hashes
    # type(self.llm_client).__name__, so the class literally has to be
    # named this for a hit to be possible at all.
    PoisonPillClient.__name__ = provider_class_name
    PoisonPillClient.__qualname__ = provider_class_name
    llm_client = PoisonPillClient(model_name=model_name)
    agent = DiagnosticAgent(llm_client=llm_client, cache=cache)

    collected: list[pe.CollectedItem] = []
    skipped_no_cache: list[str] = []
    for case_name, contract, truths in cases:
        failures = engine.validate(contract, baseline_profile)
        groups = correlate_events(failures, baseline_profile, contract)
        for group in groups:
            try:
                outcome = agent.diagnose_group(group, baseline_profile, contract)
            except RuntimeError:
                skipped_no_cache.append(case_name)
                continue
            collected.append(
                pe.CollectedItem(
                    case_name=case_name,
                    truth=pe._match_truth(group, truths),
                    group=group,
                    contract=contract,
                    diagnosis_json=outcome.diagnosis_json,
                    diagnosis_source=outcome.source,
                )
            )

    print(f"groups replayed from cache: {len(collected)}")
    print(f"groups skipped (no cached diagnosis for this exact case shape): {len(skipped_no_cache)} {skipped_no_cache}")
    print()

    results = pe.sweep(collected, baseline_profile, engine)
    print("=== wrongly_auto_fixed, from an ACTUAL attempt_fix + post-condition-verification replay ===")
    for t in pe.THRESHOLDS:
        rows = results[t]
        n_wrong = sum(1 for r in rows if r["category"] == "wrongly_auto_fixed")
        n_auto_applied = sum(1 for r in rows if r["category"] in ("correctly_auto_fixed", "wrongly_auto_fixed", "auto_fix_reverted_by_verification"))
        print(f"  threshold={t}: wrongly_auto_fixed={n_wrong}  total_auto_apply_attempts={n_auto_applied}")

    print()
    print("=== detail at threshold=0.8 (DEFAULT_CONFIDENCE_THRESHOLD) ===")
    for r in results[0.8]:
        print(f"  case={r['case']:<30} category={r['category']:<28} diagnosis_correct={r['diagnosis_correct']}")


if __name__ == "__main__":
    main()
