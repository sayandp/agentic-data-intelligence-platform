import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { apiFetch } from "../api/client";
import type { RunComparison, ComparisonSection, ComparisonDelta } from "../api/types";
import { Badge, Card, ErrorMessage, Muted, Pager, RunLabel, usePagedList } from "../components/ui";

// Two runs of one source, side by side.
//
// The design problem here is the opposite of most screens': the dangerous
// output is not an error, it is a plausible number. A delta between figures
// computed on different value bases looks exactly like a real change, so the
// "cannot compare" states are rendered LOUDLY and in place of the numbers,
// never as a footnote under them.

const SECTION_TITLES: Record<string, string> = {
  schema: "Schema",
  data_quality: "Data quality",
  exploration: "Exploration",
  analytics: "Analytics",
  marketing: "Marketing",
  model: "Model",
};

function formatNumber(value: number | null): string {
  if (value === null) return "-";
  if (Number.isInteger(value)) return value.toLocaleString();
  if (Math.abs(value) < 0.01) return value.toExponential(2);
  return value.toLocaleString(undefined, { maximumFractionDigits: 4 });
}

function formatRelative(value: number | null): string {
  // No relative change from zero. Rendering it as 0%, infinity or "new" would
  // each be a different wrong answer, so the cell says so instead.
  if (value === null) return "no relative change from zero";
  return `${value >= 0 ? "+" : ""}${(value * 100).toFixed(1)}%`;
}

function DeltaRow({ delta }: { delta: ComparisonDelta }) {
  return (
    <tr className="border-b border-border last:border-b-0">
      <td className="py-2 px-3 text-ink">
        {delta.label}
        {delta.basis && <div className="text-xs text-ink-faint">basis: {delta.basis}</div>}
      </td>
      <td className="py-2 px-3 text-right font-mono text-ink-muted">{formatNumber(delta.before)}</td>
      <td className="py-2 px-3 text-right font-mono text-ink-muted">{formatNumber(delta.after)}</td>
      <td className="py-2 px-3 text-right font-mono text-ink">
        {delta.absolute === null ? "-" : `${delta.absolute >= 0 ? "+" : ""}${formatNumber(delta.absolute)}`}
      </td>
      <td className="py-2 px-3 text-right text-sm text-ink-muted">{formatRelative(delta.relative)}</td>
    </tr>
  );
}

function Section({ section }: { section: ComparisonSection }) {
  const title = SECTION_TITLES[section.name] ?? section.name;

  // Declared here, above the not-comparable early return, because hooks may
  // not sit behind a conditional. `section.name` keys them: this component is
  // reused for every section of every comparison, so a page chosen for the
  // schema section must not carry over to marketing's.
  const changedDeltas = section.deltas.filter((d) => d.changed);
  const deltaPage = usePagedList(changedDeltas, 25, section.name);
  const membershipPage = usePagedList(section.memberships, 15, section.name);

  if (section.comparability !== "comparable") {
    const tone = section.comparability === "not_comparable" ? "negative" : "neutral";
    return (
      <Card title={title}>
        <div className="mb-2">
          <Badge value={section.comparability.replace(/_/g, " ")} tone={tone} />
        </div>
        <p className="text-sm text-ink-muted">{section.reason}</p>
        {section.notes.value_basis_a != null && (
          <p className="mt-2 text-sm text-ink-faint">
            earlier run summed <code className="font-mono">{String(section.notes.value_basis_a)}</code>; later run summed{" "}
            <code className="font-mono">{String(section.notes.value_basis_b)}</code>
          </p>
        )}
      </Card>
    );
  }

  const changed = changedDeltas;
  const unchanged = section.deltas.length - changed.length;

  return (
    <Card title={title}>
      {section.memberships.length > 0 && (
        <div className="mb-4">
          <div className="text-label uppercase text-ink-faint">Present in one run only</div>
          <ul className="mt-2">
            {membershipPage.visible.map((m) => (
              <li key={`${m.side}-${m.label}`} className="flex flex-wrap items-baseline gap-x-3 border-b border-border py-1.5 last:border-b-0">
                <Badge value={m.side === "b_only" ? "appeared" : "disappeared"} tone={m.side === "b_only" ? "active" : "caution"} />
                <span className="text-ink">{m.label}</span>
              </li>
            ))}
          </ul>
          <Pager paged={membershipPage} noun="one-sided item" />
        </div>
      )}

      {changed.length === 0 ? (
        <Muted>
          {section.deltas.length === 0
            ? "Nothing measurable to compare in this section."
            : `No change across ${section.deltas.length} measured value(s).`}
        </Muted>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-border bg-surface-sunken text-ink-muted">
                <th className="py-2 px-3 font-medium">What</th>
                <th className="py-2 px-3 text-right font-medium">Earlier</th>
                <th className="py-2 px-3 text-right font-medium">Later</th>
                <th className="py-2 px-3 text-right font-medium">Change</th>
                <th className="py-2 px-3 text-right font-medium">Relative</th>
              </tr>
            </thead>
            <tbody>
              {deltaPage.visible.map((d) => (
                <DeltaRow key={d.label} delta={d} />
              ))}
            </tbody>
          </table>
          <Pager paged={deltaPage} noun="changed value" />
          {unchanged > 0 && <p className="mt-2 text-sm text-ink-faint">{unchanged} further value(s) did not change.</p>}
        </div>
      )}

      {Array.isArray(section.notes.kind_changes) && section.notes.kind_changes.length > 0 && (
        <div className="mt-4">
          <div className="text-label uppercase text-ink-faint">Column kind changed &mdash; values not compared</div>
          <ul className="mt-2">
            {(section.notes.kind_changes as { finding: string; before: string; after: string }[]).map((k) => (
              <li key={k.finding} className="border-b border-border py-1.5 text-sm text-ink last:border-b-0">
                {k.finding}: <code className="font-mono text-xs">{k.before}</code> &rarr;{" "}
                <code className="font-mono text-xs">{k.after}</code>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}

export default function ComparePage() {
  const [params] = useSearchParams();
  const [runA, setRunA] = useState(params.get("run_a") ?? "");
  const [runB, setRunB] = useState(params.get("run_b") ?? "");
  const [comparison, setComparison] = useState<RunComparison | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);

  async function load(a: string, b: string) {
    if (!a || !b) return;
    setLoading(true);
    setError(null);
    setComparison(null);
    try {
      setComparison(await apiFetch<RunComparison>(`/compare?run_a=${encodeURIComponent(a)}&run_b=${encodeURIComponent(b)}`));
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    const a = params.get("run_a");
    const b = params.get("run_b");
    if (a && b) load(a, b);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div>
      <h1 className="mb-2 text-page-title text-ink">Compare</h1>
      <Muted>
        Two completed runs of the same source, side by side. Computed from each run's stored results at the moment you
        ask &mdash; nothing is cached, so a comparison always reflects the current state of both runs.
      </Muted>

      <div className="my-6 flex flex-wrap items-center gap-3">
        <input
          id="compare-run-a"
          value={runA}
          onChange={(e) => setRunA(e.target.value)}
          placeholder="earlier run (e.g. 17)"
          className="w-56 rounded-sm border border-border-strong px-3 py-1.5 text-sm"
        />
        <input
          id="compare-run-b"
          value={runB}
          onChange={(e) => setRunB(e.target.value)}
          placeholder="later run (e.g. 18)"
          className="w-56 rounded-sm border border-border-strong px-3 py-1.5 text-sm"
        />
        <button
          type="button"
          className="rounded-sm bg-brand-600 px-4 py-1.5 text-sm text-on-accent hover:bg-brand-700 disabled:opacity-50"
          disabled={loading || !runA || !runB}
          onClick={() => load(runA, runB)}
        >
          {loading ? "Comparing…" : "Compare"}
        </button>
      </div>

      <ErrorMessage error={error} />

      {comparison && (
        <>
          <Card title="Runs compared">
            <div className="flex flex-wrap gap-6 text-sm">
              <div>
                <div className="text-label uppercase text-ink-faint">Earlier</div>
                <RunLabel runNumber={comparison.run_a.run_number} runId={comparison.run_a.run_id} />
                <div className="text-ink-faint">{comparison.run_a.completed_at}</div>
              </div>
              <div>
                <div className="text-label uppercase text-ink-faint">Later</div>
                <RunLabel runNumber={comparison.run_b.run_number} runId={comparison.run_b.run_id} />
                <div className="text-ink-faint">{comparison.run_b.completed_at}</div>
              </div>
            </div>
          </Card>

          {!comparison.comparable ? (
            <div className="mt-6">
              <Card title="These runs cannot be compared">
                <p className="text-sm text-ink">{comparison.blocked_reason}</p>
              </Card>
            </div>
          ) : (
            <div className="mt-6 flex flex-col gap-6">
              {comparison.sections.map((section) => (
                <Section key={section.name} section={section} />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
