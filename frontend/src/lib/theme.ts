// The whole theme mechanism: a single `data-theme` attribute on <html>.
//
// Nothing else in the app branches on the theme. Components use utility
// classes bound to CSS custom properties (src/index.css), so swapping the
// attribute re-colors everything at once - there is no per-component
// conditional rendering to keep in sync, and no component can drift out of
// a theme by forgetting to handle one.

export const THEMES = ["default", "dark", "aurora"] as const;
export type Theme = (typeof THEMES)[number];

export const STORAGE_KEY = "adip-theme";

/** Labels for the switcher. Kept here so the control and the persisted
 *  values can never disagree about what exists. */
export const THEME_LABELS: Record<Theme, string> = {
  default: "Default",
  dark: "Dark",
  aurora: "Aurora",
};

export function isTheme(value: unknown): value is Theme {
  return typeof value === "string" && (THEMES as readonly string[]).includes(value);
}

/** Default is the OPERATE surface and ships as the default; Aurora is
 *  opt-in and is never chosen automatically, so first-visit detection can
 *  only ever resolve to `default` or `dark`. */
export function preferredTheme(): Theme {
  if (typeof window === "undefined") return "default";
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "default";
}

export function storedTheme(): Theme | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return isTheme(raw) ? raw : null;
  } catch {
    // Private mode / storage disabled - the app still themes, it just
    // won't remember across reloads.
    return null;
  }
}

export function resolveInitialTheme(): Theme {
  return storedTheme() ?? preferredTheme();
}

export function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute("data-theme", theme);
}

export function persistTheme(theme: Theme): void {
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // see storedTheme()
  }
}
