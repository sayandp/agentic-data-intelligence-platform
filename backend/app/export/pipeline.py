"""Assembles a DeckSources from committed rows, and owns the export job.

Every field comes from a table. Nothing is recomputed, re-fetched from the
source, or inferred - if the run did not persist it, it does not reach the
deck. That is what makes the deck and the dashboard incapable of disagreeing.

Generation is a BACKGROUND task with a polled status, the same shape POST
/ingest and POST /predict already use: kaleido renders every chart through a
real browser, which is seconds of work, and a synchronous request would hold
the connection open for all of it.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.export.deck import DeckSources, build_deck
from app.analytics.chart_specs import charts_are_current, charts_for_results
from app.models import (
    AgentTrace,
    BusinessAnalysis,
    ConfirmedColumnRole,
    SessionSummary,
    DataSource,
    ModelRun,
    Report,
    Run,
    ValidationEvent,
)

EXPORT_DIR = Path("data/exports")

#: In-process job state. Deliberately not a table: a half-built deck has no
#: meaning across a restart, and the file on disk is the only durable
#: artifact worth keeping.
_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


def deck_path(run: Run) -> Path:
    return EXPORT_DIR / f"run-{run.run_number or run.id}.pptx"


def _analytics_payload(analytics: BusinessAnalysis | None) -> dict | None:
    """The analytics document, with chart specs derived on read when the row
    predates them.

    Same function the analytics API and the analysis pipeline use, so a deck
    built from an older run gets the identical figures the screen shows for
    it - rather than the deck silently omitting charts for exactly the runs
    that existed before this feature.
    """
    if analytics is None:
        return None
    payload = dict(analytics.findings_json)
    if not charts_are_current(payload.get("charts")):
        payload["charts"] = charts_for_results(payload.get("results") or [])
    return payload


def collect_sources(db: Session, run: Run) -> DeckSources:
    """Reads the run's artifacts. One query per table, no derivation."""
    source = db.get(DataSource, run.source_id)
    report = db.query(Report).filter(Report.run_id == run.id).one_or_none()
    analytics = db.query(BusinessAnalysis).filter(BusinessAnalysis.run_id == run.id).one_or_none()
    traces = db.query(AgentTrace).filter(AgentTrace.run_id == run.id).order_by(AgentTrace.timestamp).all()
    session_summary = db.query(SessionSummary).filter(SessionSummary.run_id == run.id).one_or_none()
    events = db.query(ValidationEvent).filter(ValidationEvent.run_id == run.id).order_by(ValidationEvent.created_at).all()
    models = db.query(ModelRun).filter(ModelRun.run_id == run.id).order_by(ModelRun.created_at).all()
    # Confirmed roles are keyed by SOURCE, not by run - a person's answer
    # about what a column means survives re-ingest. A reader needs them
    # because they change what the analytics numbers are measuring.
    confirmed = (
        db.query(ConfirmedColumnRole)
        .filter(
            ConfirmedColumnRole.source_id == run.source_id,
            ~ConfirmedColumnRole.role.startswith("pii:"),  # privacy decisions are not semantic roles
        )
        .order_by(ConfirmedColumnRole.role)
        .all()
    )

    return DeckSources(
        run_number=run.run_number,
        run_id=run.id,
        run_status=run.status,
        source_label=_source_label(source),
        started_at=run.started_at,
        generation_mode=report.generation_mode if report else None,
        narrative_text=report.narrative_text if report else None,
        grounded_claims=list(report.grounded_claims_json or []) if report else [],
        chart_refs=list(report.chart_refs or []) if report else [],
        analytics=_analytics_payload(analytics),
        confirmed_roles=[
            {"role": c.role, "column_name": c.column_name, "confirmed_at": c.confirmed_at}
            for c in confirmed
        ],
        # The Session Summary as the summary agent persisted it - read, never
        # regenerated here, and never re-derived from the deck's own text.
        session_summary=(
            {
                "summary_text": session_summary.summary_text,
                "generation_mode": session_summary.generation_mode,
                "fallback_reason": session_summary.fallback_reason,
            }
            if session_summary is not None
            else None
        ),
        model_runs=[
            {
                "question": m.question,
                "target_column": m.target_column,
                "task_type": m.task_type,
                "model_family": m.model_family,
                "split_strategy": m.split_strategy,
                "row_count_trained_on": m.row_count_trained_on,
                "candidate_scores": m.candidate_scores_json,
                "out_of_sample_metric": m.out_of_sample_metric,
                "out_of_sample_score": m.out_of_sample_score,
                "baseline_scores": m.baseline_scores_json,
                "prediction_interval": m.prediction_interval_json,
                "excluded_features": m.excluded_features_json,
                "state": m.state,
                "escalation_reason": m.escalation_reason,
            }
            for m in models
        ],
        trace=[{"node": t.agent_name, "edge_taken": t.edge_taken} for t in traces],
        validation_events=[
            {
                "rule_failed": e.rule_failed,
                "state": e.state,
                "action_taken": e.action_taken,
                "reversal": e.reversal_json,
                "resolved_by": e.resolved_by,
            }
            for e in events
        ],
    )


def job_status(run_key: str) -> dict:
    with _LOCK:
        return dict(_JOBS.get(run_key) or {"state": "none"})


def _set(run_key: str, **fields) -> None:
    with _LOCK:
        _JOBS[run_key] = {**(_JOBS.get(run_key) or {}), **fields}


def start_export(db: Session, run: Run) -> dict:
    """Kicks generation off in a thread and returns immediately.

    Re-entrant: asking twice while one is running joins the run in progress
    rather than starting a second render of the same deck.
    """
    key = str(run.run_number or run.id)
    current = job_status(key)
    if current.get("state") == "running":
        return current

    sources = collect_sources(db, run)
    path = deck_path(run)
    _set(key, state="running", started_at=datetime.now(timezone.utc).isoformat(), path=str(path), error=None)

    def work() -> None:
        try:
            EXPORT_DIR.mkdir(parents=True, exist_ok=True)
            data = build_deck(sources)
            path.write_bytes(data)
            _set(key, state="ready", bytes=len(data), slides=None, error=None)
        except Exception as exc:  # noqa: BLE001 - reported as a failed job, never a 500 on the poll
            _set(key, state="failed", error=f"{type(exc).__name__}: {exc}")

    threading.Thread(target=work, daemon=True, name=f"deck-export-{key}").start()
    return job_status(key)


def _source_label(source: DataSource | None) -> str:
    if source is None:
        return "unknown source"
    config = source.connection_config or {}
    original = config.get("original_filename")
    if original:
        return str(original)
    path = config.get("path")
    if path:
        return str(path).replace("\\", "/").rsplit("/", 1)[-1]
    return source.type
