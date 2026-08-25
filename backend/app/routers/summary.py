"""GET /summary/{run_id} - the Session Summary for a run.

A pure read. The summary is generated once, by the `summarise` graph node
after narrate, and persisted - never generated inside this request. An LLM
call in a request handler is the bug POST /ingest and POST /approvals both
already fixed.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.id_lookup import resolve_run
from app.models import SessionSummary
from app.summary.pipeline import rendered_summary

router = APIRouter(prefix="/summary", tags=["summary"])


@router.get("/{run_id}")
def get_summary(run_id: str, db: Session = Depends(get_db)):
    run = resolve_run(db, run_id)
    record = db.query(SessionSummary).filter(SessionSummary.run_id == run.id).one_or_none()
    if record is None:
        # 200, not 404. The run EXISTS - it simply has no summary yet, or
        # finished before this agent did. A 404 here is indistinguishable from
        # a broken request at the network layer: the run view fetches this for
        # every report it opens, and an expected-absence 404 logs a browser
        # console error on a page that is working correctly. An unknown RUN is
        # still a 404, raised by resolve_run above.
        return {
            "run_id": run.id,
            "run_number": run.run_number,
            "available": False,
            "reason": (
                f"run {run.run_number or run.id} has no session summary - it is written once, "
                "when a run completes, so a run that finished before this agent existed has none"
            ),
        }

    return {
        "run_id": run.id,
        "run_number": run.run_number,
        "available": True,
        # Quality context is a separate field AND is first in `rendered` -
        # a consumer that renders only `summary_text` still has to go out of
        # its way to drop the caveat.
        "quality_context": record.quality_context,
        "summary_text": record.summary_text,
        "rendered": rendered_summary(record),
        "generation_mode": record.generation_mode,
        "fallback_reason": record.fallback_reason,
        "claims": record.claims_json,
        "facts": record.facts_json,
        "post_check_results": record.post_check_results,
        "generated_at": record.generated_at.isoformat(),
    }
