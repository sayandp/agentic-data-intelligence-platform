import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { apiPostJson, pollPredictStatus } from "../api/client";
import type { ForecastSeries, PredictAck, PredictResult } from "../api/types";
import { Badge, Button, Card, ErrorMessage, Muted } from "../components/ui";
import { loadPlotly } from "../lib/plotly";

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

  const data: unknown[] = [{ x: historicalX, y: historicalY, mode: "lines", name: "Actual", line: { color: "#4f46e5" } }];

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
        fillcolor: "rgba(249,115,22,0.15)",
        name: "Interval",
      }
    );
  }
  data.push({ x: forecastX, y: forecastY, mode: "lines", name: "Forecast", line: { color: "#f97316", dash: "dash" } });

  const layout = { title: "Forecast - actuals vs. forward horizon", xaxis: { title: "Period" }, yaxis: { title: "Value" }, margin: { t: 40 } };
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
          containerRef.current.innerHTML = '<p class="text-sm text-slate-500">Chart could not be rendered.</p>';
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
  const [sourceId, setSourceId] = useState("");
  const [runId, setRunId] = useState("");
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
      const payload: Record<string, string> = { question };
      if (sourceId) payload.source_id = sourceId;
      if (runId) payload.run_id = runId;
      const ack = await apiPostJson<PredictAck>("/predict", payload);
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

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold text-slate-900">Predict</h1>

      <Card>
        <form onSubmit={handlePredict} className="flex flex-col gap-3">
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
            placeholder="Forecast monthly revenue"
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
          />
          <div>
            <Button type="submit" variant="primary" disabled={!question.trim()} loading={predicting} loadingText="Predicting...">
              Predict
            </Button>
          </div>
          {progressMessage && <p className="text-xs text-slate-500">{progressMessage}</p>}
        </form>
      </Card>

      <ErrorMessage error={error} />

      {result && (
        <>
          {/* Quality context rendered FIRST, same rule as Reports - the
              caveat can never be separated from the numbers that follow. */}
          <div className="mb-6 rounded-2xl border border-amber-300 bg-amber-50 p-6">
            <h2 className="mb-2 text-lg font-semibold text-amber-900">Data quality context</h2>
            <pre className="whitespace-pre-wrap text-sm text-amber-900">{result.quality_context_summary ?? ""}</pre>
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
                <Card title="Escalated - no trustworthy answer to report" className="border-amber-200">
                  <p className="mb-2 text-sm text-slate-700">
                    {result.escalation_reason ? ESCALATION_LABELS[result.escalation_reason] ?? result.escalation_reason : "Escalated."}
                  </p>
                  {result.escalation_detail && <p className="text-sm text-slate-500">{result.escalation_detail}</p>}
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
                      <span className="font-medium text-slate-700">{result.out_of_sample_metric}:</span>{" "}
                      <span className="font-mono">{result.out_of_sample_score?.toFixed(4)}</span>
                    </div>
                    {/* The comparison IS the point - baseline is rendered
                        directly next to the model's own score, never on a
                        separate screen. */}
                    {result.baseline_scores.map((b) => (
                      <div key={b.model_family}>
                        <span className="font-medium text-slate-700">
                          baseline ({b.model_family}):
                        </span>{" "}
                        <span className="font-mono">{b.out_of_sample_score.toFixed(4)}</span>
                      </div>
                    ))}
                  </div>
                  {result.candidate_scores.length > 1 && (
                    <table className="mt-4 w-full text-left text-sm">
                      <thead>
                        <tr className="border-b border-slate-200 text-slate-500">
                          <th className="py-1 pr-3 font-medium">Candidate</th>
                          <th className="py-1 font-medium">Score</th>
                        </tr>
                      </thead>
                      <tbody>
                        {result.candidate_scores.map((c) => (
                          <tr key={c.model_family} className="border-b border-slate-100">
                            <td className="py-1 pr-3">{c.model_family}</td>
                            <td className="py-1 font-mono">{c.out_of_sample_score.toFixed(4)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                  {result.prediction_interval && result.prediction_interval.lower != null && (
                    <p className="mt-3 text-sm text-slate-600">
                      Prediction interval: <span className="font-mono">{result.prediction_interval.lower.toFixed(2)}</span> to{" "}
                      <span className="font-mono">{result.prediction_interval.upper?.toFixed(2)}</span>
                      {result.prediction_interval.confidence_level != null &&
                        ` (${Math.round(result.prediction_interval.confidence_level * 100)}%)`}
                    </p>
                  )}
                  {result.class_distribution && (
                    <p className="mt-3 text-sm text-slate-600">
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
                  <table className="w-full text-left text-sm">
                    <thead>
                      <tr className="border-b border-slate-200 text-slate-500">
                        <th className="py-1 pr-3 font-medium">Column</th>
                        <th className="py-1 font-medium">Reason</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.excluded_features.map((f) => (
                        <tr key={f.column} className="border-b border-slate-100">
                          <td className="py-1 pr-3 font-mono text-xs">{f.column}</td>
                          <td className="py-1">{f.reason}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </Card>
              )}

              {result.feature_associations.length > 0 && (
                <Card title="Feature associations" subtitle="Association, never cause - from the winning model's fitted coefficients/importances.">
                  <table className="w-full text-left text-sm">
                    <thead>
                      <tr className="border-b border-slate-200 text-slate-500">
                        <th className="py-1 pr-3 font-medium">Column</th>
                        <th className="py-1 font-medium">Association strength</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.feature_associations.map((f) => (
                        <tr key={f.column} className="border-b border-slate-100">
                          <td className="py-1 pr-3 font-mono text-xs">{f.column}</td>
                          <td className="py-1 font-mono">{f.association_strength.toFixed(4)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </Card>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}
