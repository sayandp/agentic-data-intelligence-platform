import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { apiPostJson, pollPredictStatus } from "../api/client";
import type { ForecastSeries, PredictAck, PredictResult } from "../api/types";
import { Badge, Button, Card, ErrorMessage, Muted } from "../components/ui";
import { RunNotFoundHelp, RunPicker, exampleQuestions, useRunSelection } from "../components/RunPicker";
import { CHART_ACCENT, loadPlotly } from "../lib/plotly";

// Dashboard UX pass, Part 1: "the real missing feature" - the Modeling
// Agent (POST /predict, GET /models/{run_id}) had no UI at all despite
// being the platform's most advanced agent. This page follows the SAME
// picker pattern AskPage.tsx already uses (source_id / run_id, either one)
// plus a natural-language question, and the SAME async pattern
// ingest/resolve already use (app/routers/predict.py's RESOLVE-HANG-style
// fix) - immediate ack, then pollPredictStatus (api/client.ts), never a
// second bespoke polling loop.

function buildForecastFigure(series: ForecastSeries) {
  const historicalX = series.historical.map((p) => p.date);
  const historicalY = series.historical.map((p) => p.value);
  const forecastX = series.forecast.map((p) => p.date);
  const forecastY = series.forecast.map((p) => p.value);
  const hasInterval = series.forecast.some((p) => p.lower != null && p.upper != null);

  // Actual (already-known data) stays a neutral ink line; the accent color
  // is reserved for Forecast (the model's own output) - the one thing on
  // this chart that's actually new information, same "one accent, used for
  // what matters" reasoning as everywhere else in this system.
  const data: unknown[] = [{ x: historicalX, y: historicalY, mode: "lines", name: "Actual", line: { color: "#4b5563" } }];

  if (hasInterval) {
    const upperY = series.forecast.map((p) => p.upper ?? p.value);
    const lowerY = series.forecast.map((p) => p.lower ?? p.value);
    data.push(
      { x: forecastX, y: upperY, mode: "lines", line: { width: 0 }, showlegend: false, hoverinfo: "skip" },
      {
        x: forecastX,
        y: lowerY,
        mode: "lines",
        line: { width: 0 },
        fill: "tonexty",
        fillcolor: "rgba(11,110,110,0.15)",
        name: "Interval",
      }
    );
  }
  data.push({ x: forecastX, y: forecastY, mode: "lines", name: "Forecast", line: { color: CHART_ACCENT, dash: "dash" } });

  // No native Plotly title - the Card wrapper's own "Forecast chart" caption
  // is the only title shown (same duplicate-chrome fix already applied to
  // the report charts on ReportsPage.tsx).
  const layout = { xaxis: { title: "Period" }, yaxis: { title: "Value" }, margin: { t: 16, r: 16, b: 40, l: 48 } };
  return { data, layout };
}

function ForecastChart({ series }: { series: ForecastSeries }) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    loadPlotly().then(() => {
      const { data, layout } = buildForecastFigure(series);
      if (containerRef.current && window.Plotly) {
        try {
          window.Plotly.newPlot(containerRef.current, data, layout, { responsive: true });
        } catch {
          containerRef.current.innerHTML = '<p class="text-sm text-ink-muted">Chart could not be rendered.</p>';
        }
      }
    });
  }, [series]);

  return <div ref={containerRef} />;
}

const ESCALATION_LABELS: Record<string, string> = {
  llm_unavailable: "No LLM is configured/reachable for intent classification.",
  unanswerable: "The model reported this question cannot be answered from the given schema.",
  target_not_found: "The named target column is absent from the live schema.",
  unsupported_task_type: "This column doesn't fit a supported task shape (forecast/classification/regression) from data shape alone.",
  insufficient_rows: "Not enough rows/periods are available to train reliably.",
  no_model_beats_baseline: "No candidate model outperformed its trivial baseline by the required margin.",
  below_score_floor: "The winning model's score is below the configured floor.",
  severe_class_imbalance: "The target is severely imbalanced - a high score here would be misleading.",
};

export default function PredictPage() {
  const [params] = useSearchParams();
  // ?run=<number|uuid> - same contract as View report/View audit, so a run
  // reaches this page without anyone copying an identifier.
  const selection = useRunSelection(params.get("run") ?? "");
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<PredictResult | null>(null);
  const [progressMessage, setProgressMessage] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [predicting, setPredicting] = useState(false);

  async function handlePredict(e: React.FormEvent) {
    e.preventDefault();
    setPredicting(true);
    setError(null);
    setResult(null);
    setProgressMessage(null);
    try {
      // run_id only - a run already knows its source, and POST /predict
      // ignores source_id whenever run_id is present. Asking for both is
      // what allowed a source UUID and an unrelated run id to be submitted
      // together in the first place.
      const ack = await apiPostJson<PredictAck>("/predict", { run_id: selection.runRef.trim(), question });
      const final = await pollPredictStatus(ack.id, (elapsedMs) => {
        const seconds = Math.round(elapsedMs / 1000);
        setProgressMessage(`Still training (${seconds}s elapsed) - fitting and cross-validating candidate models can take a while, this is not stuck`);
      });
      setResult(final);
    } catch (err) {
      setError(err);
    } finally {
      setPredicting(false);
      setProgressMessage(null);
    }
  }

  const isRetrieval = result?.state === "redirected";
  const isFailed = result?.state === "failed";
  const isEscalated = result?.status === "escalated";
  const canAppealToApprovals = result?.state === "awaiting_approval";
  const forecastSeries = result?.forecast_series;
  const examples = exampleQuestions(selection.columnTypes, "predict");
  const predictPlaceholder = examples[0] ?? "what should this run's data be used to forecast?";

  return (
    <div>
      <h1 className="mb-6 text-[22px] font-semibold text-ink">Predict</h1>

      <Card>
        <form onSubmit={handlePredict} className="flex flex-col gap-4">
          <RunPicker selection={selection} idPrefix="predict" />

          <div>
            <label htmlFor="predict-question" className="mb-1 block text-sm font-medium text-ink-muted">
              Question
            </label>
            <textarea
              id="predict-question"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              rows={2}
              placeholder={predictPlaceholder}
              className="w-full rounded-sm border border-border-strong px-3 py-2 text-sm"
            />
            {/* Examples drawn from THIS run's own numeric/date columns, so a
                forecast is never requested against a column that isn't here. */}
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
              loading={predicting}
              loadingText="Predicting..."
            >
              Predict
            </Button>
          </div>
          {progressMessage && <p className="text-xs text-status-active">{progressMessage}</p>}
        </form>
      </Card>

      <ErrorMessage error={error} />
      <RunNotFoundHelp error={error} selection={selection} />

      {result && (
        <>
          {/* Quality context rendered FIRST, same rule as Reports - the
              caveat can never be separated from the numbers that follow. */}
          <div className="mb-6 rounded-md border border-status-caution bg-status-caution-tint p-6">
            <h2 className="mb-2 text-[15px] font-semibold text-status-caution">Data quality context</h2>
            <div className="max-w-[68ch] whitespace-pre-wrap text-sm leading-relaxed text-ink">{result.quality_context_summary ?? ""}</div>
          </div>

          {isRetrieval && (
            <Card title="This looks like a retrieval question, not a prediction">
              <Muted>
                The question was classified as asking for a fact already in the data, not something to model/forecast. Try it on the{" "}
                <Link className="text-brand-600 hover:underline" to="/ask">
                  Ask page
                </Link>{" "}
                instead.
              </Muted>
            </Card>
          )}

          {isFailed && (
            <Card title="Prediction failed unexpectedly">
              <Muted>Something went wrong outside the normal escalation paths. Check the backend logs / GET /models/{result.id} for detail.</Muted>
            </Card>
          )}

          {!isRetrieval && !isFailed && (
            <>
              <div className="mb-6 flex flex-wrap items-center gap-3">
                <span className="text-sm font-medium">Status:</span>
                <Badge value={result.status} />
                {result.task_type && (
                  <>
                    <span className="text-sm font-medium">Task:</span>
                    <Badge value={result.task_type} />
                  </>
                )}
                {result.model_family && (
                  <>
                    <span className="text-sm font-medium">Model:</span>
                    <Badge value={result.model_family} />
                  </>
                )}
              </div>

              {/* The "no model beat the baseline" (and every other
                  escalation) outcome is a first-class, honest answer here -
                  not styled or worded as an error state. */}
              {isEscalated && (
                <Card title="Escalated - no trustworthy answer to report" className="border-status-caution">
                  <p className="mb-2 text-sm text-ink">
                    {result.escalation_reason ? ESCALATION_LABELS[result.escalation_reason] ?? result.escalation_reason : "Escalated."}
                  </p>
                  {result.escalation_detail && <p className="text-sm text-ink-muted">{result.escalation_detail}</p>}
                  {canAppealToApprovals && (
                    <p className="mt-3 text-sm">
                      <Link className="text-brand-600 hover:underline" to="/approvals">
                        View in Approvals
                      </Link>{" "}
                      - some escalation reasons still leave behind a fully trained, scored model a human can accept anyway.
                    </p>
                  )}
                </Card>
              )}

              {result.out_of_sample_metric && (
                <Card title="Out-of-sample performance">
                  <div className="flex flex-wrap gap-6 text-sm">
                    <div>
                      <span className="font-medium text-ink-muted">{result.out_of_sample_metric}:</span>{" "}
                      <span className="font-mono">{result.out_of_sample_score?.toFixed(4)}</span>
                    </div>
                    {/* The comparison IS the point - baseline is rendered
                        directly next to the model's own score, never on a
                        separate screen. */}
                    {result.baseline_scores.map((b) => (
                      <div key={b.model_family}>
                        <span className="font-medium text-ink-muted">
                          baseline ({b.model_family}):
                        </span>{" "}
                        <span className="font-mono">{b.out_of_sample_score.toFixed(4)}</span>
                      </div>
                    ))}
                  </div>
                  {result.candidate_scores.length > 1 && (
                    <div className="overflow-x-auto">
                    <table className="mt-4 w-full text-left text-sm">
                      <thead>
                        <tr className="border-b border-border bg-surface-sunken text-ink-muted">
                          <th className="py-1 px-3 font-medium">Candidate</th>
                          <th className="py-1 px-3 text-right font-medium">Score</th>
                        </tr>
                      </thead>
                      <tbody>
                        {result.candidate_scores.map((c) => (
                          <tr key={c.model_family} className="border-b border-border">
                            <td className="py-1 px-3">{c.model_family}</td>
                            <td className="py-1 px-3 text-right font-mono">{c.out_of_sample_score.toFixed(4)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    </div>
                  )}
                  {result.prediction_interval && result.prediction_interval.lower != null && (
                    <p className="mt-3 text-sm text-ink-muted">
                      Prediction interval: <span className="font-mono">{result.prediction_interval.lower.toFixed(2)}</span> to{" "}
                      <span className="font-mono">{result.prediction_interval.upper?.toFixed(2)}</span>
                      {result.prediction_interval.confidence_level != null &&
                        ` (${Math.round(result.prediction_interval.confidence_level * 100)}%)`}
                    </p>
                  )}
                  {result.class_distribution && (
                    <p className="mt-3 text-sm text-ink-muted">
                      Majority class share: {(result.class_distribution.majority_class_share * 100).toFixed(1)}% -{" "}
                      {JSON.stringify(result.class_distribution.class_counts)}
                    </p>
                  )}
                </Card>
              )}

              {/* Deterministic chart, built entirely from the returned
                  data - no LLM involved in producing or choosing it, same
                  rule app/narrative/charts.py's report charts follow. */}
              {forecastSeries && (forecastSeries.historical.length > 0 || forecastSeries.forecast.length > 0) && (
                <Card title="Forecast chart">
                  <ForecastChart series={forecastSeries} />
                </Card>
              )}

              {result.excluded_features.length > 0 && (
                <Card title="Excluded features" subtitle="Dropped before training, and why - the leakage-prevention evidence, not a black box.">
                  <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead>
                      <tr className="border-b border-border bg-surface-sunken text-ink-muted">
                        <th className="py-1 px-3 font-medium">Column</th>
                        <th className="py-1 px-3 font-medium">Reason</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.excluded_features.map((f) => (
                        <tr key={f.column} className="border-b border-border">
                          <td className="py-1 px-3 font-mono text-xs text-ink-faint">{f.column}</td>
                          <td className="py-1 px-3">{f.reason}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  </div>
                </Card>
              )}

              {result.feature_associations.length > 0 && (
                <Card title="Feature associations" subtitle="Association, never cause - from the winning model's fitted coefficients/importances.">
                  <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead>
                      <tr className="border-b border-border bg-surface-sunken text-ink-muted">
                        <th className="py-1 px-3 font-medium">Column</th>
                        <th className="py-1 px-3 text-right font-medium">Association strength</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.feature_associations.map((f) => (
                        <tr key={f.column} className="border-b border-border">
                          <td className="py-1 px-3 font-mono text-xs text-ink-faint">{f.column}</td>
                          <td className="py-1 px-3 text-right font-mono">{f.association_strength.toFixed(4)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  </div>
                </Card>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}
