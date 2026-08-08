// Dashboard UX pass, Part 1: every "View report"/"View audit" link
// navigates with ?run=<value> - the SAME lookup a human typing into the
// Reports/Audit load box gets (app/id_lookup.py::resolve_run), which
// accepts a plain run_number ("17"/"#17") or the full UUID. Links always
// prefer the number - it's the primary, human-facing identifier now - and
// fall back to the full id only for the edge case of a run whose number
// hasn't been backfilled yet (should not happen once app/db.py's migration
// has run once).
export function runQueryParam(runId: string, runNumber: number | null): string {
  return String(runNumber ?? runId);
}

export function reportLink(runId: string, runNumber: number | null): string {
  return `/reports?run=${runQueryParam(runId, runNumber)}`;
}

export function auditLink(runId: string, runNumber: number | null): string {
  return `/audit?run=${runQueryParam(runId, runNumber)}`;
}

// Same ?run= contract as report/audit above, so a completed run can be
// carried straight into Ask/Predict with nothing to copy or retype - the
// only reason those two screens ever asked for a pasted identifier.
export function askLink(runId: string, runNumber: number | null): string {
  return `/ask?run=${runQueryParam(runId, runNumber)}`;
}

export function predictLink(runId: string, runNumber: number | null): string {
  return `/predict?run=${runQueryParam(runId, runNumber)}`;
}

export function analyticsLink(runId: string, runNumber: number | null): string {
  return `/analytics?run=${runQueryParam(runId, runNumber)}`;
}
