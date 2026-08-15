import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { apiPostJson } from "../api/client";
import type { AskResponse } from "../api/types";
import { Badge, Button, Card, CodeBlock, ErrorMessage, Muted } from "../components/ui";
import { RunNotFoundHelp, RunPicker, exampleQuestions, useRunSelection } from "../components/RunPicker";

export default function AskPage() {
  const [params] = useSearchParams();
  // ?run=<number|uuid> - the same contract View report/View audit already
  // use, so a run can be carried here from Sources or the report itself
  // with nothing to copy (see lib/runLinks.ts::askLink).
  const selection = useRunSelection(params.get("run") ?? "");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<AskResponse | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [asking, setAsking] = useState(false);

  const examples = exampleQuestions(selection.columnTypes, "ask");
  const placeholder = examples[0] ?? "ask a question about this run's data";

  async function handleAsk(e: React.FormEvent) {
    e.preventDefault();
    setAsking(true);
    setError(null);
    setAnswer(null);
    try {
      // run_id only: a run already knows its source, and POST /ask ignores
      // source_id whenever run_id is present.
      const result = await apiPostJson<AskResponse>("/ask", { run_id: selection.runRef.trim(), question });
      setAnswer(result);
    } catch (err) {
      setError(err);
    } finally {
      setAsking(false);
    }
  }

  return (
    <div>
      <h1 className="mb-6 text-page-title text-ink">Ask</h1>

      <Card>
        <form onSubmit={handleAsk} className="flex flex-col gap-4">
          <RunPicker selection={selection} idPrefix="ask" />

          <div>
            <label htmlFor="ask-question" className="mb-1 block text-sm font-medium text-ink-muted">
              Question
            </label>
            <textarea
              id="ask-question"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              rows={2}
              placeholder={placeholder}
              className="w-full rounded-sm border border-border-strong px-3 py-2 text-sm"
            />
            {/* Examples built from THIS run's columns, not generic copy -
                clicking one fills the box rather than making it a puzzle. */}
            {examples.length > 0 && (
              <p className="mt-1 text-xs text-ink-muted">
                Try:{" "}
                {examples.map((ex, i) => (
                  <span key={ex}>
                    {i > 0 && " · "}
                    <button type="button" onClick={() => setQuestion(ex)} className="text-brand-600 hover:underline">
                      {ex}
                    </button>
                  </span>
                ))}
              </p>
            )}
          </div>

          <div>
            <Button
              type="submit"
              variant="primary"
              disabled={!question.trim() || !selection.runRef.trim()}
              loading={asking}
              loadingText="Asking..."
            >
              Ask
            </Button>
          </div>
        </form>
      </Card>

      <ErrorMessage error={error} />
      <RunNotFoundHelp error={error} selection={selection} />

      {answer && (
        <Card>
          <div className="mb-3 flex items-center gap-2">
            <span className="text-sm font-medium">Status:</span>
            <Badge value={answer.status} />
          </div>
          {answer.escalation_reason && (
            <p className="mb-3 text-sm text-ink-muted">
              <span className="font-medium text-ink">Escalation:</span> {answer.escalation_reason} - {answer.escalation_detail}
            </p>
          )}
          {/* The generated code is shown alongside the answer, never the answer
              alone - true for an answered question AND an escalated one, since
              even an escalation may carry code that just wasn't confident/valid
              enough to run unattended. */}
          <h3 className="mb-2 text-sm font-semibold text-ink">Generated code</h3>
          <div className="mb-4"><CodeBlock>{answer.code || "(no code was generated)"}</CodeBlock></div>

          {answer.status === "answered" && (
            <>
              <h3 className="mb-2 text-sm font-semibold text-ink">Result</h3>
              <div className="mb-2"><CodeBlock>{JSON.stringify(answer.result, null, 2)}</CodeBlock></div>
              {answer.truncated && <Muted>Result was truncated.</Muted>}
            </>
          )}

          <h3 className="mt-4 mb-2 text-sm font-semibold text-ink">Quality context</h3>
          <CodeBlock>{answer.quality_context_summary ?? ""}</CodeBlock>
        </Card>
      )}
    </div>
  );
}
