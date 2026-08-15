import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { apiFetch } from "../api/client";
import type { AuditRecord } from "../api/types";
import { Badge, Button, Card, CodeBlock, ErrorMessage, Muted, RunLabel } from "../components/ui";

export default function AuditPage() {
  const [params] = useSearchParams();
  // Dashboard UX pass, Part 1: accepts a plain run_number ("17"/"#17") or
  // the full UUID - see app/id_lookup.py::resolve_run. A number match is
  // always exact (run_number is UNIQUE), so there is no ambiguity path to
  // handle here, unlike the earlier id-prefix scheme this replaced.
  const [runQuery, setRunQuery] = useState(params.get("run") ?? "");
  const [audit, setAudit] = useState<AuditRecord | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);

  async function loadAudit(query: string) {
    if (!query) return;
    setLoading(true);
    setError(null);
    setAudit(null);
    try {
      const data = await apiFetch<AuditRecord>(`/audit/${query}`);
      setAudit(data);
      // Normalize the box to the run number once resolved, so a load-by-
      // UUID still ends up showing (and re-loadable by) the short form.
      setRunQuery(data.run_number != null ? String(data.run_number) : data.run_id);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (params.get("run")) loadAudit(params.get("run")!);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div>
      <h1 className="mb-2 text-page-title text-ink">Audit</h1>
      <Muted>GET /audit/&#123;run_id&#125; - the canonical, single source of truth for "why did this run produce this output". This page renders that response and nothing else.</Muted>
      <div className="my-6 flex items-center gap-3">
        <input
          value={runQuery}
          onChange={(e) => setRunQuery(e.target.value)}
          placeholder="run number (e.g. 17) or full run id"
          className="w-96 rounded-sm border border-border-strong px-3 py-1.5 text-sm"
        />
        <Button variant="primary" onClick={() => loadAudit(runQuery)} loading={loading} loadingText="Loading...">
          Load
        </Button>
      </div>

      <ErrorMessage error={error} />

      {audit && (
        <>
          <Card>
            <div className="mb-4 flex items-center gap-2 text-sm">
              <RunLabel runNumber={audit.run_number} runId={audit.run_id} />
            </div>
            <div className="flex flex-wrap gap-6 text-sm">
              <div><span className="font-medium">Status:</span> <Badge value={audit.run_status} /></div>
              <div><span className="font-medium">Started:</span> {audit.started_at}</div>
              <div><span className="font-medium">Completed:</span> {audit.completed_at ?? "-"}</div>
              <div><span className="font-medium">Reveal depth reached:</span> {audit.reveal_depth_reached}</div>
            </div>
          </Card>

          <Card title="Timeline - nodes visited and the edge taken out of each">
            <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-border bg-surface-sunken text-ink-muted">
                  <th className="py-2 px-3 text-right font-medium">#</th>
                  <th className="py-2 px-3 font-medium">Node</th>
                  <th className="py-2 px-3 font-medium">Edge taken</th>
                  <th className="py-2 px-3 text-right font-medium">Confidence</th>
                  <th className="py-2 px-3 font-medium">Timestamp</th>
                  <th className="py-2 px-3 font-medium">Output</th>
                </tr>
              </thead>
              <tbody>
                {audit.trace.map((t, i) => (
                  <tr key={t.id} className="border-b border-border align-top">
                    <td className="py-2 px-3 text-right font-mono text-ink-faint">{i + 1}</td>
                    <td className="py-2 px-3 font-medium text-ink">{t.node}</td>
                    <td className="py-2 px-3">{t.edge_taken ? <code className="font-mono text-xs text-ink-muted">{t.edge_taken}</code> : <span className="text-ink-faint">-</span>}</td>
                    <td className="py-2 px-3 text-right font-mono">{t.confidence ?? ""}</td>
                    <td className="py-2 px-3 font-mono text-xs text-ink-muted">{t.timestamp}</td>
                    <td className="py-2 px-3">
                      {/* Part 5: collapsed by default - the JSON is the same
                          content unchanged, just not dominating the page by
                          default. One click (native <details>, no extra JS)
                          opens it. */}
                      <details className="max-w-md">
                        <summary className="cursor-pointer text-xs font-medium text-brand-600">Show output</summary>
                        <div className="mt-1">
                          <CodeBlock>{typeof t.output === "string" ? t.output : JSON.stringify(t.output, null, 2)}</CodeBlock>
                        </div>
                      </details>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          </Card>

          <Card title="Validation events">
            <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-border bg-surface-sunken text-ink-muted">
                  <th className="py-2 px-3 font-medium">Rule</th>
                  <th className="py-2 px-3 font-medium">Column</th>
                  <th className="py-2 px-3 font-medium">State</th>
                  <th className="py-2 px-3 font-medium">Action taken</th>
                  <th className="py-2 px-3 font-medium">Gate reasons</th>
                  <th className="py-2 px-3 font-medium">Resolved by</th>
                </tr>
              </thead>
              <tbody>
                {audit.validation_events.map((e) => {
                  const reverted = e.action_taken === "auto_fix_reverted";
                  return (
                    <tr key={e.id} className={`border-b border-border ${reverted ? "bg-status-negative-tint" : ""}`}>
                      <td className="py-2 px-3">{e.rule_failed}</td>
                      <td className="py-2 px-3">{e.column ?? ""}</td>
                      <td className="py-2 px-3"><Badge value={e.state} /></td>
                      <td className="py-2 px-3">
                        {e.action_taken ?? ""}
                        {reverted && <strong className="ml-1 text-status-negative">(reverted)</strong>}
                      </td>
                      <td className="py-2 px-3 text-ink-muted">{(e.gate_reasons ?? []).join("; ")}</td>
                      <td className="py-2 px-3">{e.resolved_by ?? ""}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            </div>
          </Card>

          {audit.query_runs.length > 0 && (
            <Card title="Query runs against this data">
              <div className="flex flex-col gap-4">
                {audit.query_runs.map((q) => (
                  <div key={q.id} className="rounded-md border border-border p-4">
                    <p className="mb-1 text-sm"><span className="font-medium">Q:</span> {q.question}</p>
                    <p className="mb-2 text-sm">
                      <span className="font-medium">State:</span> <Badge value={q.state} /> {q.escalation_reason && `(${q.escalation_reason})`}
                      {q.resolved_by && ` · resolved by ${q.resolved_by}`}
                    </p>
                    <CodeBlock>{q.generated_code || "(no code)"}</CodeBlock>
                  </div>
                ))}
              </div>
            </Card>
          )}

          {audit.model_runs.length > 0 && (
            <Card title="Model runs against this data">
              <div className="flex flex-col gap-4">
                {audit.model_runs.map((m) => (
                  <div key={m.id} className="rounded-md border border-border p-4">
                    <p className="mb-1 text-sm"><span className="font-medium">Target:</span> {m.target_column} ({m.task_type})</p>
                    <p className="mb-2 text-sm">
                      <span className="font-medium">State:</span> <Badge value={m.state} /> {m.escalation_reason && `(${m.escalation_reason})`}
                      {m.resolved_by && ` · resolved by ${m.resolved_by}`}
                    </p>
                    <p className="text-sm"><span className="font-medium">Score:</span> {m.out_of_sample_metric} = {m.out_of_sample_score}</p>
                  </div>
                ))}
              </div>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
