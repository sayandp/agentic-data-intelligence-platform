import { Badge, Card, Muted } from "./ui";

// The Session Summary, at the top of the run view.
//
// Two rules shape this component. Quality context is rendered FIRST and
// separately, because a plain-language conclusion read before the caveat that
// qualifies it is exactly what the caveat exists to prevent. And a template
// summary says so, with its reason - a reader who cannot tell which mode wrote
// a summary cannot judge how much to trust its phrasing.

export interface SessionSummaryRecord {
  run_id: string;
  run_number: number | null;
  /** False when the run exists but has no summary - an expected state, not an error. */
  available?: boolean;
  reason?: string;
  quality_context: string;
  summary_text: string;
  rendered: string;
  generation_mode: string;
  fallback_reason: string | null;
  claims: unknown[] | null;
  facts: unknown[] | null;
  post_check_results: unknown[] | null;
  generated_at: string;
}

export function SessionSummarySection({ summary }: { summary: SessionSummaryRecord | null | undefined }) {
  if (!summary || summary.available === false) {
    return (
      <Card title="Summary" subtitle="A short summary of this run, for a reader who will not read the statistics.">
        <Muted>
          {summary?.reason ??
            "This run has no session summary. It is written once, when a run completes - a run that finished before this agent existed will not have one."}
        </Muted>
      </Card>
    );
  }

  return (
    <Card title="Summary" subtitle="Written from this run's stored results, not from the exported deck.">
      {/* Quality context first, always. Its own block, so nothing downstream
          can render the summary while dropping the caveat. */}
      <div className="mb-4 border-l-2 border-border-strong pl-3">
        <div className="text-label uppercase text-ink-faint">Data quality context</div>
        <p className="mt-1 whitespace-pre-line text-sm text-ink-muted">{summary.quality_context}</p>
      </div>

      <p className="text-ink">{summary.summary_text}</p>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <Badge
          value={summary.generation_mode === "llm" ? "written by a model" : "written without a model"}
          tone={summary.generation_mode === "llm" ? "active" : "neutral"}
        />
        {summary.generation_mode !== "llm" && summary.fallback_reason && (
          <span className="text-sm text-ink-faint">{summary.fallback_reason}</span>
        )}
      </div>
    </Card>
  );
}
