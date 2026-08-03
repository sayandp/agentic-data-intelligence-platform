import { useState } from "react";
import { apiPostJson } from "../api/client";
import type { AskResponse } from "../api/types";
import { Badge, Button, Card, ErrorMessage, Muted } from "../components/ui";

export default function AskPage() {
  const [sourceId, setSourceId] = useState("");
  const [runId, setRunId] = useState("");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<AskResponse | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [asking, setAsking] = useState(false);

  async function handleAsk(e: React.FormEvent) {
    e.preventDefault();
    setAsking(true);
    setError(null);
    setAnswer(null);
    try {
      const payload: Record<string, string> = { question };
      if (sourceId) payload.source_id = sourceId;
      if (runId) payload.run_id = runId;
      const result = await apiPostJson<AskResponse>("/ask", payload);
      setAnswer(result);
    } catch (err) {
      setError(err);
    } finally {
      setAsking(false);
    }
  }

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold text-slate-900">Ask</h1>

      <Card>
        <form onSubmit={handleAsk} className="flex flex-col gap-3">
          <div>
            <label className="mb-1 block text-sm font-medium text-slate-700">Source ID</label>
            <input
              value={sourceId}
              onChange={(e) => setSourceId(e.target.value)}
              placeholder="source_id (or leave blank and give a run_id)"
              className="w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
            />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium text-slate-700">Run ID</label>
            <input
              value={runId}
              onChange={(e) => setRunId(e.target.value)}
              placeholder="run_id (optional, pins the exact repaired frame)"
              className="w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
            />
          </div>
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            rows={2}
            placeholder="What is the average order amount?"
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
          />
          <div>
            <Button type="submit" variant="primary" disabled={!question.trim()} loading={asking} loadingText="Asking...">
              Ask
            </Button>
          </div>
        </form>
      </Card>

      <ErrorMessage error={error} />

      {answer && (
        <Card>
          <div className="mb-3 flex items-center gap-2">
            <span className="text-sm font-medium">Status:</span>
            <Badge value={answer.status} />
          </div>
          {answer.escalation_reason && (
            <p className="mb-3 text-sm text-slate-600">
              <span className="font-medium text-slate-700">Escalation:</span> {answer.escalation_reason} - {answer.escalation_detail}
            </p>
          )}
          {/* The generated code is shown alongside the answer, never the answer
              alone - true for an answered question AND an escalated one, since
              even an escalation may carry code that just wasn't confident/valid
              enough to run unattended. */}
          <h3 className="mb-2 text-sm font-semibold text-slate-800">Generated code</h3>
          <pre className="mb-4 overflow-x-auto rounded-xl bg-slate-900 p-4 text-sm text-slate-100">{answer.code || "(no code was generated)"}</pre>

          {answer.status === "answered" && (
            <>
              <h3 className="mb-2 text-sm font-semibold text-slate-800">Result</h3>
              <pre className="mb-2 overflow-x-auto rounded-xl bg-slate-900 p-4 text-sm text-slate-100">{JSON.stringify(answer.result, null, 2)}</pre>
              {answer.truncated && <Muted>Result was truncated.</Muted>}
            </>
          )}

          <h3 className="mt-4 mb-2 text-sm font-semibold text-slate-800">Quality context</h3>
          <pre className="overflow-x-auto rounded-xl bg-slate-900 p-4 text-sm text-slate-100">{answer.quality_context_summary ?? ""}</pre>
        </Card>
      )}
    </div>
  );
}
