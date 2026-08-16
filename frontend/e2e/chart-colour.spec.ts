import { expect, test } from "./themed-test";

// COLOUR MAY DESCRIBE DATA. IT MAY NEVER ASSERT A VERDICT.
//
// This project shipped that bug: the chart palette was --chart-1..6, whose
// values WERE the four status colours, and a segment chart cycling it
// positionally painted "promising" red and "lost" green. Nothing in the code
// was wrong in isolation - the palette and the status colours were simply
// the same list.
//
// These run per theme (themed-test parametrises the projects), because a
// palette is only safe if it is safe in the theme the reader is actually
// using.

// The four status roles, by their real custom-property names. Not
// "--status-*": the raw tokens are --success/--danger/--warning/--info and
// the --color-status-* aliases in @theme inline point AT them, so reading
// the alias prefix alone would miss the values that actually matter.
const STATUS_TOKENS = [
  "--success",
  "--success-tint",
  "--danger",
  "--danger-tint",
  "--warning",
  "--warning-tint",
  "--info",
  "--info-tint",
];

/** Reads custom properties off the live document, so this asserts what the
 *  browser resolved for THIS theme - not what the source file says. */
async function tokens(page: import("@playwright/test").Page, prefix: string): Promise<Record<string, string>> {
  return page.evaluate((p) => {
    const style = getComputedStyle(document.documentElement);
    const out: Record<string, string> = {};
    for (const sheet of Array.from(document.styleSheets)) {
      let rules: CSSRuleList;
      try {
        rules = sheet.cssRules;
      } catch {
        continue; // cross-origin sheet
      }
      for (const rule of Array.from(rules)) {
        const text = (rule as CSSStyleRule).style;
        if (!text) continue;
        for (const name of Array.from(text)) {
          if (name.startsWith(p)) out[name] = style.getPropertyValue(name).trim();
        }
      }
    }
    return out;
  }, prefix);
}

function normalise(value: string): string {
  return value.trim().toLowerCase().replace(/\s+/g, "");
}

test.describe("Chart colour cannot reach the status palette", () => {
  test("no chart token resolves to a status colour", async ({ page }) => {
    await page.goto("/analytics");
    const chart = await tokens(page, "--chart-");
    const status = await page.evaluate((names) => {
      const style = getComputedStyle(document.documentElement);
      return Object.fromEntries(names.map((n) => [n, style.getPropertyValue(n).trim()]));
    }, STATUS_TOKENS);

    expect(Object.keys(chart).length, "chart tokens must exist in this theme").toBeGreaterThan(8);
    expect(Object.values(status).filter(Boolean).length, "status tokens must resolve in this theme").toBeGreaterThan(3);

    const statusValues = new Set(Object.values(status).map(normalise).filter(Boolean));
    const collisions = Object.entries(chart)
      .filter(([, v]) => statusValues.has(normalise(v)))
      .map(([k, v]) => `${k}=${v}`);

    expect(
      collisions,
      "a chart series resolving to a status colour is how 'promising' was once painted red",
    ).toEqual([]);
  });

  test("the categorical palette is six distinct hues", async ({ page }) => {
    await page.goto("/analytics");
    const chart = await tokens(page, "--chart-cat-");
    const values = Object.values(chart).map(normalise);

    expect(values.length).toBe(6);
    expect(new Set(values).size, "duplicate hues make two series look like one").toBe(6);
  });

  test("the sequential ramp is ordered by luminance, not arbitrary hues", async ({ page }) => {
    await page.goto("/analytics");
    const ramp = await page.evaluate(() => {
      const style = getComputedStyle(document.documentElement);
      const hexes = [1, 2, 3, 4, 5].map((i) => style.getPropertyValue(`--chart-seq-${i}`).trim());
      const lum = (hex: string) => {
        const h = hex.replace("#", "");
        const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
        const f = (c: number) => (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
      };
      return hexes.map(lum);
    });

    const ascending = [...ramp].sort((a, b) => a - b);
    const descending = [...ascending].reverse();
    // A ramp for ORDERED data has to be monotonic in one direction; a set
    // that wanders is just six hues wearing a ramp's name.
    expect(
      ramp.join() === ascending.join() || ramp.join() === descending.join(),
      `sequential ramp is not monotonic: ${ramp.map((l) => l.toFixed(3)).join(", ")}`,
    ).toBe(true);
  });
});

test.describe("Colour follows the shape of the data", () => {
  test("a single-series chart uses one colour", async ({ page }) => {
    await page.goto("/reports");
    // withDesignColors is the one place a series colour is assigned, so its
    // rule is asserted directly rather than through a rendered figure.
    const colours = await page.evaluate(async () => {
      // Resolved by Vite in the BROWSER, so the specifier is a dev-server
      // URL rather than a module TypeScript can find on disk. The indirection
      // keeps it out of TS module resolution without disabling the check.
      const specifier = "/src/lib/plotly.ts";
      const mod = (await import(/* @vite-ignore */ specifier)) as {
        withDesignColors: (data: unknown[], chartType?: string) => unknown[];
        chartAccent: () => string;
        chartCategorical: () => string[];
      };
      const one = mod.withDesignColors([{ type: "bar", marker: {} }]) as Array<Record<string, any>>;
      const many = mod.withDesignColors([
        { type: "bar", marker: {} },
        { type: "bar", marker: {} },
        { type: "bar", marker: {} },
      ]) as Array<Record<string, any>>;
      return {
        singleColour: one[0].marker.color,
        accent: mod.chartAccent(),
        manyColours: many.map((t) => t.marker.color),
      };
    });

    expect(colours.singleColour, "one trace must take the accent, not a palette slot").toBe(colours.accent);
    expect(new Set(colours.manyColours).size, "a genuine multi-series figure gets distinct hues").toBe(3);
  });
});
