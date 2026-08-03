import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";

const NAV_ITEMS = [
  { to: "/sources", label: "Sources", icon: "\u{1F4C1}" },
  { to: "/approvals", label: "Approvals", icon: "✅" },
  { to: "/reports", label: "Reports", icon: "\u{1F4C4}" },
  { to: "/audit", label: "Audit", icon: "\u{1F50E}" },
  { to: "/ask", label: "Ask", icon: "\u{1F4AC}" },
  { to: "/predict", label: "Predict", icon: "\u{1F52E}" },
];

export default function Layout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen">
      <aside className="flex w-64 shrink-0 flex-col bg-brand-950 text-brand-100">
        <div className="flex items-center gap-3 px-6 py-6">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand-500 text-lg font-bold text-white">A</div>
          <div>
            <div className="text-sm font-semibold text-white">Agentic Data</div>
            <div className="text-xs text-brand-200">Intelligence Platform</div>
          </div>
        </div>
        <nav className="mt-4 flex flex-col gap-1 px-3">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition-colors ${
                  isActive ? "bg-brand-600 text-white shadow-sm" : "text-brand-200 hover:bg-brand-900 hover:text-white"
                }`
              }
            >
              <span aria-hidden="true">{item.icon}</span>
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto px-6 py-6 text-xs text-brand-200/70">
          Every screen here calls the same JSON API you can hit with curl - nothing is hidden behind this UI.
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
