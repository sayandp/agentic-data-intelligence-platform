import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import {
  deckDownloadUrl,
  fetchDeckContents,
  fetchDeckExportStatus,
  pollDeckExport,
  startDeckExport,
} from "../api/client";
import type { DeckContents, DeckExportStatus } from "../api/client";
import { RunNotFoundHelp, RunPicker, formatWhen, useRunSelection } from "../components/RunPicker";
import { Button, Card, ErrorMessage, Muted } from "../components/ui";

// The deck covers the report, the analytics, the model and the audit trail,
// so it never belonged to the Reports page - it is not a view of one
// screen's data. Its own destination, on the same RunPicker every other
// run-scoped screen uses, so no id is ever typed.

function SectionRow({ section, state, detail }: { section: string; state: string; detail: string }) {
  const hasContent = state === "content";
  return (
    <li className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-border py-2 last:border-b-0">
      {/* The state is carried by the word, not by the colour - the dot is a
          second channel, never the only one. */}
      <span className="flex min-w-0 items-baseline gap-2">
        <span
          aria-hidden="true"
          className={`inline-block h-2 w-2 shrink-0 translate-y-[-1px] rounded-full ${
            hasContent ? "bg-brand-600" : "bg-border-strong"
          }`}
        />
        <span className="font-medium text-ink">{section}</span>
      </span>
      <span className={`text-sm ${hasContent ? "text-ink-muted" : "text-ink-faint"}`}>
        {hasContent ? "included" : "explanatory placeholder"} &middot; {detail}
      </span>
    </li>
  );
}

export default function ExportPage() {
  const [params, setParams] = useSearchParams();
  const selection = useRunSelection(params.get("run") ?? "");
  const { runRef } = selection;

  const [contents, setContents] = useState<DeckContents | null>(null);
  const [contentsError, setContentsError] = useState<unknown>(null);
  const [existing, setExisting] = useState<{ bytes: number; generated_at: string } | null>(null);
  const [state, setState] = useState<"idle" | "working" | "failed">("idle");
  const [elapsed, setElapsed] = useState("0s");
  const [error, setError] = useState<string | null>(null);

  // Keep ?run= in step with the picker so this page is linkable and a
  // reload does not lose the selection.
  useEffect(() => {
    if (!runRef) return;
    if (params.get("run") === runRef) return;
    const next = new URLSearchParams(params);
    next.set("run", runRef);
    setParams(next, { replace: true });
    // `params` is a fresh object every render - depending on it here would
    // loop. runRef is the value that actually drives this.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runRef]);

  const refresh = useCallback((ref: string) => {
    if (!ref) {
      setContents(null);
      setExisting(null);
      return;
    }
    setContentsError(null);
    fetchDeckContents(ref)
      .then(setContents)
      .catch((err) => {
        setContents(null);
        setContentsError(err);
      });
    fetchDeckExportStatus(ref)
      .then((s: DeckExportStatus) => setExisting(s.existing ?? null))
      .catch(() => setExisting(null));
  }, []);

  useEffect(() => {
    refresh(runRef.trim());
  }, [runRef, refresh]);

  async function build() {
    const ref = runRef.trim();
    if (!ref) return;
    setState("working");
    setError(null);
    setElapsed("0s");
    try {
      await startDeckExport(ref);
      const done = await pollDeckExport(ref, (ms) => setElapsed(`${Math.round(ms / 1000)}s`));
      if (done.state === "ready") {
        setState("idle");
        setExisting({ bytes: done.bytes ?? 0, generated_at: done.generated_at ?? new Date().toISOString() });
      } else {
        setState("failed");
        setError(done.error ?? "the deck could not be generated");
      }
    } catch (err) {
      setState("failed");
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const included = contents?.sections.filter((s) => s.state === "content").length ?? 0;
  const placeholders = contents?.sections.filter((s) => s.state === "placeholder").length ?? 0;

  return (
    <div>
      <h1 className="mb-6 text-[22px] font-semibold text-ink">Export</h1>

      <Card>
        <div className="flex flex-col gap-4">
          <RunPicker selection={selection} idPrefix="export" />
          <Muted>
            One .pptx covering this run end to end &mdash; the report and its charts, the business analytics and the
            analyses that did not apply, the model results, and the audit trail. Every slide is rendered from what the
            run already recorded; nothing is written for the deck.
          </Muted>
        </div>
      </Card>

      <ErrorMessage error={contentsError} />
      <RunNotFoundHelp error={contentsError} selection={selection} />

      {contents && (
        <Card
          title="What this deck will contain"
          subtitle={`${included} section(s) with this run's results, ${placeholders} explanatory placeholder(s)`}
          className="mt-6"
        >
          <ul className="mt-1">
            {contents.sections.map((s) => (
              <SectionRow key={s.section} section={s.section} state={s.state} detail={s.detail} />
            ))}
          </ul>
          <p className="mt-3 text-xs text-ink-faint">
            A section with no results still ships, carrying the sentence that says why. Dropping it would let a reader
            assume the work was never attempted.
          </p>
        </Card>
      )}

      {runRef.trim() && (
        <Card className="mt-6">
          <div className="flex flex-wrap items-center gap-3">
            <Button onClick={build} loading={state === "working"} loadingText={`Building deck ${elapsed}...`}>
              {existing ? "Rebuild deck" : "Build deck"}
            </Button>
            {state === "working" && (
              <span className="text-sm text-ink-muted">
                Rendering every chart through a headless browser &mdash; this takes a few seconds per chart.
              </span>
            )}
          </div>

          {/* Any deck already on disk is offered whether or not this session
              made it. A file you can only catch in the seconds after
              generation is not saved. */}
          {existing && state !== "working" && (
            <div className="mt-4 border-t border-border pt-4">
              <div className="text-sm font-medium text-ink">Deck built for this run</div>
              <p className="mt-1 text-sm text-ink-muted">
                <a className="font-medium text-brand-600 underline" href={deckDownloadUrl(runRef.trim())} download>
                  Download .pptx
                </a>{" "}
                <span className="text-ink-faint">
                  ({Math.max(1, Math.round(existing.bytes / 1024))} KB, built {formatWhen(existing.generated_at)})
                </span>
              </p>
            </div>
          )}
          {!existing && state !== "working" && (
            <p className="mt-3 text-sm text-ink-faint">No deck has been built for this run yet.</p>
          )}
          {state === "failed" && error && <p className="mt-3 text-sm text-status-negative">{error}</p>}
        </Card>
      )}
    </div>
  );
}
