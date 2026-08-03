from app.diagnosis.cache import DiagnosisCache, cache_key_for_group
from app.validation.engine import ValidationFailure

PROVIDER = "GeminiClient"
MODEL = "gemini-2.5-flash"


def _key(members):
    return cache_key_for_group(members, provider=PROVIDER, model=MODEL)


def test_cache_set_get_round_trip(tmp_path):
    cache = DiagnosisCache(path=tmp_path / "diagnoses.json")
    cache.set("key-1", {"cause_category": "rename"})

    assert cache.get("key-1") == {"cause_category": "rename"}


def test_cache_miss_returns_none(tmp_path):
    cache = DiagnosisCache(path=tmp_path / "diagnoses.json")
    assert cache.get("missing-key") is None


def test_cache_persists_across_instances(tmp_path):
    path = tmp_path / "diagnoses.json"
    DiagnosisCache(path=path).set("key-1", {"cause_category": "dtype_change"})

    reloaded = DiagnosisCache(path=path)
    assert reloaded.get("key-1") == {"cause_category": "dtype_change"}


def test_cache_survives_corrupt_file(tmp_path):
    path = tmp_path / "diagnoses.json"
    path.write_text("not valid json {{{", encoding="utf-8")

    cache = DiagnosisCache(path=path)  # must not raise
    assert cache.get("anything") is None


# ---- cache_key_for_group ----


def test_single_event_key_is_stable():
    a = ValidationFailure(rule_failed="null_threshold:amount", column="amount", detail={"current_null_rate": 0.3})
    b = ValidationFailure(rule_failed="null_threshold:amount", column="amount", detail={"current_null_rate": 0.3})
    assert _key([a]) == _key([b])


def test_key_differs_by_detail_not_just_rule_and_column():
    a = ValidationFailure(rule_failed="null_threshold:amount", column="amount", detail={"current_null_rate": 0.3})
    b = ValidationFailure(rule_failed="null_threshold:amount", column="amount", detail={"current_null_rate": 0.6})
    assert _key([a]) != _key([b])


def test_group_key_covers_every_member_not_just_one_pair():
    """The whole point of a group-aware key: two events correlated together
    must produce a DIFFERENT key than either event alone, and a different
    key than the same first event paired with a different second event."""
    missing = ValidationFailure(rule_failed="schema_conformance:missing_column:city", column="city", detail={})
    unexpected_a = ValidationFailure(
        rule_failed="schema_conformance:unexpected_column:town", column="town", detail={}
    )
    unexpected_b = ValidationFailure(
        rule_failed="schema_conformance:unexpected_column:municipality", column="municipality", detail={}
    )

    key_missing_alone = _key([missing])
    key_pair_a = _key([missing, unexpected_a])
    key_pair_b = _key([missing, unexpected_b])

    assert len({key_missing_alone, key_pair_a, key_pair_b}) == 3


def test_group_key_is_order_independent():
    a = ValidationFailure(rule_failed="schema_conformance:missing_column:city", column="city", detail={})
    b = ValidationFailure(rule_failed="schema_conformance:unexpected_column:town", column="town", detail={})
    assert _key([a, b]) == _key([b, a])


# ---- provider/model are part of the key, not just the failure signature ----


def test_key_differs_by_provider_for_the_same_failure():
    """A FakeLLMClient run and a real Gemini run over the same corruption
    must never share a cache entry - the exact bug this test guards
    against: whichever ran first would otherwise silently serve its result
    to the other forever."""
    a = ValidationFailure(rule_failed="null_threshold:amount", column="amount", detail={"current_null_rate": 0.3})
    gemini_key = cache_key_for_group([a], provider="GeminiClient", model="gemini-2.5-flash")
    fake_key = cache_key_for_group([a], provider="FakeLLMClient", model="fake-model")
    assert gemini_key != fake_key


def test_key_differs_by_model_for_the_same_provider():
    """Swapping gemini-2.5-flash-lite for gemini-2.5-flash must be a cache
    miss, not a stale hit from a different, cheaper model's answer."""
    a = ValidationFailure(rule_failed="null_threshold:amount", column="amount", detail={"current_null_rate": 0.3})
    flash_key = cache_key_for_group([a], provider="GeminiClient", model="gemini-2.5-flash")
    lite_key = cache_key_for_group([a], provider="GeminiClient", model="gemini-2.5-flash-lite")
    assert flash_key != lite_key
