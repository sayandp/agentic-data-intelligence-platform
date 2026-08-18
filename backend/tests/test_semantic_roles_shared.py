"""Semantic column roles are detected ONCE and read by every agent.

app/analytics/roles.py always knew that `Customer ID` is an entity
identifier. Only the analytics agent asked, so exploration correlated
invoice numbers against prices, drew a histogram of customer ids, and every
LLM prompt described the columns by dtype alone - leaving the model to infer
from a name that an id was not a quantity.

These tests hold the shared path: identifiers are kept out of the analyses
that treat a column as a MEASUREMENT, every exclusion is recorded with its
reason rather than vanishing, no chart is drawn for one, the role block
reaches each agent's prompt, and a human's confirmation overrides detection
everywhere rather than only in analytics.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.exploration.engine import ExplorationEngine
from app.exploration.findings import DataQualityContext, FindingType
from app.narrative.charts import build_charts
from app.privacy.redaction import RedactedSample
from app.semantic_roles import detect_for_run, identifier_exclusions, prompt_context, roles_document


def _retail_frame(rows: int = 4000) -> pd.DataFrame:
    """Online Retail II's shape - the dataset where the nonsense pairs
    appear. Same column names and roles, small enough to run in a test."""
    rng = np.random.default_rng(11)
    start = pd.Timestamp("2010-01-01")
    # Cardinalities matter to the detector, so they match the real file's
    # shape: ~4 line items per invoice, customers that REPEAT (an id that is
    # nearly unique reads as a key, not an entity), a modest product range.
    return pd.DataFrame(
        {
            "Invoice": [f"5{300000 + i // 4}" for i in range(rows)],
            "StockCode": [f"{85000 + int(x)}" for x in rng.integers(0, 180, rows)],
            "Description": [f"PRODUCT {int(x)}" for x in rng.integers(0, 180, rows)],
            "Quantity": rng.integers(1, 20, rows),
            "InvoiceDate": [start + pd.Timedelta(hours=int(i)) for i in range(rows)],
            "Price": np.round(rng.uniform(0.5, 40.0, rows), 2),
            "Customer ID": rng.integers(12000, 12300, rows).astype(float),
            "Country": rng.choice(["United Kingdom", "France", "Germany"], rows),
        }
    )


def _explore(df: pd.DataFrame, roles: dict | None):
    return ExplorationEngine().run(
        df,
        run_id="run-under-test",
        data_quality_context=DataQualityContext(total_events=0),
        roles=roles,
    )


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    return _retail_frame()


@pytest.fixture(scope="module")
def roles(frame: pd.DataFrame) -> dict:
    return roles_document(detect_for_run(frame, {}))


# ---- the detector's answer is shared, not re-derived ----


def test_the_run_level_document_names_a_role_for_each_key_column(roles):
    by_column = roles["by_column"]

    assert by_column["Customer ID"]["role"] == "entity_id"
    assert by_column["Invoice"]["role"] == "transaction_id"
    assert by_column["InvoiceDate"]["role"] == "event_date"


def test_identifier_columns_are_the_ones_excluded_and_dates_are_not(roles):
    exclusions = identifier_exclusions(roles)

    assert "Customer ID" in exclusions
    assert "Invoice" in exclusions
    # The event date is a role, not an identifier - trends NEED it.
    assert "InvoiceDate" not in exclusions
    assert "Price" not in exclusions


def test_an_exclusion_reason_names_the_role_a_person_would_recognise(roles):
    assert identifier_exclusions(roles)["Customer ID"] == "excluded: detected as entity identifier"


# ---- exploration ----


def test_an_identifier_produces_no_correlation_no_trend_and_no_distribution(frame, roles):
    findings = _explore(frame, roles)

    for finding in findings.findings:
        if finding.finding_type in (
            FindingType.CORRELATION,
            FindingType.TREND,
            FindingType.DISTRIBUTION_SHAPE,
            FindingType.OUTLIER_CLUSTER,
        ):
            assert "Customer ID" not in (finding.columns or []), f"{finding.finding_type} still reported on an identifier"


def test_the_exclusion_is_recorded_in_skipped_with_its_reason(frame, roles):
    """Silence with a reason, never silent absence - the same contract
    suppressed correlations already have."""
    skipped = {entry.column: entry.reason for entry in _explore(frame, roles).skipped}

    assert "Customer ID" in skipped
    assert skipped["Customer ID"] == "excluded: detected as entity identifier"


def test_summary_statistics_for_an_identifier_are_KEPT(frame, roles):
    """"53,628 distinct invoices" is informative. It is the correlation,
    trend and distribution of an id that describe the numbering scheme."""
    findings = _explore(frame, roles)
    summarised = {c for f in findings.findings if f.finding_type == FindingType.SUMMARY_STAT for c in (f.columns or [])}

    assert "Customer ID" in summarised


def test_without_roles_exploration_behaves_exactly_as_before(frame):
    """An older run has no persisted roles. It must keep working, not lose
    findings to a None."""
    before = _explore(frame, None)

    assert any("Customer ID" in (f.columns or []) for f in before.findings)
    assert not [entry for entry in before.skipped if "excluded:" in entry.reason]


# ---- charts ----


def test_no_chart_is_drawn_for_an_identifier_column(frame, roles):
    findings = _explore(frame, roles)
    charts = build_charts(findings, frame, roles=roles)

    for chart in charts:
        assert "Customer ID" not in chart.title
        assert "Invoice" not in chart.title


def test_a_retail_shaped_frame_produces_no_identifier_over_time_chart(frame, roles):
    """The specific nonsense this was reported for: an id plotted against
    the event date."""
    charts = build_charts(_explore(frame, roles), frame, roles=roles)

    identifiers = set(identifier_exclusions(roles))
    for chart in charts:
        assert not (identifiers & set(chart.title.split())), f"{chart.title!r} charts an identifier"


def test_the_chart_layer_alone_suppresses_an_identifier_summary_chart(frame, roles):
    """Exploration keeps the summary stat, so the chart layer is what has to
    refuse to draw it - the two rules are separate on purpose."""
    findings = _explore(frame, roles)

    with_roles = build_charts(findings, frame, roles=roles)
    without = build_charts(findings, frame)

    assert len(with_roles) < len(without)


# ---- the role block reaches the models, as context ----


def test_prompt_context_names_the_column_its_role_and_how_it_was_established(roles):
    context = {entry["column"]: entry for entry in prompt_context(roles)}

    assert context["Customer ID"]["role"] == "entity_id"
    assert context["Customer ID"]["means"] == "entity identifier"
    assert "detected" in context["Customer ID"]["basis"]


def test_a_low_confidence_role_is_labelled_unconfirmed_rather_than_stated_as_fact(roles):
    """Consistent with the confirm-a-role flow: an unconfirmed guess
    presented as settled is how a model reasons confidently about the wrong
    column."""
    weakened = {
        "by_column": {
            "maybe_total": {"role": "monetary", "label": "monetary value", "confidence": "medium", "confirmed": False}
        }
    }

    assert "unconfirmed" in prompt_context(weakened)[0]["basis"]


def test_a_confirmed_role_reads_as_a_persons_decision(roles):
    confirmed = {
        "by_column": {"total_spend": {"role": "monetary", "label": "monetary value", "confidence": "high", "confirmed": True}}
    }

    assert prompt_context(confirmed)[0]["basis"] == "confirmed by a person"


def test_prompt_context_is_empty_and_harmless_for_a_run_with_no_roles():
    assert prompt_context(None) == []
    assert identifier_exclusions(None) == {}


@pytest.mark.parametrize("agent_module", ["query", "modeling"])
def test_each_agent_payload_carries_the_role_block(agent_module, frame, roles, monkeypatch):
    """The block must actually reach the prompt, not just exist."""
    captured = {}

    class _Client:
        """Records the prompt and returns a shell of the right type -
        model_construct skips validation, so this never has to know an
        agent's response schema in order to observe its prompt."""

        model_name = "test-model"

        def complete(self, system, user, response_schema):
            captured["user"] = user
            captured["system"] = system
            return response_schema.model_construct()

    context = prompt_context(roles)

    if agent_module == "query":
        from app.query.agent import QueryAgent
        from app.query.models import QueryKind

        agent = QueryAgent(llm_client=_Client(), sleep=lambda _s: None)
        try:
            agent.generate(
                QueryKind.PANDAS, "how many orders?", {"Price": "float"}, RedactedSample(rows=[]), [], column_roles=context
            )
        except Exception:  # noqa: BLE001 - the prompt is what is under test
            pass
    else:
        from app.modeling.agent import ModelingAgent

        agent = ModelingAgent(llm_client=_Client(), sleep=lambda _s: None)
        try:
            agent.classify_intent("forecast revenue", {"Price": "float"}, RedactedSample(rows=[]), column_roles=context)
        except Exception:  # noqa: BLE001 - the prompt is what is under test
            pass

    assert "column_roles" in captured["user"]
    assert "entity_id" in captured["user"]
    assert "Customer ID" in captured["user"]
    # And the prompt tells the model this is established fact, not a question.
    assert "column_roles" in captured["system"]


# ---- a human's confirmation carries everywhere ----


def test_a_confirmed_role_overrides_detection_in_the_shared_document(frame):
    """Confirmation is source-scoped and used to reach only analytics. The
    shared document is what carries it to exploration, charts and prompts."""
    detected = roles_document(detect_for_run(frame, {}))
    confirmed = roles_document(detect_for_run(frame, {"entity_id": "StockCode"}))

    assert detected["by_column"]["Customer ID"]["role"] == "entity_id"
    assert "StockCode" not in detected["by_column"], "the fixture must not already assign this column"
    assert confirmed["by_column"]["StockCode"]["role"] == "entity_id"
    assert confirmed["by_column"]["StockCode"]["confirmed"] is True


def test_a_confirmed_role_changes_what_exploration_excludes(frame):
    """The proof it is shared: confirming a different entity column moves
    the exclusion, so the same decision reaches exploration."""
    confirmed = roles_document(detect_for_run(frame, {"entity_id": "StockCode"}))
    exclusions = identifier_exclusions(confirmed)

    assert "StockCode" in exclusions
    assert exclusions["StockCode"] == "excluded: confirmed as entity identifier"


def test_a_confirmed_role_changes_which_charts_are_drawn(frame):
    confirmed = roles_document(detect_for_run(frame, {"entity_id": "StockCode"}))
    findings = _explore(frame, confirmed)

    for chart in build_charts(findings, frame, roles=confirmed):
        assert "StockCode" not in chart.title
