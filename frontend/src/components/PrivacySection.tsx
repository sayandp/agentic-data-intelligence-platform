import { useState } from "react";
import { apiFetch, apiPostJson } from "../api/client";
import { Badge, Card, Muted } from "./ui";

// What is masked before leaving for a third-party model, on WHICH path, and
// what a person has cleared.
//
// The states are kept visually distinct on purpose. "Masked everywhere" is a
// guarantee; "masked on the strict paths only" is a guarantee with a stated
// exception; "marked safe by a person" is somebody's decision, attributed.
// Conflating them would let a reader believe a column was protected on a path
// where its values are deliberately sent in the clear.

export type PrivacyConfidence = "confirmed" | "not_personal" | "high" | "candidate";

export interface PrivacyClassificationRecord {
  classifications: {
    column: string;
    kind: string;
    confidence: PrivacyConfidence;
    matched_fraction: number | null;
    reason: string;
    redacted_strict: boolean;
    redacted_permissive: boolean;
    verified_personal: boolean;
    confirmed_by?: string | null;
    confirmed_at?: string | null;
    detail?: Record<string, unknown>;
  }[];
  redacted_strict: string[];
  redacted_permissive: string[];
  policy_by_path: Record<string, string>;
  candidate_columns: string[];
  not_personal_columns: string[];
  parameters?: Record<string, unknown>;
}

function kindLabel(kind: string): string {
  return kind.replace(/_/g, " ");
}

function pathsFor(policyByPath: Record<string, string>, policy: string): string[] {
  return Object.entries(policyByPath)
    .filter(([, value]) => value === policy)
    .map(([path]) => path);
}

export function PrivacySection({
  privacy,
  runRef,
  onChange,
}: {
  privacy: PrivacyClassificationRecord | null | undefined;
  runRef?: string;
  onChange?: (next: PrivacyClassificationRecord) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!privacy) {
    // A run from before detection existed. Saying so beats an empty panel a
    // reader would take for "nothing personal here".
    return (
      <Card title="Privacy" subtitle="Personal data detected in this run's columns.">
        <Muted>
          This run completed before personal-data detection existed, so nothing was classified and nothing was masked.
          Re-ingest the source to classify it.
        </Muted>
      </Card>
    );
  }

  const policyByPath = privacy.policy_by_path ?? {};
  const strictPaths = pathsFor(policyByPath, "strict");
  const permissivePaths = pathsFor(policyByPath, "permissive");

  // Masked on every path, because the machine can prove what it is.
  const everywhere = privacy.classifications.filter((c) => c.redacted_permissive);
  // Masked only where over-redacting was measured to cost nothing.
  const strictOnly = privacy.classifications.filter((c) => c.redacted_strict && !c.redacted_permissive);
  const cleared = privacy.classifications.filter((c) => c.confidence === "not_personal");

  async function decide(column: string, decision: "not_personal") {
    if (!runRef) return;
    const markedBy = window.prompt(
      `Who is recording that "${column}" holds no personal data?\n\n` +
        "This system has no accounts, so this is stored as an attribution, not a verified identity.",
    );
    if (markedBy === null) return;

    setBusy(column);
    setError(null);
    try {
      const next = await apiPostJson<PrivacyClassificationRecord>(`/privacy/${runRef}/decisions`, {
        column,
        decision,
        marked_by: markedBy.trim() || null,
      });
      onChange?.(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  async function withdraw(column: string) {
    if (!runRef) return;
    setBusy(column);
    setError(null);
    try {
      const next = await apiFetch<PrivacyClassificationRecord>(`/privacy/${runRef}/decisions/${encodeURIComponent(column)}`, {
        method: "DELETE",
      });
      onChange?.(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card
      title="Privacy"
      subtitle="Detected deterministically at ingest. No language model is involved in finding personal data."
    >
      {privacy.classifications.length === 0 && (
        <Muted>No column matched a personal-data pattern, and no column name raised a question.</Muted>
      )}

      {error && (
        <p className="mb-4 border border-status-negative px-3 py-2 text-sm text-status-negative">{error}</p>
      )}

      {everywhere.length > 0 && (
        <div className="mb-5">
          <div className="text-label uppercase text-ink-faint">Masked on every model call</div>
          <ul className="mt-2">
            {everywhere.map((c) => (
              <li key={c.column} className="flex flex-wrap items-baseline gap-x-3 border-b border-border py-2 last:border-b-0">
                <Badge value="masked" tone="positive" />
                <span className="font-mono text-ink">{c.column}</span>
                <span className="text-sm text-ink-muted">{kindLabel(c.kind)}</span>
                {c.matched_fraction != null && (
                  <span className="text-sm text-ink-faint">{(c.matched_fraction * 100).toFixed(0)}% of values matched</span>
                )}
                {c.confidence === "confirmed" && (
                  <span className="text-sm text-ink-faint">
                    confirmed by {c.confirmed_by ?? "a person"}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {strictOnly.length > 0 && (
        <div className="mb-5">
          <div className="text-label uppercase text-ink-faint" data-testid="privacy-split">
            Masked on <span data-testid="privacy-strict-paths">{strictPaths.join(", ") || "the strict paths"}</span>{" "}
            &mdash; sent in the clear to{" "}
            <span data-testid="privacy-permissive-paths">{permissivePaths.join(", ") || "no path"}</span>
          </div>
          <p className="mt-1 text-sm text-ink-muted">
            These cannot be verified from their values, so they are masked by default rather than sent on a guess.
            Diagnosis was measured to lose nothing to that masking, so it is applied there. The narrative measurably lost
            findings, so these values reach the report-writing model in the clear. Mark one safe below and it stops being
            masked anywhere.
          </p>
          <ul className="mt-2">
            {strictOnly.map((c) => (
              <li key={c.column} className="border-b border-border py-2 last:border-b-0">
                <div className="flex flex-wrap items-baseline gap-x-3">
                  <Badge value="masked by default" tone="caution" />
                  <span className="font-mono text-ink">{c.column}</span>
                  <span className="text-sm text-ink-muted">possibly {kindLabel(c.kind)}</span>
                  {runRef && (
                    <button
                      type="button"
                      className="text-sm text-brand-600 underline disabled:opacity-50"
                      disabled={busy === c.column}
                      onClick={() => decide(c.column, "not_personal")}
                    >
                      {busy === c.column ? "saving…" : "mark not personal"}
                    </button>
                  )}
                </div>
                <p className="mt-1 text-sm text-ink-faint">{c.reason}</p>
              </li>
            ))}
          </ul>
        </div>
      )}

      {cleared.length > 0 && (
        <div>
          <div className="text-label uppercase text-ink-faint">Marked not personal by a person &mdash; never masked</div>
          <p className="mt-1 text-sm text-ink-muted">
            This system has no accounts, so the name below is an attribution, not a verified identity.
          </p>
          <ul className="mt-2">
            {cleared.map((c) => (
              <li key={c.column} className="flex flex-wrap items-baseline gap-x-3 border-b border-border py-2 last:border-b-0">
                <Badge value="cleared" tone="neutral" />
                <span className="font-mono text-ink">{c.column}</span>
                <span className="text-sm text-ink-muted">
                  marked by {c.confirmed_by ?? "someone unrecorded"}
                  {c.confirmed_at ? ` on ${new Date(c.confirmed_at).toLocaleDateString()}` : ""}
                </span>
                {runRef && (
                  <button
                    type="button"
                    className="text-sm text-brand-600 underline disabled:opacity-50"
                    disabled={busy === c.column}
                    onClick={() => withdraw(c.column)}
                  >
                    {busy === c.column ? "saving…" : "undo"}
                  </button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}
