"""Part 1: comparing two runs of one source.

The tests that matter here are the REFUSALS. A comparison view's whole purpose
is to report change, so a comparison that invents a change - by differencing
two numbers computed on different bases, or by rendering "absent" as zero - is
worse than one that shows nothing. Most of this file is about the cases where
the honest output is "these cannot be compared, and here is why".
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.comparison.engine import compare_runs
from app.comparison.identity import exploration_key
from app.comparison.models import Comparability, Delta, SectionComparison
from app.models import Baseline, BusinessAnalysis, DataSource, ExplorationFinding, ModelRun, Run
from tests.golden_scenarios import ingest_and_wait

#: Words a comparison must never produce. Two runs differ in every
#: uncontrolled way at once, so nothing here can support a causal claim.
CAUSAL_WORDS = (
    "because",
    "caused",
    "due to",
    "drove",
    "led to",
    "resulted in",
    "as a result",
    "explains",
    "thanks to",
    "attributable",
)


def _source(db) -> DataSource:
    source = DataSource(type="file", connection_config={"path": "x.csv"})
    db.add(source)
    db.flush()
    return source


def _run(db, source, number, *, status="completed", minutes=0, columns=None, rows=60) -> Run:
    run = Run(
        source_id=source.id,
        status=status,
        run_number=number,
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=minutes),
        completed_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=minutes + 1),
        contract_metadata={"row_count": rows, "column_types": columns or {"id": "int64", "amount": "float64"}},
    )
    db.add(run)
    db.flush()
    return run


def _analytics(db, run, *, basis="price x quantity", results=None):
    record = BusinessAnalysis(
        run_id=run.id,
        schema_version=1,
        findings_json={
            "value_definition": {"label": basis, "monetary_column": "price"},
            "results": results
            if results is not None
            else [
                {
                    "analysis": "abc_pareto",
                    "ran": True,
                    "findings": [
                        {
                            "finding_type": "concentration_band",
                            "columns": ["customer_id"],
                            "payload": {"band": "A", "entity_count": 10, "value_share": 0.8},
                        }
                    ],
                }
            ],
        },
    )
    db.add(record)
    db.flush()
    return record


# ---- blocked entirely ----


def test_runs_of_different_sources_are_not_compared_at_all(db_session):
    source_a, source_b = _source(db_session), _source(db_session)
    run_a = _run(db_session, source_a, 1)
    run_b = _run(db_session, source_b, 2, minutes=10)

    result = compare_runs(db_session, run_a, run_b)

    assert result.blocked_reason is not None
    assert "different sources" in result.blocked_reason
    assert result.sections == [], "a blocked comparison must not render half a diff"


def test_an_incomplete_run_blocks_the_comparison(db_session):
    """Exploration and analytics run only on a completed run, so comparing
    against a failed one would silently compare against partial output."""
    source = _source(db_session)
    run_a = _run(db_session, source, 1)
    run_b = _run(db_session, source, 2, status="failed", minutes=10)

    result = compare_runs(db_session, run_a, run_b)

    assert result.blocked_reason is not None
    assert "did not reach completed" in result.blocked_reason


def test_a_run_cannot_be_compared_with_itself(db_session):
    source = _source(db_session)
    run = _run(db_session, source, 1)

    assert compare_runs(db_session, run, run).blocked_reason is not None


# ---- the value-basis trap ----


def test_different_value_bases_make_analytics_not_comparable(db_session):
    """The central refusal. A unit-price basis and a price x quantity basis
    differ by orders of magnitude and both look reasonable in isolation, so a
    delta between them is a number with no meaning shown with the authority of
    a measurement."""
    source = _source(db_session)
    run_a = _run(db_session, source, 1)
    run_b = _run(db_session, source, 2, minutes=10)
    _analytics(db_session, run_a, basis="unit price")
    _analytics(db_session, run_b, basis="price x quantity")

    section = _section(compare_runs(db_session, run_a, run_b), "analytics")

    assert section.comparability is Comparability.NOT_COMPARABLE
    assert "different value bases" in section.reason
    assert "unit price" in section.reason and "price x quantity" in section.reason
    assert section.deltas == [], "a not-comparable section must not also show deltas"


def test_matching_value_bases_do_compare(db_session):
    """The other half: the guard must not refuse legitimate comparisons."""
    source = _source(db_session)
    run_a = _run(db_session, source, 1)
    run_b = _run(db_session, source, 2, minutes=10)
    _analytics(db_session, run_a, basis="price x quantity")
    _analytics(db_session, run_b, basis="price x quantity")

    section = _section(compare_runs(db_session, run_a, run_b), "analytics")

    assert section.comparability is Comparability.COMPARABLE
    assert section.deltas


def test_a_superseded_baseline_makes_analytics_not_comparable(db_session):
    """The later run's figures are measured against a different definition of
    normal, so a delta would mix a change in the data with a change in what it
    was compared to."""
    source = _source(db_session)
    run_a = _run(db_session, source, 1, minutes=0)
    run_b = _run(db_session, source, 2, minutes=60)
    _analytics(db_session, run_a)
    _analytics(db_session, run_b)
    db_session.add(
        Baseline(
            source_id=source.id,
            profile_json={},
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=30),
        )
    )
    db_session.flush()

    section = _section(compare_runs(db_session, run_a, run_b), "analytics")

    assert section.comparability is Comparability.NOT_COMPARABLE
    assert "baseline was superseded" in section.reason


# ---- never a delta against nothing ----


def test_an_analysis_present_in_one_run_only_is_membership_not_a_delta(db_session):
    source = _source(db_session)
    run_a = _run(db_session, source, 1)
    run_b = _run(db_session, source, 2, minutes=10)
    _analytics(db_session, run_a, results=[{"analysis": "rfm", "ran": False, "findings": [], "reason": "no monetary role"}])
    _analytics(
        db_session,
        run_b,
        results=[
            {
                "analysis": "rfm",
                "ran": True,
                "findings": [{"finding_type": "segment", "columns": ["c"], "payload": {"segment": "champions", "size": 12}}],
            }
        ],
    )

    section = _section(compare_runs(db_session, run_a, run_b), "analytics")

    labels = [m.label for m in section.memberships]
    assert any("rfm" in label for label in labels)
    assert all("rfm" not in d.label for d in section.deltas), "an analysis that ran once must not produce a delta"


def test_exploration_missing_from_one_run_is_reported_not_differenced(db_session):
    source = _source(db_session)
    run_a = _run(db_session, source, 1)
    run_b = _run(db_session, source, 2, minutes=10)
    db_session.add(
        ExplorationFinding(run_id=run_b.id, schema_version=1, findings_json={"findings": []})
    )
    db_session.flush()

    section = _section(compare_runs(db_session, run_a, run_b), "exploration")

    assert section.comparability is Comparability.ONE_SIDED
    assert section.reason
    assert section.deltas == []


# ---- delta arithmetic ----


def test_a_change_from_zero_reports_no_relative_change():
    """Not infinity, not 0%, not "new" - each of those is a different wrong
    answer. The absolute change is the honest output."""
    delta = Delta(label="rules fired", before=0.0, after=5.0)

    assert delta.absolute == 5.0
    assert delta.relative is None
    assert delta.changed


def test_relative_change_uses_the_magnitude_of_the_earlier_value():
    delta = Delta(label="score", before=-4.0, after=-2.0)

    assert delta.absolute == 2.0
    assert delta.relative == 0.5


def test_a_delta_with_a_missing_side_reports_neither_change():
    delta = Delta(label="x", before=None, after=3.0)

    assert delta.absolute is None
    assert delta.relative is None
    assert not delta.changed


# ---- structure ----


def test_a_non_comparable_section_cannot_be_built_without_a_reason():
    """'Not comparable' with no reason is an error message pretending to be an
    answer. The type refuses to hold that state."""
    with pytest.raises(ValueError, match="gives no reason"):
        SectionComparison(name="analytics", comparability=Comparability.NOT_COMPARABLE)


def test_finding_identity_survives_renumbering():
    """Exploration ids are positional (`correlation-3`), so matching on them
    would report every finding after an insertion as both disappeared and
    appeared."""
    before = {"id": "correlation-3", "finding_type": "correlation", "columns": ["price", "quantity"], "payload": {}}
    after = {"id": "correlation-7", "finding_type": "correlation", "columns": ["quantity", "price"], "payload": {}}

    assert exploration_key(before) == exploration_key(after)


def test_a_column_that_changed_kind_is_reported_rather_than_differenced(db_session):
    """A numeric column that starts arriving as text has summary fields that
    are not the same measurement - a mean against a cardinality."""
    source = _source(db_session)
    run_a = _run(db_session, source, 1)
    run_b = _run(db_session, source, 2, minutes=10)
    for run, kind, payload in (
        (run_a, "numeric", {"kind": "numeric", "mean": 50.0, "count": 10}),
        (run_b, "categorical", {"kind": "categorical", "cardinality": 10, "count": 10}),
    ):
        db_session.add(
            ExplorationFinding(
                run_id=run.id,
                schema_version=1,
                findings_json={"findings": [{"finding_type": "summary_stat", "columns": ["amount"], "payload": payload}]},
            )
        )
    db_session.flush()

    section = _section(compare_runs(db_session, run_a, run_b), "exploration")

    assert section.notes["kind_changes"], "a kind change must be reported"
    assert section.notes["kind_changes"][0]["before"] == "numeric"
    assert not any("amount" in d.label for d in section.deltas), "numbers of different kinds must not be differenced"


def test_the_model_section_refuses_a_different_target_or_metric(db_session):
    source = _source(db_session)
    run_a = _run(db_session, source, 1)
    run_b = _run(db_session, source, 2, minutes=10)
    db_session.add(
        ModelRun(run_id=run_a.id, source_id=source.id, question="q", target_column="revenue", out_of_sample_metric="r2", out_of_sample_score=0.8, state="answered")
    )
    db_session.add(
        ModelRun(run_id=run_b.id, source_id=source.id, question="q", target_column="churn", out_of_sample_metric="roc_auc", out_of_sample_score=0.9, state="answered")
    )
    db_session.flush()

    section = _section(compare_runs(db_session, run_a, run_b), "model")

    assert section.comparability is Comparability.NOT_COMPARABLE
    assert "not the same measurement" in section.reason
    assert section.deltas == []


# ---- language ----


def test_no_comparison_output_uses_causal_language(db_session):
    source = _source(db_session)
    run_a = _run(db_session, source, 1)
    run_b = _run(db_session, source, 2, minutes=10, columns={"id": "int64"}, rows=90)
    _analytics(db_session, run_a, basis="unit price")
    _analytics(db_session, run_b, basis="price x quantity")

    blob = str(compare_runs(db_session, run_a, run_b).to_dict()).lower()

    for word in CAUSAL_WORDS:
        assert word not in blob, f"comparison output used causal language: {word!r}"


def _section(comparison, name) -> SectionComparison:
    match = [s for s in comparison.sections if s.name == name]
    assert match, f"no {name} section in {[s.name for s in comparison.sections]}"
    return match[0]


# ---- the route itself ----
#
# The engine is tested above by calling it directly. These cover what only the
# route does: defaulting, argument order, and the 400s.


def _completed_pair(client, tmp_path, name="route_compare.csv"):
    """Two completed runs of one source, through the real ingest path."""
    csv = tmp_path / name
    csv.write_text("region,amount\n" + "\n".join(f"north,{i}" for i in range(60)) + "\n")
    source_id = client.post(
        "/sources", json={"type": "file", "connection_config": {"path": str(csv)}}
    ).json()["id"]
    first = ingest_and_wait(client, source_id)
    second = ingest_and_wait(client, source_id)
    return source_id, first, second


def test_source_id_defaults_to_the_two_most_recent_completed_runs(client, tmp_path):
    source_id, first, second = _completed_pair(client, tmp_path)

    body = client.get(f"/compare?source_id={source_id}").json()

    assert {body["run_a"]["run_id"], body["run_b"]["run_id"]} == {first["run_id"], second["run_id"]}


def test_the_earlier_run_is_always_a_regardless_of_argument_order(client, tmp_path):
    """Otherwise the same pair renders two different sets of signs depending
    on which id the caller happened to type first."""
    _source_id, first, second = _completed_pair(client, tmp_path)

    forwards = client.get(f"/compare?run_a={first['run_id']}&run_b={second['run_id']}").json()
    backwards = client.get(f"/compare?run_a={second['run_id']}&run_b={first['run_id']}").json()

    assert forwards["run_a"]["run_id"] == backwards["run_a"]["run_id"] == first["run_id"]
    assert forwards["run_b"]["run_id"] == backwards["run_b"]["run_id"] == second["run_id"]


def test_a_source_with_one_run_is_a_400_naming_how_many_it_has(client, tmp_path):
    """"Nothing changed" and "there is nothing to compare" are different
    answers and must not render the same."""
    csv = tmp_path / "single.csv"
    csv.write_text("region,amount\n" + "\n".join(f"north,{i}" for i in range(60)) + "\n")
    source_id = client.post(
        "/sources", json={"type": "file", "connection_config": {"path": str(csv)}}
    ).json()["id"]
    ingest_and_wait(client, source_id)

    response = client.get(f"/compare?source_id={source_id}")

    assert response.status_code == 400
    assert "1 completed run(s)" in response.json()["detail"]


def test_calling_compare_with_no_arguments_is_refused(client):
    assert client.get("/compare").status_code == 400
