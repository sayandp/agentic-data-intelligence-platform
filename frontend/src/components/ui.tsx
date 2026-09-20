import { useRef, useState, type ReactNode } from "react";
import type { ToastState } from "../hooks/useToast";

// DESIGN.md's Flat-By-Default Rule: no shadow, a 1px border and a 6px
// radius carry the surface instead of a floating-card illusion. Cards
// never nest inside another card (see the Do's and Don'ts) - a section
// needing internal grouping uses `surface-sunken` or a divider instead.
export function Card({ title, subtitle, children, className = "" }: { title?: string; subtitle?: string; children: ReactNode; className?: string }) {
  return (
    <div className={`panel mb-6 rounded-md border border-border bg-surface p-5 ${className}`}>
      {title && <h2 className="mb-1 text-card-title text-ink">{title}</h2>}
      {subtitle && <p className="mb-4 text-sm text-ink-muted">{subtitle}</p>}
      {children}
    </div>
  );
}

// Fixed status vocabulary (DESIGN.md's Status-Never-Accent Rule) - exactly
// four roles, never the accent color, mapped consistently everywhere a
// status appears. A leading dot carries meaning by shape+color before the
// word is read, per Part 3's "status should be legible at a glance."
const STATUS_ROLES: Record<string, "positive" | "negative" | "caution" | "active" | "neutral"> = {
  completed: "positive",
  answered: "positive",
  resolved: "positive",
  low: "positive",
  failed: "negative",
  rejected: "negative",
  high: "negative",
  awaiting_approval: "caution",
  escalated: "caution",
  running: "active",
  template: "neutral",
  llm: "active",
};

const BADGE_STYLES: Record<string, string> = {
  positive: "bg-status-positive-tint text-status-positive",
  negative: "bg-status-negative-tint text-status-negative",
  caution: "bg-status-caution-tint text-status-caution",
  active: "bg-status-active-tint text-status-active",
  neutral: "bg-surface-sunken text-ink-muted",
};

const DOT_STYLES: Record<string, string> = {
  positive: "bg-status-positive",
  negative: "bg-status-negative",
  caution: "bg-status-caution",
  active: "bg-status-active",
  neutral: "bg-ink-faint",
};

export type BadgeTone = "positive" | "negative" | "caution" | "active" | "neutral";

// `tone` overrides the STATUS_ROLES lookup. Needed because that map encodes
// SEVERITY - "high" is red because a high-severity issue is bad - and the
// same words mean the opposite as CONFIDENCE: a high-confidence role
// detection is the good case. Without an override, the analytics roles line
// painted a confident detection red and an unusable one green.
export function Badge({ value, tone }: { value: string | null | undefined; tone?: BadgeTone }) {
  if (!value) return null;
  const role = tone ?? STATUS_ROLES[value] ?? "neutral";
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${BADGE_STYLES[role]}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${DOT_STYLES[role]}`} aria-hidden="true" />
      {value}
    </span>
  );
}

// An indeterminate spinner, deliberately - these are single request/response
// calls with no server-side progress events to report a real percentage
// from. A fake 0-100% bar would be lying about precision it doesn't have;
// this tells the only two true things: working, and (via the button's own
// label swap) what it's currently doing.
export function Spinner({ className = "" }: { className?: string }) {
  return (
    <svg className={`h-4 w-4 animate-spin ${className}`} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
    </svg>
  );
}

export function Button({
  children,
  onClick,
  variant = "default",
  type = "button",
  disabled,
  loading = false,
  loadingText,
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "default" | "primary" | "danger";
  type?: "button" | "submit";
  disabled?: boolean;
  loading?: boolean;
  loadingText?: string;
}) {
  const styles = {
    default: "border border-border-strong bg-surface text-ink hover:bg-surface-sunken",
    primary: "glow-accent bg-brand-600 text-on-accent hover:bg-brand-700",
    // Reserved for the single irreversible action (discard run) - its color
    // alone signals "this one is different" before a reader reaches the
    // label. Never used for an ordinary reject/dismiss decision.
    danger: "border border-status-negative bg-surface text-status-negative hover:bg-status-negative-tint",
  }[variant];
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled || loading}
      className={`inline-flex items-center gap-2 rounded-sm px-3.5 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${styles}`}
    >
      {loading && <Spinner />}
      {loading ? loadingText ?? "Working..." : children}
    </button>
  );
}

// Shared by every "here's an id, with a copy button" control below -
// CopyableId (a source id, a raw run id) and RunLabel (Run #N, backed by
// the same full UUID for API use). One clipboard-write-with-feedback
// implementation, never duplicated per component.
function useCopyFeedback(): [boolean, (text: string) => void] {
  const [copied, setCopied] = useState(false);
  function copy(text: string) {
    navigator.clipboard
      .writeText(text)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      })
      .catch(() => {
        // Clipboard API unavailable or permission denied - the id/label
        // text itself is still visible on screen, so this fails silently
        // rather than surfacing an error for what is a convenience action.
      });
  }
  return [copied, copy];
}

function CopyIcon({ copied }: { copied: boolean }) {
  return copied ? (
    <svg className="h-3.5 w-3.5 text-emerald-600" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
      <path fillRule="evenodd" d="M16.7 5.3a1 1 0 010 1.4l-8 8a1 1 0 01-1.4 0l-4-4a1 1 0 111.4-1.4L8 12.6l7.3-7.3a1 1 0 011.4 0z" clipRule="evenodd" />
    </svg>
  ) : (
    <svg className="h-3.5 w-3.5" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
      <path d="M7 3a2 2 0 00-2 2v9a2 2 0 002 2h6a2 2 0 002-2V7.414a2 2 0 00-.586-1.414l-2.414-2.414A2 2 0 0010.586 3H7z" />
      <path d="M3 7a2 2 0 012-2v9a3 3 0 003 3h5a2 2 0 01-2 2H5a3 3 0 01-3-3V7z" />
    </svg>
  );
}

// Every id in the UI is a full UUID. Shows the complete id as plain,
// selectable text by default - a human needs to actually READ or paste the
// whole thing elsewhere (a log line, a curl command, another tab), and
// truncating it behind a hover tooltip or a tiny copy icon is friction, not
// a feature. The underlying id is UNCHANGED (still the real primary key) -
// this is display only. Pass `full={false}` for the rare spot (a dense
// table column) where only recognizing/comparing the first 8 chars matters
// and showing the whole UUID would genuinely break the layout.
//
// NOTE: for a RUN specifically, prefer RunLabel below - Run.run_number
// (dashboard UX pass, Part 1) is the primary identifier a human should see
// and type; this component (full UUID text) is for ids that have no such
// short alias, like a source id.
export function CopyableId({ id, className = "", full = true }: { id: string; className?: string; full?: boolean }) {
  const [copied, copy] = useCopyFeedback();

  return (
    <span className={`inline-flex items-center gap-1 ${className}`}>
      <code className="select-all font-mono text-xs text-ink-faint" title={full ? undefined : id}>
        {full ? id : id.slice(0, 8)}
      </code>
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          copy(id);
        }}
        aria-label={copied ? "Copied" : "Copy full ID"}
        title={copied ? "Copied" : "Copy full ID"}
        className="text-ink-faint hover:text-ink"
      >
        <CopyIcon copied={copied} />
      </button>
    </span>
  );
}

// Dashboard UX pass, Part 1 (replaces the earlier id-prefix scheme): "Run
// #17" is the primary, human-typeable identifier for a run everywhere one
// is shown - short, memorable, and exactly what GET /reports and GET /audit
// now accept in their load boxes (app/id_lookup.py::resolve_run). The full
// UUID is still available on hover (title) and via the copy icon, for
// pasting into a curl command or another API call - never the primary
// display, since a human almost never needs it directly anymore.
// `runNumber` is null only for a Run that predates this feature and hasn't
// been backfilled yet (should not happen once app/db.py's migration has run
// once) - falls back to the old short-UUID form rather than showing
// "Run #null".
export function RunLabel({ runNumber, runId, className = "" }: { runNumber: number | null; runId: string; className?: string }) {
  const [copied, copy] = useCopyFeedback();
  const label = runNumber != null ? `Run #${runNumber}` : `Run ${runId.slice(0, 8)}`;

  return (
    <span className={`inline-flex items-center gap-1 ${className}`}>
      <span className="font-medium text-ink" title={runId}>
        {label}
      </span>
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          copy(runId);
        }}
        aria-label={copied ? "Copied" : "Copy full run ID (for API use)"}
        title={copied ? "Copied" : "Copy full run ID (for API use)"}
        className="text-ink-faint hover:text-ink"
      >
        <CopyIcon copied={copied} />
      </button>
    </span>
  );
}

export function ErrorMessage({ error }: { error: unknown }) {
  if (!error) return null;
  const message = error instanceof Error ? error.message : String(error);
  return (
    <p className="rounded-sm border border-status-negative bg-status-negative-tint px-3 py-2 text-sm font-medium text-status-negative">{message}</p>
  );
}

export function CodeBlock({ children }: { children: string }) {
  return <pre className="panel overflow-x-auto rounded-md bg-code p-4 font-mono text-sm text-code-ink">{children}</pre>;
}

export function Muted({ children }: { children: ReactNode }) {
  return <p className="text-sm text-ink-muted">{children}</p>;
}

// Fixed-position, self-dismissing (see useToast) confirmation that an
// action actually happened - specifically for the case where a resolved
// item just disappears from a list with no other visible trace that
// anything occurred at all. This is genuinely overlaying content (the one
// exception to Flat-By-Default), so it keeps a small ambient shadow.
export function Toast({ toast, onDismiss }: { toast: ToastState | null; onDismiss: () => void }) {
  if (!toast) return null;
  const styles = toast.kind === "success" ? "bg-status-positive text-on-accent" : "bg-status-negative text-on-accent";
  return (
    <div className={`panel fixed right-6 bottom-6 z-50 flex max-w-sm items-start gap-3 rounded-md px-4 py-3 shadow-float ${styles}`} role="status">
      <span className="text-sm font-medium">{toast.message}</span>
      <button onClick={onDismiss} aria-label="Dismiss" className="ml-2 text-on-accent/80 hover:text-on-accent">
        &times;
      </button>
    </div>
  );
}

// PAGINATION. Added because the result lists are not bounded by anything:
// measured on 7,680 rows of district-crop statistics, one agriculture run
// produced 1,328 findings (939 warnings, 388 improvements) and 655 scope
// chips. Rendered whole, a single card ran for thousands of rows and the
// only way to learn how much was there was to scroll to the end.
//
// Two separate staleness bugs are possible here, and each gets its own
// structural guard rather than a check:
//
//  1. The list is REPLACED under the reader (a different ad set is filtered,
//     a different run is loaded). A page number from the previous list is
//     meaningless against the new one. So the page is stored TOGETHER WITH
//     the identity of the list it belongs to, and read back only when that
//     identity still matches. A page from another list is not something this
//     state can return - which is stronger than remembering to reset it, and
//     unlike an effect it cannot render one frame of the wrong page first.
//
//  2. The list SHRINKS without changing identity (a refetch returns fewer
//     rows). Then page 40 of a 3-item list would render empty, with nothing
//     on screen to say that anything had ever been there. So the page is
//     also clamped to the list's real length at read time.
//
// Guard (2) is DEFENCE IN DEPTH, not a tested guarantee. Falsification
// (scripts/falsify-pagination.mjs) removes it and no test fails: while guard
// (1) stands, every route a reader can take to a too-large page changes the
// list's identity too, and guard (1) has already reset the page by then. It
// is kept because it makes the invalid state unrepresentable rather than
// merely unreached - but it is recorded here as unproven instead of being
// claimed as covered.
//
// `total` is deliberately part of the return and always displayed. This
// platform's premise is that every warning reaches a person; a pager that
// showed "10 warnings" while hiding 929 more would quietly break that, so
// the full count is stated on screen whenever a list is paged.
export type PagedList<T> = {
  visible: T[];
  page: number;
  pageCount: number;
  total: number;
  firstIndex: number;
  lastIndex: number;
  pageSize: number;
  setPage: (page: number) => void;
};

export function usePagedList<T>(items: T[], pageSize: number, listKey: string = ""): PagedList<T> {
  // The page and the list it was chosen for, stored as one value. See (1).
  const [chosen, setChosen] = useState({ key: listKey, page: 0 });

  const pageCount = Math.max(1, Math.ceil(items.length / pageSize));
  const requested = chosen.key === listKey ? chosen.page : 0;
  const page = Math.min(Math.max(requested, 0), pageCount - 1); // see (2)

  const firstIndex = page * pageSize;
  const visible = items.slice(firstIndex, firstIndex + pageSize);

  function setPage(next: number) {
    setChosen({ key: listKey, page: Math.min(Math.max(next, 0), pageCount - 1) });
  }

  return {
    visible,
    page,
    pageCount,
    total: items.length,
    firstIndex,
    lastIndex: firstIndex + visible.length,
    pageSize,
    setPage,
  };
}

// Renders NOTHING when everything already fits. A list of four ad sets is
// not improved by a disabled "Page 1 of 1" and a count of four it can see.
export function Pager<T>({ paged, noun }: { paged: PagedList<T>; noun: string }) {
  // The Pager scrolls, not the caller: paging from the bottom of a long list
  // would otherwise leave the reader at the END of the next page, looking at
  // its last row before its first. Owning it here means no call site has to
  // thread a ref through to get the behaviour right.
  const self = useRef<HTMLDivElement | null>(null);

  function go(next: number) {
    paged.setPage(next);
    self.current?.closest(".panel")?.scrollIntoView({ block: "nearest" });
  }

  if (paged.total <= paged.pageSize) return null;

  const plural = paged.total === 1 ? noun : `${noun}s`;
  const atStart = paged.page === 0;
  const atEnd = paged.page >= paged.pageCount - 1;

  return (
    <div ref={self} className="mt-3 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-3">
      {/* The total is the point of this line, not the range. */}
      <p className="text-sm text-ink-muted" role="status" data-testid="pager-status">
        Showing{" "}
        <span className="font-medium text-ink">
          {paged.firstIndex + 1}&ndash;{paged.lastIndex}
        </span>{" "}
        of <span className="font-medium text-ink">{paged.total}</span> {plural}
      </p>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => go(paged.page - 1)}
          disabled={atStart}
          aria-label={`Previous page of ${plural}`}
          data-testid="pager-prev"
          className="rounded-sm border border-border-strong px-2.5 py-1 text-sm text-ink hover:bg-surface-sunken disabled:cursor-not-allowed disabled:opacity-40"
        >
          &larr; Prev
        </button>
        <span className="text-sm tabular-nums text-ink-muted" data-testid="pager-position">
          Page {paged.page + 1} of {paged.pageCount}
        </span>
        <button
          type="button"
          onClick={() => go(paged.page + 1)}
          disabled={atEnd}
          aria-label={`Next page of ${plural}`}
          data-testid="pager-next"
          className="rounded-sm border border-border-strong px-2.5 py-1 text-sm text-ink hover:bg-surface-sunken disabled:cursor-not-allowed disabled:opacity-40"
        >
          Next &rarr;
        </button>
      </div>
    </div>
  );
}
