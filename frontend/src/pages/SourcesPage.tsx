import { Fragment, useEffect, useState } from "react";
import { apiFetch, apiPostForm, pollIngestStatus } from "../api/client";
import type { RunSummary, SourceRecord } from "../api/types";
import { Badge, Button, Card, CopyableId, ErrorMessage, Muted, RunLabel, Toast } from "../components/ui";
import { askLink, auditLink, predictLink, reportLink } from "../lib/runLinks";
import { useToast } from "../hooks/useToast";
import { Link } from "react-router-dom";

const SOURCE_TYPES = ["file", "sql", "api"] as const;

export default function SourcesPage() {
  const [sources, setSources] = useState<SourceRecord[]>([]);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [sourcesLoading, setSourcesLoading] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [runsBySource, setRunsBySource] = useState<Record<string, RunSummary[]>>({});
  const [runsError, setRunsError] = useState<unknown>(null);
  const [historyLoadingId, setHistoryLoadingId] = useState<string | null>(null);

  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadResult, setUploadResult] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<unknown>(null);
  const [uploading, setUploading] = useState(false);

  const [sourceType, setSourceType] = useState<(typeof SOURCE_TYPES)[number]>("file");
  const [configText, setConfigText] = useState('{"path": "/data/orders.csv"}');
  const [registerResult, setRegisterResult] = useState<string | null>(null);
  const [registerError, setRegisterError] = useState<unknown>(null);
  const [registering, setRegistering] = useState(false);

  // Ingest is the slowest action on this page by far - it can involve
  // diagnosis calls with LLM retry-with-backoff - so it gets its own
  // per-source tracking (which source is mid-ingest) plus a result banner
  // instead of alert(), which blocks the whole tab until dismissed.
  const [ingestingId, setIngestingId] = useState<string | null>(null);
  const [ingestResultBySource, setIngestResultBySource] = useState<
    Record<string, { ok: boolean; message: string; runId?: string; runNumber?: number | null }>
  >({});

  const { toast, showToast, dismissToast } = useToast();

  async function loadSources() {
    setSourcesLoading(true);
    try {
      setLoadError(null);
      const list = await apiFetch<SourceRecord[]>("/sources");
      setSources(list);
    } catch (err) {
      setLoadError(err);
    } finally {
      setSourcesLoading(false);
    }
  }

  useEffect(() => {
    loadSources();
  }, []);

  // POST /ingest returns {run_id, status: "running"} right away - the
  // graph runs in the background (backend/app/routers/ingest.py), so this
  // never holds one fetch() open for however long narrate_node's LLM calls
  // take (previously: 0.3s to 60s+ to what looked like an indefinite hang,
  // since fetch() has no default timeout of its own). pollIngestStatus's
  // onTick keeps this message updated with elapsed time so a slow LLM
  // backoff retry reads as "working, waiting on a slow call" rather than
  // being indistinguishable from a dead request.
  async function triggerIngest(sourceId: string) {
    setIngestingId(sourceId);
    setIngestResultBySource((prev) => ({ ...prev, [sourceId]: { ok: true, message: "Starting ingest..." } }));
    try {
      const start = await apiFetch<{ run_id: string; run_number: number | null; status: string }>(`/ingest/${sourceId}`, { method: "POST" });
      const runLabel = start.run_number != null ? `Run #${start.run_number}` : `Run ${start.run_id.slice(0, 8)}`;
      const result = await pollIngestStatus(start.run_id, (elapsedMs) => {
        const seconds = Math.round(elapsedMs / 1000);
        setIngestResultBySource((prev) => ({
          ...prev,
          [sourceId]: {
            ok: true,
            message: `Ingesting ${runLabel}... (${seconds}s elapsed - still running; a slow or retrying LLM call can take a while, this is not stuck)`,
          },
        }));
      });
      setIngestResultBySource((prev) => ({
        ...prev,
        [sourceId]: {
          ok: true,
          message: `Ingest finished: ${runLabel}, status=${result.status}`,
          runId: result.run_id,
          runNumber: result.run_number,
        },
      }));
      showToast(`Ingest finished: ${runLabel} - ${result.status}.`, "success");
      loadSources();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setIngestResultBySource((prev) => ({
        ...prev,
        [sourceId]: { ok: false, message: `Ingest failed: ${message}` },
      }));
      showToast(`Ingest failed: ${message}`, "error");
    } finally {
      setIngestingId(null);
    }
  }

  async function toggleHistory(sourceId: string) {
    if (expanded === sourceId) {
      setExpanded(null);
      return;
    }
    setExpanded(sourceId);
    if (!runsBySource[sourceId]) {
      setHistoryLoadingId(sourceId);
      try {
        const runs = await apiFetch<RunSummary[]>(`/sources/${sourceId}/runs`);
        setRunsBySource((prev) => ({ ...prev, [sourceId]: runs }));
      } catch (err) {
        setRunsError(err);
      } finally {
        setHistoryLoadingId(null);
      }
    }
  }

  async function handleUpload(e: React.FormEvent) {
    e.preventDefault();
    if (!uploadFile) return;
    setUploading(true);
    setUploadError(null);
    setUploadResult(null);
    try {
      const formData = new FormData();
      formData.append("file", uploadFile);
      const result = await apiPostForm<{ id: string }>("/sources/upload", formData);
      setUploadResult(result.id);
      setUploadFile(null);
      showToast(`Uploaded and registered source ${result.id.slice(0, 8)}.`, "success");
      loadSources();
    } catch (err) {
      setUploadError(err);
      showToast(`Upload failed: ${err instanceof Error ? err.message : String(err)}`, "error");
    } finally {
      setUploading(false);
    }
  }

  async function handleRegister(e: React.FormEvent) {
    e.preventDefault();
    setRegisterError(null);
    setRegisterResult(null);
    let connectionConfig: unknown;
    try {
      connectionConfig = configText.trim() ? JSON.parse(configText) : {};
    } catch (err) {
      setRegisterError(new Error(`connection_config must be valid JSON: ${err instanceof Error ? err.message : String(err)}`));
      return;
    }
    setRegistering(true);
    try {
      const result = await apiFetch<{ id: string }>("/sources", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ type: sourceType, connection_config: connectionConfig }),
      });
      setRegisterResult(result.id);
      setConfigText("");
      showToast(`Registered source ${result.id.slice(0, 8)}.`, "success");
      loadSources();
    } catch (err) {
      setRegisterError(err);
      showToast(`Register failed: ${err instanceof Error ? err.message : String(err)}`, "error");
    } finally {
      setRegistering(false);
    }
  }

  return (
    <div>
      <h1 className="mb-6 text-page-title text-ink">Sources</h1>

      <Card title="Upload a CSV or Excel file" subtitle="The simplest way to register a file source - no filesystem path to type or share with the app process.">
        <form onSubmit={handleUpload} className="flex flex-wrap items-center gap-3">
          <input
            type="file"
            accept=".csv,.xlsx,.xls"
            onChange={(e) => setUploadFile(e.target.files?.[0] ?? null)}
            className="text-sm text-ink-muted file:mr-3 file:rounded-sm file:border-0 file:bg-brand-50 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-brand-700 hover:file:bg-brand-100"
          />
          <Button type="submit" variant="primary" disabled={!uploadFile} loading={uploading} loadingText="Uploading...">
            Upload
          </Button>
        </form>
        {uploadResult && (
          <p className="mt-3 flex items-center gap-2 text-sm text-status-positive">
            Uploaded and registered: <CopyableId id={uploadResult} />
          </p>
        )}
        <ErrorMessage error={uploadError} />
      </Card>

      <Card title="Register a source another way" subtitle="For a SQL/API source, or a file the app process already has a path to.">
        <form onSubmit={handleRegister}>
          <div className="mb-3 flex items-center gap-3">
            <label className="text-sm font-medium text-ink-muted">Type</label>
            <select
              value={sourceType}
              onChange={(e) => setSourceType(e.target.value as (typeof SOURCE_TYPES)[number])}
              className="rounded-sm border border-border-strong px-2 py-1.5 text-sm"
            >
              {SOURCE_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>
          <Muted>
            connection_config (JSON) - e.g. file: <code>{'{"path": "/data/orders.csv"}'}</code>, sql:{" "}
            <code>{'{"connection_string_env": "MY_DB_URL", "query": "select * from orders"}'}</code>, api: <code>{'{"url": "https://example/items"}'}</code>
          </Muted>
          <textarea
            value={configText}
            onChange={(e) => setConfigText(e.target.value)}
            rows={3}
            className="mt-2 w-full rounded-sm border border-border-strong px-3 py-2 font-mono text-sm"
          />
          <div className="mt-3">
            <Button type="submit" variant="primary" loading={registering} loadingText="Registering...">
              Register
            </Button>
          </div>
        </form>
        {registerResult && (
          <p className="mt-3 flex items-center gap-2 text-sm text-status-positive">
            Registered: <CopyableId id={registerResult} />
          </p>
        )}
        <ErrorMessage error={registerError} />
      </Card>

      <Card title="Registered sources">
        <div className="mb-3 flex items-center justify-between">
          <Muted>{sources.length} source{sources.length === 1 ? "" : "s"}</Muted>
          <Button onClick={loadSources} loading={sourcesLoading} loadingText="Refreshing...">
            Refresh
          </Button>
        </div>
        {Boolean(loadError) && (
          <div className="mb-3 flex items-center gap-3">
            <ErrorMessage error={loadError} />
            <Button onClick={loadSources} loading={sourcesLoading} loadingText="Retrying...">
              Retry
            </Button>
          </div>
        )}
        {sources.length === 0 && !loadError && <Muted>No sources registered yet.</Muted>}
        {sources.length > 0 && (
          <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-border bg-surface-sunken text-ink-muted">
                <th className="py-2 px-3 font-medium">ID</th>
                <th className="py-2 px-3 font-medium">Type</th>
                <th className="py-2 px-3 font-medium">Source</th>
                <th className="py-2 px-3 font-medium">Created</th>
                <th className="py-2 px-3 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {sources.map((s) => {
                const ingestResult = ingestResultBySource[s.id];
                const isIngesting = ingestingId === s.id;
                return (
                  <Fragment key={s.id}>
                    <tr className="border-b border-border">
                      <td className="py-2 px-3"><CopyableId id={s.id} /></td>
                      <td className="py-2 px-3">{s.type}</td>
                      <td className="py-2 px-3">
                        {s.connection_config.original_filename ? (
                          <>
                            {s.connection_config.original_filename} <span className="text-ink-faint">(uploaded)</span>
                          </>
                        ) : (
                          <code className="font-mono text-xs text-ink-faint">{JSON.stringify(s.connection_config)}</code>
                        )}
                      </td>
                      <td className="py-2 px-3 text-ink-muted">{s.created_at}</td>
                      <td className="py-2 px-3">
                        <div className="flex gap-2">
                          <Button onClick={() => triggerIngest(s.id)} loading={isIngesting} loadingText="Ingesting...">
                            Ingest
                          </Button>
                          <Button onClick={() => toggleHistory(s.id)} loading={historyLoadingId === s.id} loadingText="Loading...">
                            History
                          </Button>
                        </div>
                        {/* Shown WHILE ingesting too, not just after - this is where
                            the polling progress message (elapsed time, "still
                            running") actually reaches the user. Hiding it during
                            isIngesting would silently defeat that: the button's
                            own spinner text has no room for elapsed-time detail,
                            and a slow LLM backoff retry needs to read as "working,
                            waiting" rather than a bare spinner with no detail. */}
                        {ingestResult && (
                          <p className={`mt-1 max-w-xs text-xs ${ingestResult.ok ? "text-status-positive" : "text-status-negative"}`}>
                            {ingestResult.message}
                          </p>
                        )}
                        {/* Part 2: kill the copy-paste workflow - once ingest
                            has actually finished (runId is only set in the
                            terminal branch below, never while still
                            "running"), jump straight to the run's report/
                            audit with the id prefilled, no manual paste. */}
                        {ingestResult?.ok && ingestResult.runId && (
                          <p className="mt-1 text-xs">
                            <Link className="text-brand-600 hover:underline" to={reportLink(ingestResult.runId, ingestResult.runNumber ?? null)}>
                              View report
                            </Link>{" "}
                            &middot;{" "}
                            <Link className="text-brand-600 hover:underline" to={auditLink(ingestResult.runId, ingestResult.runNumber ?? null)}>
                              View audit
                            </Link>{" "}
                            &middot;{" "}
                            <Link className="text-brand-600 hover:underline" to={askLink(ingestResult.runId, ingestResult.runNumber ?? null)}>
                              Ask
                            </Link>{" "}
                            &middot;{" "}
                            <Link className="text-brand-600 hover:underline" to={predictLink(ingestResult.runId, ingestResult.runNumber ?? null)}>
                              Predict
                            </Link>
                          </p>
                        )}
                      </td>
                    </tr>
                    {expanded === s.id && (
                      <tr key={`${s.id}-history`}>
                        <td colSpan={5} className="bg-surface-sunken px-3 py-3">
                          <ErrorMessage error={runsError} />
                          {!runsBySource[s.id] ? (
                            <Muted>Loading...</Muted>
                          ) : runsBySource[s.id].length === 0 ? (
                            <Muted>No runs yet.</Muted>
                          ) : (
                            <div className="overflow-x-auto">
                            <table className="w-full text-left text-sm">
                              <thead>
                                <tr className="text-ink-muted">
                                  <th className="py-1 px-3 font-medium">Run</th>
                                  <th className="py-1 px-3 font-medium">Status</th>
                                  <th className="py-1 px-3 font-medium">Started</th>
                                  <th className="py-1 px-3 font-medium">Completed</th>
                                  <th className="py-1 px-3 font-medium">Links</th>
                                </tr>
                              </thead>
                              <tbody>
                                {runsBySource[s.id].map((r) => (
                                  <tr key={r.id}>
                                    <td className="py-1 px-3">
                                      <RunLabel runNumber={r.run_number} runId={r.id} />
                                    </td>
                                    <td className="py-1 px-3"><Badge value={r.status} /></td>
                                    <td className="py-1 px-3 text-ink-muted">{r.started_at ?? ""}</td>
                                    <td className="py-1 px-3 text-ink-muted">{r.completed_at ?? ""}</td>
                                    <td className="py-1 px-3">
                                      <Link className="text-brand-600 hover:underline" to={reportLink(r.id, r.run_number)}>
                                        View report
                                      </Link>{" "}
                                      &middot;{" "}
                                      <Link className="text-brand-600 hover:underline" to={auditLink(r.id, r.run_number)}>
                                        View audit
                                      </Link>
                                      {/* Ask/Predict carry the run straight
                                          through (?run=), so neither screen
                                          ever needs an id typed by hand. Only
                                          offered for a completed run - those
                                          are the only ones either can answer
                                          against (same rule GET /runs applies
                                          to the picker). */}
                                      {r.status === "completed" && (
                                        <>
                                          {" "}
                                          &middot;{" "}
                                          <Link className="text-brand-600 hover:underline" to={askLink(r.id, r.run_number)}>
                                            Ask
                                          </Link>{" "}
                                          &middot;{" "}
                                          <Link className="text-brand-600 hover:underline" to={predictLink(r.id, r.run_number)}>
                                            Predict
                                          </Link>
                                        </>
                                      )}
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                            </div>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
          </div>
        )}
      </Card>

      <Toast toast={toast} onDismiss={dismissToast} />
    </div>
  );
}
