"""Phase 7 ACCEPTANCE: forecast monthly order volume, end to end - the one
place in this suite that fits real Prophet/ARIMA models. That's a local
library/subprocess call, not a network LLM call, so it doesn't violate the
"no live LLM call" rule the rest of this suite holds to; it's still real
computation, so this file is deliberately kept to a single run with a
reduced fold count to bound the cost. Every other forecast gating/logic
path (baseline-beat, insufficient periods) is covered in
tests/test_modeling_pipeline.py against a constructed TrainingOutcome
instead of a real fit.
"""

from __future__ import annotations

from app.db import SessionLocal
from app.modeling.config import ModelingConfig
from app.modeling.pipeline import predict


def _olist_like_csv(tmp_path) -> str:
    """36 months of synthetic orders with a clear upward trend plus a
    Nov/Dec seasonal bump - deterministic (formula-based, no RNG), so a
    real forecast model has genuine signal to beat a naive baseline on."""
    lines = ["order_id,customer_id,session_id,order_date,price"]
    order_index = 0
    for month in range(36):
        year = 2021 + month // 12
        month_of_year = month % 12 + 1
        count = 50 + 3 * month + (40 if month % 12 in (10, 11) else 0)
        for k in range(count):
            day = (k % 27) + 1
            order_date = f"{year:04d}-{month_of_year:02d}-{day:02d}"
            customer_id = f"cust-{order_index % 50}"  # repeats - not an identifier
            session_id = f"sess-{order_index}"  # unique per row - a genuine identifier
            price = 10 + (order_index % 37)
            lines.append(f"order-{order_index},{customer_id},{session_id},{order_date},{price}")
            order_index += 1
    path = tmp_path / "olist_like.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def test_forecast_monthly_order_volume_end_to_end(client, tmp_path):
    csv_path = _olist_like_csv(tmp_path)
    source_id = client.post("/sources", json={"type": "file", "connection_config": {"path": csv_path}}).json()["id"]
    ingest_resp = client.post(f"/ingest/{source_id}")
    assert ingest_resp.status_code == 200, ingest_resp.json()
    run_id = ingest_resp.json()["run_id"]

    # n_splits=3 bounds this test to a handful of real Prophet/ARIMA fits
    # (3 folds x 2 families, plus one final refit of the winner for its
    # prediction interval) rather than the API's default of 5 folds, while
    # keeping each fold's forecast horizon short enough for a real trend
    # model to clearly beat naive-last-value (a 2-fold split leaves a
    # 12-month horizon per fold, long enough that naive becomes
    # competitive purely by chance on a linearly trending series).
    config = ModelingConfig(n_splits=3)
    with SessionLocal() as db:
        record = predict(db, modeling_agent=None, query_agent=None, run_id=run_id, target_column="order_id", config=config)

    assert record.state == "answered", (record.escalation_reason, record.escalation_detail)
    assert record.task_type == "forecast"
    # Forward-chained split - train on past, test on future - never random.
    assert record.split_strategy == "forward_chaining"
    assert record.out_of_sample_metric == "rmse"
    assert record.out_of_sample_score is not None

    # Beats the naive baseline: both are reported, whether it won or not.
    assert record.baseline_scores_json
    naive = next(b for b in record.baseline_scores_json if b["model_family"] == "naive")
    assert record.out_of_sample_score < naive["out_of_sample_score"]

    # Out-of-sample score AND an uncertainty measure, both reported.
    assert record.prediction_interval_json is not None
    assert record.prediction_interval_json["lower"] is not None
    assert record.prediction_interval_json["upper"] is not None
    assert record.prediction_interval_json["lower"] <= record.prediction_interval_json["upper"]

    # Feature exclusions are reported even for a univariate forecast -
    # session_id is unique per row (an identifier) and must be listed, not
    # silently dropped; customer_id repeats and is legitimately kept.
    assert record.excluded_features_json
    excluded_columns = {e["column"] for e in record.excluded_features_json}
    assert "session_id" in excluded_columns
    assert "customer_id" not in excluded_columns

    assert record.row_count_trained_on == 36  # 36 months of aggregated history
    assert record.seed is not None
    assert record.quality_context_summary

    # Predict page chart data (dashboard UX pass, Part 1) - deterministic,
    # computed alongside training, never LLM-touched: every historical
    # actual plus a forward horizon with a real interval band.
    forecast_series = record.forecast_series_json
    assert forecast_series is not None
    assert len(forecast_series["historical"]) == 36
    assert len(forecast_series["forecast"]) == 12  # min(config default 12, 36 // 2)
    for point in forecast_series["forecast"]:
        assert point["lower"] is not None and point["upper"] is not None
        assert point["lower"] <= point["value"] <= point["upper"]
    # The forecast picks up in time right after the historical series ends,
    # never overlapping or leaving a gap.
    assert forecast_series["forecast"][0]["date"] > forecast_series["historical"][-1]["date"]


def test_forecast_question_with_no_target_and_no_model_beating_baseline_never_reports_a_bad_model(client, tmp_path):
    """A flat, unpredictable series: 24 months with the SAME count every
    month plus a deterministic +/-1 wobble. Nothing outperforms naive-last-
    value on a series with no real trend or seasonality to learn, so this
    must escalate honestly rather than presenting a marginal model as
    usable."""
    lines = ["order_id,customer_id,order_date"]
    order_index = 0
    for month in range(24):
        year = 2021 + month // 12
        month_of_year = month % 12 + 1
        count = 40 + (1 if month % 2 == 0 else -1)
        for k in range(count):
            day = (k % 27) + 1
            order_date = f"{year:04d}-{month_of_year:02d}-{day:02d}"
            lines.append(f"order-{order_index},cust-{order_index % 20},{order_date}")
            order_index += 1
    path = tmp_path / "flat.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    source_id = client.post("/sources", json={"type": "file", "connection_config": {"path": str(path)}}).json()["id"]
    ingest_resp = client.post(f"/ingest/{source_id}")
    run_id = ingest_resp.json()["run_id"]

    config = ModelingConfig(n_splits=2)
    with SessionLocal() as db:
        record = predict(db, modeling_agent=None, query_agent=None, run_id=run_id, target_column="order_id", config=config)

    # Either outcome is acceptable here - the point is that whichever it is,
    # it is reported HONESTLY: an escalation carries a real reason, and an
    # answer is never dressed up past what the numbers show.
    assert record.task_type == "forecast"
    if record.state == "awaiting_approval":
        assert record.escalation_reason == "no_model_beats_baseline"
    else:
        assert record.state == "answered"
        naive = next(b for b in record.baseline_scores_json if b["model_family"] == "naive")
        assert record.out_of_sample_score < naive["out_of_sample_score"]
