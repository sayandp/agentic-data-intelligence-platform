"""Part 5: POST /ask - the Query Agent's HTTP surface.

Body is {source_id or run_id, question}: run_id pins the exact repaired
frame/quality context to answer against; a bare source_id resolves to that
source's latest completed run (app/query/pipeline.py::resolve_run). The
generated code is always returned, even on escalation - "the user must be
able to see what would have run" (Part 5) - and the response never raises a
500 for an LLM/validation/execution failure; every one of those becomes a
clean escalated QueryAnswer instead. A 404 here means only "no such
source/run to even ask against", never anything about how the question
itself resolved.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, model_validator
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import QueryRun
from app.query.agent import QueryAgent
from app.query.dependency import get_query_agent
from app.query.models import QueryAnswerStatus
from app.query.pipeline import ask_question

router = APIRouter(tags=["query"])


class AskRequest(BaseModel):
    source_id: str | None = None
    run_id: str | None = None
    question: str

    @model_validator(mode="after")
    def _require_one_target(self) -> "AskRequest":
        if not self.source_id and not self.run_id:
            raise ValueError("either source_id or run_id is required")
        return self


def _serialize(record: QueryRun) -> dict:
    return {
        "id": record.id,
        "status": QueryAnswerStatus.ANSWERED.value if record.state == "answered" else QueryAnswerStatus.ESCALATED.value,
        "question": record.question,
        "quality_context_summary": record.quality_context_summary,
        "query_kind": record.query_kind,
        "code": record.generated_code,
        "result": record.result_json,
        "truncated": record.truncated,
        "row_count": record.row_count,
        "columns_referenced": record.columns_referenced_json or [],
        "assumptions": record.assumptions_json or [],
        "confidence": record.confidence,
        "escalation_reason": record.escalation_reason,
        "escalation_detail": record.escalation_detail,
        "state": record.state,
    }


@router.post("/ask")
def ask(
    payload: AskRequest,
    db: Session = Depends(get_db),
    query_agent: QueryAgent | None = Depends(get_query_agent),
):
    try:
        record = ask_question(db, query_agent, source_id=payload.source_id, run_id=payload.run_id, question=payload.question)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _serialize(record)
