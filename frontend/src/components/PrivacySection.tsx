import { Badge, Card, Muted } from "./ui";

// What was classified as personal, what was masked before leaving for a
// third-party model, and what is still waiting on a human decision.
//
// The three states are kept visually distinct on purpose: "redacted" is a
// guarantee, "candidate" is an open question, and conflating them would let
// a reader believe a name column was protected when it was not.

export interface PrivacyClassificationRecord {
  classifications: {
    column: string;
    kind: string;
    confidence: "confirmed" | "high" | "candidate";
    matched_fraction: number | null;
    reason: string;
    redactable: boolean;
    detail?: Record<string, unknown>;
  }[];
  redactable_columns: string[];
  candidate_columns: string[];
  parameters?: Record<string, unknown>;
}

function kindLabel(kind: string): string {
  return kind.replace(/_/g, " ");
}

export function PrivacySection({ privacy }: { privacy: PrivacyClassificationRecord | null | undefined }) {
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

  const redacted = privacy.classifications.filter((c) => c.redactable);
  const candidates = privacy.classifications.filter((c) => c.confidence === "candidate");

  return (
    <Card
      title="Privacy"
      subtitle="Detected deterministically at ingest. No language model is involved in finding personal data."
    >
      {privacy.classifications.length === 0 && (
        <Muted>No column matched a personal-data pattern, and no column name raised a question.</Muted>
      )}

      {redacted.length > 0 && (
        <div className="mb-5">
          <div className="text-label uppercase text-ink-faint">Masked before any model call</div>
          <ul className="mt-2">
            {redacted.map((c) => (
              <li key={c.column} className="flex flex-wrap items-baseline gap-x-3 border-b border-border py-2 last:border-b-0">
                <Badge value="redacted" tone="positive" />
                <span className="font-mono text-ink">{c.column}</span>
                <span className="text-sm text-ink-muted">{kindLabel(c.kind)}</span>
                {c.matched_fraction != null && (
                  <span className="text-sm text-ink-faint">{(c.matched_fraction * 100).toFixed(0)}% of values matched</span>
                )}
                {c.confidence === "confirmed" && <span className="text-sm text-ink-faint">confirmed by a person</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {candidates.length > 0 && (
        <div>
          <div className="text-label uppercase text-ink-faint">Awaiting a decision &mdash; NOT masked</div>
          <p className="mt-1 text-sm text-ink-muted">
            These cannot be verified from their values, so nothing is redacted on this basis. Their values DO leave the
            system on a model call until someone confirms them.
          </p>
          <ul className="mt-2">
            {candidates.map((c) => (
              <li key={c.column} className="border-b border-border py-2 last:border-b-0">
                <div className="flex flex-wrap items-baseline gap-x-3">
                  <Badge value="candidate" tone="caution" />
                  <span className="font-mono text-ink">{c.column}</span>
                  <span className="text-sm text-ink-muted">possibly {kindLabel(c.kind)}</span>
                </div>
                <p className="mt-1 text-sm text-ink-faint">{c.reason}</p>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}
