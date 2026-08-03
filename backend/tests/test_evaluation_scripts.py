"""Phase 7.5 Part 4: scripts/part6_evaluation.py's diagnosis cache must
persist across separate invocations of the script, not just within one -
previously it wrote diagnoses.json inside a fresh tempfile.mkdtemp()
directory every run, so a second run always started from an empty cache
and re-spent quota re-diagnosing corruptions a prior run had already paid
for. Fixed by letting DiagnosisCache fall back to its own stable,
env-configurable default path (DIAGNOSIS_CACHE_PATH, default
.cache/diagnoses.json) instead of an explicit ephemeral one.

This test simulates two separate script invocations sharing that stable
path and asserts the SECOND makes zero LLM calls - on FakeLLMClient's own
call_count, never inferred from timing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


@pytest.fixture
def part6_evaluation_module():
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    import part6_evaluation as pe

    return pe


def test_second_evaluation_run_makes_zero_llm_calls(tmp_path, monkeypatch, part6_evaluation_module):
    pe = part6_evaluation_module
    monkeypatch.setenv("DIAGNOSIS_CACHE_PATH", str(tmp_path / "diagnoses.json"))

    from app.diagnosis.agent import DiagnosticAgent
    from app.diagnosis.cache import DiagnosisCache
    from app.profiling import BaselineProfiler
    from app.validation.engine import ValidationEngine
    from tests.fakes import FakeLLMClient

    clean_df = pe.make_clean_orders()
    baseline_profile = BaselineProfiler().profile(clean_df)
    scratch_dir = tmp_path / "scratch"
    scratch_dir.mkdir()
    cases = pe.build_cases(clean_df, scratch_dir)
    engine = ValidationEngine()

    # First "run": DiagnosisCache() with no explicit path picks up
    # DIAGNOSIS_CACHE_PATH from the environment, exactly like the script's
    # own main() now does.
    first_client = FakeLLMClient(model_name="fake-eval-model")
    first_agent = DiagnosticAgent(llm_client=first_client, cache=DiagnosisCache())
    first_collected = pe.diagnose_once(cases, baseline_profile, first_agent, engine)
    assert first_client.call_count > 0
    assert any(item.diagnosis_source == "llm" for item in first_collected)

    # Second "run": a BRAND NEW DiagnosisCache instance and a BRAND NEW
    # FakeLLMClient (same class, same model_name - simulating a fresh
    # process), pointed at the SAME stable path via the same env var.
    second_client = FakeLLMClient(model_name="fake-eval-model")
    second_agent = DiagnosticAgent(llm_client=second_client, cache=DiagnosisCache())
    second_collected = pe.diagnose_once(cases, baseline_profile, second_agent, engine)

    assert second_client.call_count == 0
    assert all(item.diagnosis_source == "cache" for item in second_collected)
    assert len(second_collected) == len(first_collected)


def test_narrative_evaluation_script_has_no_persistent_cache_to_break():
    """Checked per Part 4's explicit instruction ('same for the narrative
    evaluation script if it has the same problem'): it doesn't use a
    DiagnosisCache-shaped persistent cache at all, so there's nothing to
    fix there - this test pins that fact so a future change introducing
    one doesn't silently reintroduce the mkdtemp mistake unnoticed."""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    source = (SCRIPTS_DIR / "narrative_evaluation.py").read_text(encoding="utf-8")
    assert "mkdtemp" not in source
    assert "DiagnosisCache" not in source
