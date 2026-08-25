import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import ThemeSwitcher from "./ThemeSwitcher";

// One consistent stroke (1.6px, round caps/joins) across every nav icon -
// drawn, not emoji (DESIGN.md: "unicode glyphs standing in for an icon
// system" is refused).
function IconSources() {
  return (
    <svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M2.5 5.5a1 1 0 0 1 1-1H8l1.5 2h7a1 1 0 0 1 1 1v7.5a1 1 0 0 1-1 1h-14a1 1 0 0 1-1-1v-9.5Z" />
    </svg>
  );
}

function IconApprovals() {
  return (
    <svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="10" cy="10" r="7.25" />
      <path d="M7 10.2 9.1 12.3 13.3 8" />
    </svg>
  );
}

function IconReports() {
  return (
    <svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M5.5 2.5h6l3 3v12a1 1 0 0 1-1 1h-8a1 1 0 0 1-1-1v-14a1 1 0 0 1 1-1Z" />
      <path d="M7.5 11h5M7.5 14h5M7.5 8h2" />
    </svg>
  );
}

function IconAudit() {
  return (
    <svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="8.75" cy="8.75" r="5.25" />
      <path d="M16 16l-3.4-3.4" />
    </svg>
  );
}

function IconAsk() {
  return (
    <svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M3 4.5h14v9h-8l-3 3v-3H3v-9Z" />
    </svg>
  );
}

function IconPredict() {
  return (
    <svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M3 15.5 8 9l3.5 3.5L17 5.5" />
      <path d="M12.5 5.5H17v4.5" />
    </svg>
  );
}

function IconAnalytics() {
  return (
    <svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M3 17V9M8 17V4M13 17v-6M18 17v-9" />
    </svg>
  );
}

function IconExport() {
  return (
    <svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M10 2.75v9M6.75 8.5 10 11.75 13.25 8.5" />
      <path d="M3.5 13v3.25a1 1 0 0 0 1 1h11a1 1 0 0 0 1-1V13" />
    </svg>
  );
}

function IconMarketing() {
  return (
    <svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M3 8.5v3a1 1 0 0 0 1 1h2.5L11 16V4L6.5 7.5H4a1 1 0 0 0-1 1Z" />
      <path d="M14.5 7.5a3.5 3.5 0 0 1 0 5" />
    </svg>
  );
}

function IconCompare() {
  return (
    <svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M10 3.25v13.5" />
      <path d="M6.25 6.5H3.5l2.25 4.75L8 6.5H6.25Z" />
      <path d="M14.25 6.5H11.5l2.25 4.75L16 6.5h-1.75Z" />
    </svg>
  );
}

function IconAgriculture() {
  return (
    <svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M10 17V8.5" />
      <path d="M10 11.5C10 9.5 8.4 7.9 6.4 7.9c0 2 1.6 3.6 3.6 3.6Z" />
      <path d="M10 9.6c0-2 1.6-3.6 3.6-3.6 0 2-1.6 3.6-3.6 3.6Z" />
      <path d="M4.5 17h11" />
    </svg>
  );
}

const NAV_ITEMS = [
  { to: "/sources", label: "Sources", Icon: IconSources },
  { to: "/approvals", label: "Approvals", Icon: IconApprovals },
  { to: "/reports", label: "Reports", Icon: IconReports },
  { to: "/audit", label: "Audit", Icon: IconAudit },
  { to: "/compare", label: "Compare", Icon: IconCompare },
  { to: "/ask", label: "Ask", Icon: IconAsk },
  { to: "/predict", label: "Predict", Icon: IconPredict },
  { to: "/analytics", label: "Analytics", Icon: IconAnalytics },
  { to: "/marketing", label: "Marketing", Icon: IconMarketing },
  { to: "/agriculture", label: "Agriculture", Icon: IconAgriculture },
  { to: "/export", label: "Export", Icon: IconExport },
];

export default function Layout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen">
      <aside className="panel flex w-64 shrink-0 flex-col bg-nav-bg text-nav-ink">
        <div className="flex items-center gap-3 px-6 py-6">
          <div className="flex h-9 w-9 items-center justify-center rounded-sm bg-brand-600 text-lg font-bold text-on-accent">A</div>
          <div>
            <div className="text-sm font-semibold text-nav-ink-active">Agentic Data</div>
            <div className="text-xs text-nav-ink">Intelligence Platform</div>
          </div>
        </div>
        <nav className="mt-4 flex flex-col gap-0.5 px-3">
          {NAV_ITEMS.map(({ to, label, Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-sm px-3 py-2 text-sm font-medium transition-colors ${
                  isActive ? "glow-accent bg-nav-active text-nav-ink-active" : "text-nav-ink hover:bg-nav-hover hover:text-nav-ink-active"
                }`
              }
            >
              <Icon />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto">
          <ThemeSwitcher />
          <div className="px-6 pb-6 text-xs text-nav-ink">
            Every screen here calls the same JSON API you can hit with curl - nothing is hidden behind this UI.
          </div>
        </div>
      </aside>
      <div className="flex min-h-screen flex-1 flex-col">
        <main className="flex-1 px-8 py-8">
          <div className="mx-auto max-w-6xl">{children}</div>
        </main>
      </div>
    </div>
  );
}
