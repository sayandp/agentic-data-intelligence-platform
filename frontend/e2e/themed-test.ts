import { test as base, expect } from "@playwright/test";

// Every spec runs once per theme (see playwright.config.ts's `projects`).
//
// The specs themselves are theme-agnostic on purpose: their locators are
// text- and role-based, so a theme change must not break a single one. If a
// spec fails in one theme and passes in another, that is the signal that a
// theme made a STRUCTURAL change rather than a visual one - which is exactly
// the failure this parametrization exists to catch.
//
// The theme is applied by seeding localStorage before any page script runs,
// which is the same path a returning human user takes (index.html's
// pre-paint boot script reads that key). Nothing about the app is stubbed.

export const THEME_NAMES = ["default", "dark", "aurora"] as const;
export type ThemeName = (typeof THEME_NAMES)[number];

export const test = base.extend<object, object>({
  page: async ({ page }, use, testInfo) => {
    const theme = (testInfo.project.name || "default") as ThemeName;
    await page.addInitScript((value) => {
      try {
        window.localStorage.setItem("adip-theme", value);
      } catch {
        /* storage disabled - the app falls back to prefers-color-scheme */
      }
    }, theme);
    await use(page);
  },
});

export { expect };
