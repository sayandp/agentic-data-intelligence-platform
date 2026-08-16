import { useState } from "react";

import { apiFetch, apiPostForm, pollIngestStatus } from "../api/client";
import type { IngestResponse } from "../api/types";
import { Button, ErrorMessage, Muted } from "./ui";

// ONE upload-and-ingest implementation, used by the Sources page and the
// Marketing page.
//
// Both pages hit the SAME endpoints - POST /sources/upload then POST
// /ingest/{source_id} - and the same graph run. This component exists so
// that stays true: a second copy of the request, polling and progress logic
// is how two entry points start to disagree, which is the failure this
// codebase has already hit twice (two resolve_run functions, client-side vs
// backend chart derivation).
//
// Page-specific COPY is a prop. Page-specific BEHAVIOUR is not.

export interface IngestProgress {
  runId: string;
  runNumber: number | null;
  elapsedSeconds: number;
}

/**
 * POST /ingest/{source_id}, then poll until the run leaves "running".
 *
 * Returns immediately with `{run_id, status: "running"}` and polls from
 * there, because the graph runs as a background task: holding one fetch()
 * open for however long a narrative LLM call takes is the bug this shape
 * was introduced to fix. `onProgress` receives elapsed time on every tick so
 * a slow call reads as "working" rather than as a dead request - never an
 * unbounded spinner.
 */
export async function runIngest(
  sourceId: string,
  onProgress?: (progress: IngestProgress) => void
): Promise<IngestResponse> {
  const start = await apiFetch<{ run_id: string; run_number: number | null; status: string }>(
    `/ingest/${sourceId}`,
    { method: "POST" }
  );
  return pollIngestStatus(start.run_id, (elapsedMs) =>
    onProgress?.({
      runId: start.run_id,
      runNumber: start.run_number,
      elapsedSeconds: Math.round(elapsedMs / 1000),
    })
  );
}

export function runLabelOf(runNumber: number | null, runId: string): string {
  return runNumber != null ? `Run #${runNumber}` : `Run ${runId.slice(0, 8)}`;
}

export interface UploadIngestPanelProps {
  /** Page-specific copy. The request logic behind it is identical. */
  description: React.ReactNode;
  submitLabel?: string;
  /** Marketing uploads a file in order to look at it, so it ingests straight
   *  away. Sources registers many sources and ingests them per row, so it
   *  does not. */
  autoIngest?: boolean;
  /** Called after the source row is created, whichever mode. */
  onUploaded?: (sourceId: string) => void;
  /** Called after the ingest run settles. Only fires when autoIngest. */
  onIngested?: (result: IngestResponse) => void;
  idPrefix: string;
}

export function UploadIngestPanel({
  description,
  submitLabel = "Upload",
  autoIngest = false,
  onUploaded,
  onIngested,
  idPrefix,
}: UploadIngestPanelProps) {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  const inputId = `${idPrefix}-upload-file`;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!file || busy) return;

    setBusy(true);
    setError(null);
    setStatus("Uploading...");
    try {
      const formData = new FormData();
      formData.append("file", file);
      const source = await apiPostForm<{ id: string }>("/sources/upload", formData);
      onUploaded?.(source.id);

      if (!autoIngest) {
        setStatus(`Registered source ${source.id.slice(0, 8)}.`);
        setFile(null);
        return;
      }

      setStatus("Starting ingest...");
      const result = await runIngest(source.id, ({ runNumber, runId, elapsedSeconds }) => {
        // Elapsed time, always. A slow or retrying LLM call can take a
        // while, and a spinner with no number cannot be told from a hang.
        setStatus(
          `Ingesting ${runLabelOf(runNumber, runId)}... (${elapsedSeconds}s elapsed - still running, not stuck)`
        );
      });
      setStatus(`Ingest finished: ${runLabelOf(result.run_number, result.run_id)}, status=${result.status}`);
      setFile(null);
      onIngested?.(result);
    } catch (err) {
      setError(err);
      setStatus(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3">
      <Muted>{description}</Muted>
      <div className="flex flex-wrap items-center gap-3">
        <input
          id={inputId}
          type="file"
          accept=".csv,.tsv,.xlsx,.xls"
          aria-label="File to upload"
          onChange={(e) => {
            setFile(e.target.files?.[0] ?? null);
            setError(null);
          }}
          className="text-sm"
        />
        <Button type="submit" variant="primary" disabled={!file} loading={busy} loadingText={autoIngest ? "Working..." : "Uploading..."}>
          {submitLabel}
        </Button>
      </div>
      {/* Progress is a sentence with a number in it, never a bare spinner -
          and it lives here rather than in the button, because a control's
          label is not the place for a growing elapsed-time sentence. */}
      {status && <p className="text-sm text-ink-muted">{status}</p>}
      <ErrorMessage error={error} />
    </form>
  );
}
