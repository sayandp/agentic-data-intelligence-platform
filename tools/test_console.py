"""Manual test console for the Agentic Data Intelligence Platform.

A Streamlit UI for poking the real running backend by hand - register/
upload/ingest a source, resolve approvals, inspect reports/audit, verify the
Gemini key is actually live (not just present in .env), and run the backend
pytest suite on demand. This talks to the real HTTP API exactly like the
React frontend does; it is a debugging/verification tool, not a replacement
for it.

Run (from the project root, with backend/.venv active or via its python):
    backend\\.venv\\Scripts\\python.exe -m streamlit run tools\\test_console.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import requests
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

st.set_page_config(page_title="Platform test console", layout="wide")

if "backend_url" not in st.session_state:
    st.session_state.backend_url = "http://127.0.0.1:8000"
if "pending" not in st.session_state:
    st.session_state.pending = None

with st.sidebar:
    st.header("Backend")
    st.session_state.backend_url = st.text_input("Backend URL", st.session_state.backend_url)
    st.caption("This console is a thin client over the real API - every action here is a real HTTP request, same as the React dashboard.")


def api_get(path: str, **kwargs):
    try:
        r = requests.get(f"{st.session_state.backend_url}{path}", timeout=30, **kwargs)
        return r
    except requests.RequestException as exc:
        st.error(f"GET {path} failed: {exc}")
        return None


def api_post(path: str, **kwargs):
    try:
        r = requests.post(f"{st.session_state.backend_url}{path}", timeout=120, **kwargs)
        return r
    except requests.RequestException as exc:
        st.error(f"POST {path} failed: {exc}")
        return None


def show_response(r: requests.Response | None, success_label: str = "Response"):
    if r is None:
        return
    if r.ok:
        st.success(f"{success_label}: {r.status_code}")
    else:
        st.error(f"{success_label}: {r.status_code}")
    try:
        st.json(r.json())
    except ValueError:
        st.code(r.text)


def _safe_json(r: requests.Response):
    try:
        return r.json()
    except ValueError:
        return r.text


def resolve_and_refresh(item_id: str, decision: str, resolved_by: str, label: str):
    """Every decision here mutates server state that the pending list was
    built from - re-fetching /approvals/pending and rerunning immediately
    after is what makes an already-resolved item disappear from the list
    instead of sitting there stale, clickable again, and 409-ing on the
    next click (exactly what a resolve-then-never-refresh console does).
    The result of THIS click still needs to render after that rerun wipes
    the current script pass, so it's stashed in session_state and flashed
    once at the top of the tab on the next run, then cleared."""
    r = api_post(f"/approvals/{item_id}/resolve", json={"decision": decision, "resolved_by": resolved_by})
    if r is not None:
        st.session_state.last_resolve = {"label": label, "status": r.status_code, "ok": r.ok, "body": _safe_json(r)}
    refreshed = api_get("/approvals/pending")
    if refreshed is not None and refreshed.ok:
        st.session_state.pending = refreshed.json()
    st.rerun()


st.title("Platform test console")

tab_status, tab_sources, tab_approvals, tab_reports, tab_tests = st.tabs(
    ["Status", "Sources & Ingest", "Approvals", "Reports & Audit", "Unit tests"]
)

# --- Status --------------------------------------------------------------
with tab_status:
    st.subheader("Backend reachability")
    if st.button("Check /health"):
        r = api_get("/health")
        show_response(r, "Health")

    st.subheader("Gemini key - live check")
    st.caption(
        "This does NOT just check that GEMINI_API_KEY is set in .env - it constructs the real "
        "GeminiClient this backend process would build and makes one real call against the live "
        "API, the same way app/main.py's own [startup] log line is derived."
    )
    if st.button("Verify Gemini key is actually working"):
        try:
            from pydantic import BaseModel

            from app.llm.factory import get_llm_client

            class _Probe(BaseModel):
                answer: str

            with st.spinner("Constructing client and calling the live Gemini API..."):
                client = get_llm_client()
                result = client.complete(
                    "You are a terse test responder.", "Reply with answer=OK", _Probe
                )
            st.success(f"LLM provider active: {type(client).__name__} (model={client.model_name})")
            st.json({"answer": result.answer})
        except Exception as exc:  # noqa: BLE001 - surfacing the real failure to the tester
            st.error(f"No working LLM client: {exc}")
            st.info(
                "This is not necessarily a bug - no-LLM mode is a fully supported operating mode "
                "(every corruption escalates to human review, reports use the deterministic "
                "template). This just tells you which mode you're actually in."
            )

# --- Sources & Ingest ------------------------------------------------------
with tab_sources:
    st.subheader("Register a source by local path")
    path = st.text_input("Absolute path to a CSV/Excel file on this machine", key="reg_path")
    if st.button("Register") and path:
        r = api_post("/sources", json={"type": "file", "connection_config": {"path": path}})
        show_response(r, "Register")

    st.subheader("Or upload a file")
    uploaded = st.file_uploader("CSV or Excel file", type=["csv", "xlsx", "xls"])
    if uploaded is not None and st.button("Upload"):
        r = api_post("/sources/upload", files={"file": (uploaded.name, uploaded.getvalue())})
        show_response(r, "Upload")

    st.subheader("All sources")
    if st.button("List sources"):
        r = api_get("/sources")
        if r is not None and r.ok:
            sources = r.json()
            st.dataframe(sources, width="stretch")
        else:
            show_response(r, "List sources")

    st.subheader("Ingest a source")
    source_id = st.text_input("Source id", key="ingest_id")
    if st.button("Ingest") and source_id:
        with st.spinner("Ingesting - this runs the full validate/diagnose/gate pipeline..."):
            r = api_post(f"/ingest/{source_id}")
        show_response(r, "Ingest")

# --- Approvals ---------------------------------------------------------
with tab_approvals:
    if st.button("Refresh pending approvals"):
        r = api_get("/approvals/pending")
        if r is not None and r.ok:
            st.session_state.pending = r.json()
        else:
            show_response(r, "Pending approvals")

    pending = st.session_state.pending
    if pending is None:
        st.info("Click 'Refresh pending approvals' to load.")
    else:
        if st.session_state.get("last_resolve"):
            info = st.session_state.pop("last_resolve")
            (st.success if info["ok"] else st.error)(f"{info['label']}: {info['status']}")
            st.json(info["body"])

        resolved_by = st.text_input("Resolved by", "test-console")

        st.markdown("### Provisional baselines")
        if not pending["provisional_baselines"]:
            st.caption("None pending.")
        for b in pending["provisional_baselines"]:
            with st.expander(f"Baseline {b['id'][:8]} - source {b['source_id'][:8]} - {b['row_count']} rows"):
                c1, c2 = st.columns(2)
                if c1.button("Confirm", key=f"base_ok_{b['id']}"):
                    resolve_and_refresh(b["id"], "approve", resolved_by, "Confirm baseline")
                if c2.button("Reject", key=f"base_no_{b['id']}"):
                    resolve_and_refresh(b["id"], "reject_data", resolved_by, "Reject baseline")

        st.markdown("### Escalated validation events")
        if not pending["validation_events"]:
            st.caption("None pending.")
        for g in pending["validation_events"]:
            title = f"Run {g['run_id'][:8]} - {', '.join(g['rules_failed'])}"
            with st.expander(title):
                st.json(g["diagnosis"])
                st.caption("Gate reasons: " + "; ".join(g["gate_reasons"] or []))

                # Mirrors ApprovalsPage.tsx's hasApprovableAction exactly:
                # a diagnosis whose suggested_fix.action is "escalate" (or
                # missing, or an error record) has no automated fix at all -
                # the applicability matrix backing this rule permits none,
                # so POST .../resolve with decision=approve always 422s for
                # it. Rendering Approve as clickable there gives the same
                # false affordance the React dashboard deliberately avoids.
                diagnosis = g["diagnosis"] or {}
                suggested_action = (diagnosis.get("suggested_fix") or {}).get("action")
                has_approvable_action = (
                    isinstance(suggested_action, str) and suggested_action != "escalate" and not diagnosis.get("error")
                )

                c1, c2, c3, c4 = st.columns(4)
                if c1.button("Approve", key=f"ev_approve_{g['resolve_id']}", disabled=not has_approvable_action):
                    resolve_and_refresh(g["resolve_id"], "approve", resolved_by, "Approve")
                if c2.button("Keep data as-is", key=f"ev_keep_{g['resolve_id']}"):
                    resolve_and_refresh(g["resolve_id"], "reject_fix", resolved_by, "Reject fix")
                if c3.button("Accept as baseline", key=f"ev_base_{g['resolve_id']}"):
                    resolve_and_refresh(g["resolve_id"], "accept_as_baseline", resolved_by, "Accept as baseline")
                if c4.button("Discard run", key=f"ev_discard_{g['resolve_id']}"):
                    resolve_and_refresh(g["resolve_id"], "reject_data", resolved_by, "Discard run")
                if not has_approvable_action:
                    st.caption("Approve is disabled - the diagnosis has no matrix-permitted fix to apply for this group.")

        st.markdown("### Escalated queries")
        if not pending["escalated_queries"]:
            st.caption("None pending.")
        for q in pending["escalated_queries"]:
            with st.expander(f"{q['question']} - {q['escalation_reason']}"):
                st.code(q["code"] or "(no code generated)")
                c1, c2 = st.columns(2)
                if q["approvable"] and c1.button("Approve & run", key=f"q_ok_{q['id']}"):
                    resolve_and_refresh(q["id"], "approve", resolved_by, "Approve query")
                if c2.button("Dismiss", key=f"q_no_{q['id']}"):
                    resolve_and_refresh(q["id"], "reject_fix", resolved_by, "Dismiss query")

        st.markdown("### Escalated models")
        if not pending["escalated_models"]:
            st.caption("None pending.")
        for m in pending["escalated_models"]:
            with st.expander(f"{m['target_column']} ({m['task_type']}) - {m['escalation_reason']}"):
                c1, c2 = st.columns(2)
                if m["approvable"] and c1.button("Approve despite caveat", key=f"m_ok_{m['id']}"):
                    resolve_and_refresh(m["id"], "approve", resolved_by, "Approve model")
                if c2.button("Dismiss", key=f"m_no_{m['id']}"):
                    resolve_and_refresh(m["id"], "reject_fix", resolved_by, "Dismiss model")

        st.markdown("### Connector warnings")
        if not pending["connector_warnings"]:
            st.caption("None pending.")
        for w in pending["connector_warnings"]:
            with st.expander(f"Run {w['run_id'][:8]} - {w['message']}"):
                if st.button("Acknowledge", key=f"w_ack_{w['id']}"):
                    resolve_and_refresh(w["id"], "acknowledge", resolved_by, "Acknowledge")

# --- Reports & Audit -----------------------------------------------------
with tab_reports:
    run_id = st.text_input("Run id")
    c1, c2 = st.columns(2)
    if c1.button("Get audit trace") and run_id:
        r = api_get(f"/audit/{run_id}")
        show_response(r, "Audit")
    if c2.button("Get report") and run_id:
        r = api_get(f"/reports/{run_id}")
        if r is not None and r.ok:
            body = r.json()
            if body.get("narrative_text"):
                st.markdown("**Narrative:**")
                st.write(body["narrative_text"])
            st.json(body)
        else:
            show_response(r, "Report")

# --- Unit tests ------------------------------------------------------------
with tab_tests:
    st.caption(
        "Runs the real backend pytest suite (backend/tests) as a subprocess, using this same "
        "venv's python. Takes roughly 1-2 minutes cold (langgraph/statsmodels/prophet imports) - "
        "this is normal, not a hang."
    )
    if st.button("Run backend test suite"):
        with st.spinner("Running pytest..."):
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-q"],
                cwd=str(BACKEND_DIR),
                capture_output=True,
                text=True,
            )
        summary_line = next(
            (line for line in reversed(result.stdout.splitlines()) if "passed" in line or "failed" in line or "error" in line),
            None,
        )
        if result.returncode == 0:
            st.success(summary_line or "All tests passed.")
        else:
            st.error(summary_line or f"pytest exited with code {result.returncode}")
        with st.expander("Full output"):
            st.code(result.stdout + "\n" + result.stderr)
