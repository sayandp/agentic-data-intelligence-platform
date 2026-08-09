// Every function in this file is a thin wrapper over the backend's existing,
// already-tested JSON API - this app has no business logic of its own, only
// presentation. Nothing here reimplements what the backend already decides.

import type { IngestResponse, PredictResult } from "./types";

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  // The raw `detail` field from the backend's error body, before it was
  // stringified into `message` - most callers only need `message`, but a
  // structured detail (e.g. GET /reports|/audit's 409 ambiguous-prefix body,
  // {message, candidates: string[]}) needs this to build a real picker
  // instead of re-parsing JSON back out of a message string.
  detail: unknown;
  constructor(message: string, status: number, detail?: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

// fetch() rejects with a bare "Failed to fetch"/"NetworkError" for every
// reason it never got an HTTP response at all - server not running, CORS
// blocked, DNS/firewall, offline. That message alone gives no way to tell
// those apart. This wraps every such rejection with the one fact the
// browser's own error never includes: WHERE it tried to connect - which is
// often enough on its own to tell "backend isn't running" from "pointed at
// the wrong place".
export class NetworkError extends Error {
  constructor(cause: unknown) {
    const causeMessage = cause instanceof Error ? cause.message : String(cause);
    super(
      `Could not reach the backend at ${API_BASE_URL} (${causeMessage}). ` +
        "Check that it's running, and that this page's origin is allowed by the backend's FRONTEND_ORIGINS."
    );
  }
}

async function rawFetch(path: string, options?: RequestInit): Promise<Response> {
  try {
    return await fetch(`${API_BASE_URL}${path}`, options);
  } catch (err) {
    throw new NetworkError(err);
  }
}

export async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const resp = await rawFetch(path, options);
  let body: unknown = null;
  try {
    body = await resp.json();
  } catch {
    // no JSON body (e.g. a plain-text 500) - handled below
  }
  if (!resp.ok) {
    const detail =
      body && typeof body === "object" && "detail" in body
        ? (body as { detail: unknown }).detail
        : resp.statusText;
    const message =
      typeof detail === "string" ? detail : (detail && typeof detail === "object" && "message" in detail ? String((detail as { message: unknown }).message) : JSON.stringify(detail));
    throw new ApiError(message, resp.status, detail);
  }
  return body as T;
}

export function apiPostJson<T>(path: string, payload: unknown): Promise<T> {
  return apiFetch<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

// POST /ingest returns {run_id, status: "running"} immediately and runs
// the graph in the background (see backend/app/routers/ingest.py's module
// docstring) - this is the fix for a real bug where the browser's fetch()
// held the connection open for however long narrate_node's LLM calls took
// (measured: 0.3s to 60s+ to what looked like an indefinite hang), because
// fetch() has no default timeout and the UI had no way to distinguish a
// slow LLM retry from a dead request. This polls GET /ingest/{run_id}/status
// until the run leaves "running", surfacing elapsed time via onTick so the
// caller can show real progress instead of a static, indistinguishable
// spinner - and bounds the wait so a genuinely stuck run eventually
// surfaces as an error instead of polling forever.
const INGEST_POLL_INTERVAL_MS = 1000;
const INGEST_POLL_TIMEOUT_MS = 5 * 60 * 1000; // generous: up to ~8 chained LLM calls with backoff is a real, valid duration, not a hang
// Separate, SHORT, bounded grace period for report generation specifically
// (see the stillGeneratingReport comment below) - a run whose narrate_node
// itself failed/degraded stays "completed" with report.available=false
// FOREVER, indistinguishable from "hasn't gotten there yet" by this
// response shape alone. A short, separate grace period lets the common
// case (report ready in a few seconds) resolve fast, without making a
// permanently-missing report burn the entire 5-minute outer timeout.
const REPORT_GRACE_PERIOD_MS = 20 * 1000;

// Shared polling core - the RESOLVE-HANG-style fix's frontend half, reused
// (never forked) by every backend endpoint that returns an immediate ack
// and defers the real work to a BackgroundTask: POST /ingest, POST
// /approvals/{id}/resolve, and POST /predict all share this exact loop
// shape (fetch once, check whether the result is terminal yet, otherwise
// report progress and wait) - only WHAT to fetch and WHAT COUNTS AS
// TERMINAL differ per caller.
async function pollUntil<T>(
  fetchOnce: () => Promise<T>,
  isTerminal: (result: T) => boolean,
  opts: {
    onTick?: (elapsedMs: number) => void;
    timeoutMs: number;
    intervalMs: number;
    timeoutMessage: (elapsedSeconds: number) => string;
  }
): Promise<T> {
  const start = Date.now();
  while (true) {
    const result = await fetchOnce();
    if (isTerminal(result)) return result;

    const elapsedMs = Date.now() - start;
    if (elapsedMs > opts.timeoutMs) {
      throw new Error(opts.timeoutMessage(Math.round(elapsedMs / 1000)));
    }
    opts.onTick?.(elapsedMs);
    await new Promise((resolve) => setTimeout(resolve, opts.intervalMs));
  }
}

// resolveId is set by callers polling a validation_event resolve's outcome
// (ApprovalsPage.tsx) rather than a fresh ingest's (SourcesPage.tsx) - same
// endpoint, same polling loop, same progress/timeout guarantees; see
// app/routers/approvals.py's RESOLVE-HANG FIX docstring for why resolving
// an escalation needed the identical treatment a fresh ingest did. With
// resolveId set, this returns once body.resolution is populated (the
// caller reads response.resolution, not the top-level run fields) instead
// of waiting on the run's own status.
export function pollIngestStatus(runId: string, onTick?: (elapsedMs: number) => void, resolveId?: string): Promise<IngestResponse> {
  let completedSeenAt: number | null = null;
  return pollUntil<IngestResponse>(
    () => apiFetch<IngestResponse>(resolveId ? `/ingest/${runId}/status?resolve_id=${encodeURIComponent(resolveId)}` : `/ingest/${runId}/status`),
    (body) => {
      if (resolveId) return body.resolution != null;
      // The run is marked "completed" before narrate_node (the report LLM
      // call) runs - the pre-fix synchronous contract guaranteed the report
      // was ready by the time a caller saw "completed", so this keeps
      // polling a little longer rather than telling the UI "done" while
      // GET /reports/{runId} would still 404.
      const stillGeneratingReport = body.status === "completed" && body.report?.available === false;
      if (stillGeneratingReport) {
        completedSeenAt ??= Date.now();
        return Date.now() - completedSeenAt > REPORT_GRACE_PERIOD_MS; // narrate_node degraded/failed - report will never come
      }
      return body.status !== "running";
    },
    {
      onTick,
      timeoutMs: INGEST_POLL_TIMEOUT_MS,
      intervalMs: INGEST_POLL_INTERVAL_MS,
      timeoutMessage: (s) =>
        `${resolveId ? "Resolving" : "Ingest is"} still running after ${s}s - this may indicate a stuck run. Check GET /audit/${runId} for details.`,
    }
  );
}

// A run is marked "completed" by explore_node BEFORE narrate_node writes
// the report, so there is a real window where GET /reports/{run} 404s on a
// run whose status already says completed. Anyone following a "View
// report" link straight after an ingest lands in it and, before this, was
// shown a red error that read as terminal.
//
// Polls the SAME status endpoint pollIngestStatus does, with the same
// grace period, so the two agree on when a report is never coming rather
// than each deciding separately.
export function pollReportReady(runRef: string, onTick?: (elapsedMs: number) => void): Promise<IngestResponse> {
  let completedSeenAt: number | null = null;
  return pollUntil<IngestResponse>(
    () => apiFetch<IngestResponse>(`/ingest/${runRef}/status`),
    (body) => {
      if (body.report?.available) return true;
      // Only a COMPLETED run is worth waiting on. A paused or failed run
      // will never produce a report, and spinning on one would replace a
      // wrong message with a wrong wait.
      if (body.status !== "completed") return true;
      completedSeenAt ??= Date.now();
      return Date.now() - completedSeenAt > REPORT_GRACE_PERIOD_MS;
    },
    {
      onTick,
      timeoutMs: REPORT_GRACE_PERIOD_MS,
      intervalMs: INGEST_POLL_INTERVAL_MS,
      timeoutMessage: (seconds) =>
        `The report for run ${runRef} has not appeared after ${seconds}s. The Audit view shows whether the narrative step ran and what it returned.`,
    }
  );
}

// Predict page UX pass, Part 1: POST /predict returns {id, state: "running"}
// immediately (app/routers/predict.py's RESOLVE-HANG-style fix - CV/
// training across several candidate families is genuinely slow) - this
// polls GET /models/{id} (already existed, for direct lookup; reused as
// the poll target rather than a second endpoint, the same choice ingest's
// own fix made) until it leaves "running".
const PREDICT_POLL_TIMEOUT_MS = 5 * 60 * 1000;

export function pollPredictStatus(modelId: string, onTick?: (elapsedMs: number) => void): Promise<PredictResult> {
  return pollUntil<PredictResult>(
    () => apiFetch<PredictResult>(`/models/${modelId}`),
    (body) => body.state !== "running",
    {
      onTick,
      timeoutMs: PREDICT_POLL_TIMEOUT_MS,
      intervalMs: INGEST_POLL_INTERVAL_MS,
      timeoutMessage: (s) => `Predicting is still running after ${s}s - this may indicate a stuck run. Check GET /models/${modelId} directly.`,
    }
  );
}

// For multipart uploads - deliberately NOT apiPostJson, since a
// Content-Type header set by hand here would strip the multipart boundary
// the browser generates automatically for FormData.
export async function apiPostForm<T>(path: string, formData: FormData): Promise<T> {
  const resp = await rawFetch(path, { method: "POST", body: formData });
  let body: unknown = null;
  try {
    body = await resp.json();
  } catch {
    // no JSON body
  }
  if (!resp.ok) {
    const detail =
      body && typeof body === "object" && "detail" in body
        ? (body as { detail: unknown }).detail
        : resp.statusText;
    throw new ApiError(typeof detail === "string" ? detail : JSON.stringify(detail), resp.status);
  }
  return body as T;
}
