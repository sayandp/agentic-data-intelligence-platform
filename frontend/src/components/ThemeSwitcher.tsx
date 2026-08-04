import { useEffect, useState } from "react";
import { THEMES, THEME_LABELS, applyTheme, persistTheme, resolveInitialTheme, type Theme } from "../lib/theme";

// A labelled control, not a mystery icon: three named themes in a plain
// <select>, so a reader can see what they are choosing and what they are
// currently on without opening anything.
//
// The attribute is already set before first paint by the inline script in
// index.html; this component only reads that decision back and owns changes
// from here on. It renders no theme-specific markup itself - switching is
// one attribute swap, never a re-render of styled variants.
export default function ThemeSwitcher() {
  const [theme, setTheme] = useState<Theme>(() => resolveInitialTheme());

  // Keeps the attribute authoritative even if something else changed it
  // (a test, devtools, a second tab writing storage).
  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  function choose(next: Theme) {
    setTheme(next);
    applyTheme(next);
    persistTheme(next);
  }

  return (
    <div className="px-3 pb-4">
      <label htmlFor="theme-select" className="mb-1 block px-3 text-xs font-medium text-nav-ink">
        Theme
      </label>
      <select
        id="theme-select"
        value={theme}
        onChange={(e) => choose(e.target.value as Theme)}
        className="w-full rounded-sm border border-white/15 bg-nav-active px-3 py-1.5 text-sm text-nav-ink-active"
      >
        {THEMES.map((name) => (
          <option key={name} value={name}>
            {THEME_LABELS[name]}
          </option>
        ))}
      </select>
    </div>
  );
}
