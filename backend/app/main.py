from __future__ import annotations

import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()  # GEMINI_API_KEY, GEMINI_MODEL, LLM_PROVIDER, etc. - see .env.example

from app.db import init_db  # noqa: E402
from app.routers import analytics, approvals, audit, baselines, export, findings, ingest, marketing, predict, query, reports, runs, sources  # noqa: E402


def _log_llm_mode() -> None:
    """Prints the operating mode plainly at boot, rather than leaving it to
    be inferred from behaviour three requests later (a corruption that
    escalates instead of auto-fixing, a report that comes back
    generation_mode="template"). Every agent's own dependency
    (get_diagnostic_agent/get_narrative_agent/get_query_agent/
    get_modeling_agent) already degrades independently and identically on
    construction failure - this just surfaces the ONE underlying reason
    (get_llm_client()) once, at startup, instead of four times, silently,
    per request."""
    from app.llm.factory import get_llm_client

    try:
        client = get_llm_client()
    except Exception as exc:  # noqa: BLE001 - reporting only; every caller of get_llm_client() already handles this itself
        print(
            f"[startup] No LLM configured ({exc}). Running in NO-LLM mode: every corruption escalates to human "
            "review, reports are template-generated, and questions/predictions that need generation are refused - "
            "not a degraded state, the platform's designed fallback when no LLM is reachable."
        )
        return
    print(f"[startup] LLM provider active: {type(client).__name__} (model={client.model_name})")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _log_llm_mode()
    yield


app = FastAPI(title="Agentic Data Intelligence Platform", version="0.1.0", lifespan=lifespan)

# The dashboard is now a separate frontend (frontend/), served from its own
# origin/port - this API has no server-rendered UI of its own anymore, so it
# needs to accept cross-origin requests from wherever that frontend runs.
# FRONTEND_ORIGINS is a comma-separated list; defaults cover the
# docker-compose frontend service's published port, plus Vite's dev server
# AND its next few fallback ports - Vite silently picks 5174/5175/... with
# no error if 5173 is already taken (by a leftover process from a previous
# run, another project, anything), and a request from whichever port it
# actually landed on is otherwise blocked identically to the backend simply
# being unreachable - a genuinely confusing failure mode to debug blind.
_DEFAULT_FRONTEND_ORIGINS = ",".join(
    ["http://localhost:3000", *[f"http://localhost:{5173 + i}" for i in range(5)]]
)
_frontend_origins = [o.strip() for o in os.environ.get("FRONTEND_ORIGINS", _DEFAULT_FRONTEND_ORIGINS).split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_frontend_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sources.router)
app.include_router(baselines.router)
app.include_router(approvals.router)
app.include_router(ingest.router)
app.include_router(findings.router)
app.include_router(reports.router)
app.include_router(query.router)
app.include_router(predict.router)
app.include_router(audit.router)
app.include_router(runs.router)
app.include_router(export.router)
app.include_router(analytics.router)
app.include_router(marketing.router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def root():
    return {"service": "Agentic Data Intelligence Platform API", "docs": "/docs", "health": "/health"}
