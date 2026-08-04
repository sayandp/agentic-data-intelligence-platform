import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, apiFetch } from "../api/client";
import type { IngestResponse, RunSummaryRecord } from "../api/types";

// Shared by Ask and Predict so the two can never drift apart again - the
// original defect was exactly that kind of drift: Reports/Audit accepted a
// run NUMBER while Ask/Predict silently accepted only a UUID, so a run that
// plainly existed reported "run '48' not found". Selecting a run is now the
// primary interaction on both screens; typing is a fallback, never a
// requirement, and a UUID is never required anywhere.
//
// Neither screen asks for a source any more: a run already knows its source
// (POST /ask and POST /predict both take source_id OR run_id and ignore
// source_id entirely when run_id is present - see
// app/query/pipeline.py::resolve_run), so asking for both invited exactly
// the contradictory input that produced the bug report.

function formatWhen(iso: string | null): string {
  if (!iso) return "";
  // The API serialises naive UTC datetimes ("2026-08-04T07:15:00", no
  // trailing Z and no offset). JS parses a bare date-TIME form as LOCAL
  // time, which silently shifts every run by the viewer's UTC offset - a
  // run completed seconds ago renders as "6h ago" in UTC+5:30. Mark it as
  // UTC explicitly unless the server already said otherwise.
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(iso);
  const then = new Date(hasZone ? iso : `${iso}Z`).getTime();
  if (Number.isNaN(then)) return "";
  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export function runOptionLabel(run: RunSummaryRecord): string {
  const number = run.run_number != null ? `Run #${run.run_number}` : `Run ${run.id.slice(0, 8)}`;
  const when = formatWhen(run.completed_at ?? run.started_at);
  return when ? `${number} - ${run.source_label} - completed ${when}` : `${number} - ${run.source_label}`;
}

// The value carried in ?run= and posted as run_id: the run NUMBER when it
// has one (the human-facing identifier everywhere else in this app), the
// UUID only for a run predating the run_number backfill.
export function runRefOf(run: RunSummaryRecord): string {
  return String(run.run_number ?? run.id);
}

function matches(run: RunSummaryRecord, ref: string): boolean {
  const candidate = ref.trim().replace(/^#/, "");
  return run.id === candidate || (run.run_number != null && String(run.run_number) === candidate);
}

// Placeholder/example questions built from the SELECTED RUN'S OWN COLUMNS -
// a generic "What is the average order amount?" invites a question about a
// column that isn't there, which then escalates for a reason that reads
// like a system failure rather than a typo.
// Row-identity columns make terrible examples: averaging a rank or
// grouping by a per-row name is a question nobody asks. Cardinality isn't
// available client-side (the run list is deliberately cheap), so this is a
// name-shape heuristic - it only reorders SUGGESTIONS, every column is
// still listed and still askable.
const IDENTIFIER_LIKE = /^(rk|rank|no|num|idx|index|id|key|uuid|guid)$/i;
const IDENTIFIER_SUFFIX = /(_id|id|_key|name|code)$/i;

function preferDescriptive(columns: string[]): string[] {
  const descriptive = columns.filter((c) => !IDENTIFIER_LIKE.test(c) && !IDENTIFIER_SUFFIX.test(c));
  return descriptive.length > 0 ? descriptive : columns;
}

export function exampleQuestions(columnTypes: Record<string, string> | null, mode: "ask" | "predict"): string[] {
  if (!columnTypes) return [];
  const entries = Object.entries(columnTypes);
  const numeric = preferDescriptive(entries.filter(([, t]) => /int|float|decimal|number/i.test(t)).map(([c]) => c));
  const categorical = preferDescriptive(entries.filter(([, t]) => /str|object|category|bool/i.test(t)).map(([c]) => c));
  const temporal = entries.filter(([, t]) => /date|time/i.test(t)).map(([c]) => c);

  if (mode === "predict") {
    const target = numeric[0];
    if (!target) return [];
    return temporal.length > 0 ? [`forecast monthly ${target}`, `forecast ${target} over ${temporal[0]}`] : [`predict ${target}`];
  }

  const examples: string[] = [];
  if (numeric[0]) examples.push(`what is the average ${numeric[0]}?`);
  if (numeric[0] && categorical[0]) examples.push(`what were total ${numeric[0]} by ${categorical[0]}?`);
  if (examples.length === 0 && categorical[0]) examples.push(`how many rows per ${categorical[0]}?`);
  return examples;
}

export interface RunSelection {
  runs: RunSummaryRecord[] | null;
  runsError: unknown;
  /** Run number (preferred) or UUID. Posted verbatim as run_id. */
  runRef: string;
  setRunRef: (ref: string) => void;
  selectedRun: RunSummaryRecord | undefined;
  columnTypes: Record<string, string> | null;
  columnsLoading: boolean;
  reload: () => void;
}

export function useRunSelection(initialRunRef: string): RunSelection {
  const [runs, setRuns] = useState<RunSummaryRecord[] | null>(null);
  const [runsError, setRunsError] = useState<unknown>(null);
  const [runRef, setRunRef] = useState(initialRunRef);
  const [columnTypes, setColumnTypes] = useState<Record<string, string> | null>(null);
  const [columnsLoading, setColumnsLoading] = useState(false);

  const reload = useCallback(() => {
    setRunsError(null);
    apiFetch<RunSummaryRecord[]>("/runs?status=completed&limit=50")
      .then((list) => {
        setRuns(list);
        // Nothing prefilled (no ?run= and nothing typed) - default to the
        // newest completed run so the page is immediately usable rather
        // than presenting an empty required field.
        setRunRef((current) => (current || list.length === 0 ? current : runRefOf(list[0])));
      })
      .catch(setRunsError);
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  const selectedRun = useMemo(() => runs?.find((r) => matches(r, runRef)), [runs, runRef]);

  // Columns come from GET /ingest/{run}/status for the ONE selected run.
  // That endpoint rebuilds the repaired frame, so it is deliberately not
  // done for every row of the list (see app/routers/runs.py's docstring).
  useEffect(() => {
    const ref = runRef.trim();
    if (!ref) {
      setColumnTypes(null);
      return;
    }
    let cancelled = false;
    setColumnsLoading(true);
    apiFetch<IngestResponse>(`/ingest/${encodeURIComponent(ref)}/status`)
      .then((body) => {
        if (cancelled) return;
        const types = (body.metadata?.column_types ?? null) as Record<string, string> | null;
        setColumnTypes(types);
      })
      .catch(() => {
        // A run that can't be described is still a run the user may submit
        // (the backend is the authority on whether it resolves) - this only
        // costs the column hints, so it never becomes a blocking error.
        if (!cancelled) setColumnTypes(null);
      })
      .finally(() => {
        if (!cancelled) setColumnsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [runRef]);

  return { runs, runsError, runRef, setRunRef, selectedRun, columnTypes, columnsLoading, reload };
}

export function RunPicker({ selection, idPrefix }: { selection: RunSelection; idPrefix: string }) {
  const { runs, runRef, setRunRef, selectedRun, columnTypes, columnsLoading } = selection;
  const selectId = `${idPrefix}-run-select`;
  const typedId = `${idPrefix}-run-typed`;
  const columns = columnTypes ? Object.keys(columnTypes) : [];

  return (
    <div className="flex flex-col gap-2">
      <label htmlFor={selectId} className="text-sm font-medium text-ink-muted">
        Run
      </label>
      <div className="flex flex-wrap items-center gap-3">
        <select
          id={selectId}
          value={selectedRun ? runRefOf(selectedRun) : ""}
          onChange={(e) => setRunRef(e.target.value)}
          className="min-w-0 flex-1 rounded-sm border border-border-strong bg-white px-2 py-1.5 text-sm"
        >
          {runs === null && <option value="">Loading runs...</option>}
          {runs !== null && runs.length === 0 && <option value="">No completed runs yet - ingest a source first</option>}
          {runs !== null && runs.length > 0 && !selectedRun && <option value="">Select a run...</option>}
          {(runs ?? []).map((run) => (
            <option key={run.id} value={runRefOf(run)}>
              {runOptionLabel(run)}
            </option>
          ))}
        </select>
        <span className="text-xs text-ink-faint">or</span>
        <input
          id={typedId}
          aria-label="Run number"
          value={runRef}
          onChange={(e) => setRunRef(e.target.value)}
          placeholder="run number, e.g. 48"
          className="w-44 rounded-sm border border-border-strong px-3 py-1.5 text-sm"
        />
      </div>
      {selectedRun && (
        <p className="text-xs text-ink-faint">
          {selectedRun.source_label} &middot; <span className="font-mono">{selectedRun.id}</span>
        </p>
      )}
      {/* Part 3: the run's actual columns, so a question never has to guess
          at a column that isn't in this data. */}
      {columnsLoading && <p className="text-xs text-ink-faint">Loading columns...</p>}
      {!columnsLoading && columns.length > 0 && (
        <p className="text-xs text-ink-muted">
          <span className="font-medium">Columns:</span>{" "}
          {columns.map((c, i) => (
            <span key={c}>
              {i > 0 && ", "}
              <span className="font-mono text-ink">{c}</span>
            </span>
          ))}
        </p>
      )}
    </div>
  );
}

// A 404 from /ask or /predict means only "no such run" - the one thing the
// user can act on is which runs DO exist, so this replaces a dead red box
// with the recent runs, clickable.
export function RunNotFoundHelp({ error, selection }: { error: unknown; selection: RunSelection }) {
  const isRunNotFound = error instanceof ApiError && error.status === 404;
  if (!isRunNotFound) return null;
  const recent = (selection.runs ?? []).slice(0, 8);
  if (recent.length === 0) return null;

  return (
    <p className="mt-2 text-sm text-ink">
      Recent runs:{" "}
      {recent.map((run, i) => (
        <span key={run.id}>
          {i > 0 && ", "}
          <button
            type="button"
            onClick={() => selection.setRunRef(runRefOf(run))}
            className="font-medium text-brand-600 hover:underline"
          >
            {run.run_number != null ? `#${run.run_number}` : run.id.slice(0, 8)}
          </button>
        </span>
      ))}
      <span className="text-ink-muted">
        {" "}
        &middot; or see all on <Link className="text-brand-600 hover:underline" to="/sources">Sources</Link>
      </span>
    </p>
  );
}
