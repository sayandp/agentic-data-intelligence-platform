import { useState, type ReactNode } from "react";
import type { ToastState } from "../hooks/useToast";

// DESIGN.md's Flat-By-Default Rule: no shadow, a 1px border and a 6px
// radius carry the surface instead of a floating-card illusion. Cards
// never nest inside another card (see the Do's and Don'ts) - a section
// needing internal grouping uses `surface-sunken` or a divider instead.
export function Card({ title, subtitle, children, className = "" }: { title?: string; subtitle?: string; children: ReactNode; className?: string }) {
  return (
    <div className={`mb-6 rounded-md border border-border bg-white p-5 ${className}`}>
      {title && <h2 className="mb-1 text-[15px] font-semibold text-ink">{title}</h2>}
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

export function Badge({ value }: { value: string | null | undefined }) {
  if (!value) return null;
  const role = STATUS_ROLES[value] ?? "neutral";
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
    default: "border border-border-strong bg-white text-ink hover:bg-surface-sunken",
    primary: "bg-brand-600 text-white hover:bg-brand-700",
    // Reserved for the single irreversible action (discard run) - its color
    // alone signals "this one is different" before a reader reaches the
    // label. Never used for an ordinary reject/dismiss decision.
    danger: "border border-status-negative bg-white text-status-negative hover:bg-status-negative-tint",
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
  return <pre className="overflow-x-auto rounded-md bg-ink p-4 font-mono text-sm text-white">{children}</pre>;
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
  const styles = toast.kind === "success" ? "bg-status-positive text-white" : "bg-status-negative text-white";
  return (
    <div className={`fixed right-6 bottom-6 z-50 flex max-w-sm items-start gap-3 rounded-md px-4 py-3 shadow-md ${styles}`} role="status">
      <span className="text-sm font-medium">{toast.message}</span>
      <button onClick={onDismiss} aria-label="Dismiss" className="ml-2 text-white/80 hover:text-white">
        &times;
      </button>
    </div>
  );
}
