import { expect } from "@playwright/test";

import { test } from "./themed-test";

const API = "http://127.0.0.1:8000";

/**
 * The Marketing Agent's view, in a real browser.
 *
 * The two assertions that matter are the shape of the feature: a run that
 * QUALIFIES shows key values before warnings, and a run that does NOT shows
 * the refusal with the missing role named rather than an empty page a reader
 * has to interpret.
 */

async function runsWithMarketing(request: { get: (url: string) => Promise<{ ok: () => boolean; json: () => Promise<unknown> }> }) {
  const runs = (await (await request.get(`${API}/runs?status=completed&limit=40`)).json()) as { run_number: number }[];
  let qualifying: number | null = null;
  let refusing: number | null = null;
  for (const run of runs) {
    const response = await request.get(`${API}/marketing/${run.run_number}`);
    if (!response.ok()) continue;
    const body = (await response.json()) as { applicable: boolean };
    if (body.applicable && qualifying === null) qualifying = run.run_number;
    if (!body.applicable && refusing === null) refusing = run.run_number;
    if (qualifying !== null && refusing !== null) break;
  }
  return { qualifying, refusing };
}

test("a qualifying run shows key values, then what needs a decision", async ({ page, request }) => {
  const { qualifying } = await runsWithMarketing(request);
  test.skip(qualifying === null, "no qualifying ads run in this database");

  await page.goto(`/marketing?run=${qualifying}`);
  await expect(page.getByRole("heading", { name: "Marketing", level: 1 })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Key values" })).toBeVisible({ timeout: 30_000 });

  // Key values lead, by bounding box - the same order-of-consequence rule
  // the Approvals page follows.
  const keyValues = await page.getByRole("heading", { name: "Key values" }).boundingBox();
  const decisions = await page.getByRole("heading", { name: "Needs a decision" }).boundingBox();
  expect(keyValues!.y).toBeLessThan(decisions!.y);

  // The period the numbers cover is stated with them.
  await expect(page.getByText(/\d{4}-\d{2}-\d{2} to \d{4}-\d{2}-\d{2}/)).toBeVisible();
});

test("every warning states its rule, observed value, threshold and comparison basis", async ({ page, request }) => {
  const { qualifying } = await runsWithMarketing(request);
  test.skip(qualifying === null, "no qualifying ads run in this database");

  await page.goto(`/marketing?run=${qualifying}`);
  await expect(page.getByRole("heading", { name: "Key values" })).toBeVisible({ timeout: 30_000 });

  // Wait for the section itself before counting: gating on a locator that
  // has not painted yet turns a real assertion into a silent skip.
  await expect(page.getByRole("heading", { name: "Needs a decision" })).toBeVisible();
  const observed = page.getByText(/observed \w+:/);
  await expect(observed.first()).toBeVisible();

  // A warning missing any of these cannot be judged.
  await expect(page.getByText(/observed \w+:/).first()).toBeVisible();
  await expect(page.getByText(/threshold:/).first()).toBeVisible();
  await expect(page.getByText(/Compared against /).first()).toBeVisible();
});

test("a non-ads run says why it does not qualify and names the missing role", async ({ page, request }) => {
  const { refusing } = await runsWithMarketing(request);
  test.skip(refusing === null, "every run in this database qualifies");

  await page.goto(`/marketing?run=${refusing}`);
  await expect(page.getByText("This run is not an ad-campaign export")).toBeVisible({ timeout: 30_000 });
  // Two legitimate refusal shapes: a missing required role, or spend and a
  // date with nothing only an ad platform reports. Both must name what was
  // needed - that is the part a reader can act on.
  await expect(page.getByText(/Marketing analysis (also )?needs/)).toBeVisible();

  // No key values, no warnings - a refusal is not a half-rendered report.
  await expect(page.getByRole("heading", { name: "Key values" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Needs a decision" })).toHaveCount(0);
});

test("marketing charts render from the persisted spec", async ({ page, request }) => {
  const { qualifying } = await runsWithMarketing(request);
  test.skip(qualifying === null, "no qualifying ads run in this database");

  await page.goto(`/marketing?run=${qualifying}`);
  await expect(page.getByRole("heading", { name: "Key values" })).toBeVisible({ timeout: 30_000 });
  await page.waitForTimeout(4000);

  const plots = page.locator(".js-plotly-plot");
  expect(await plots.count()).toBeGreaterThan(0);

  // The spec is the backend's; the browser only resolves colour roles. No
  // chart colour may resolve to a status token.
  const usesStatusColour = await page.evaluate(() => {
    const style = getComputedStyle(document.documentElement);
    const status = ["--success", "--danger", "--warning", "--info"]
      .map((t) => style.getPropertyValue(t).trim().toLowerCase())
      .filter(Boolean);
    return [...document.querySelectorAll(".js-plotly-plot")].some((el) =>
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      ((el as any).data ?? []).some((trace: any) => {
        const colours = [trace?.marker?.color, trace?.line?.color].flat().filter(Boolean);
        return colours.some((c: unknown) => status.includes(String(c).toLowerCase()));
      })
    );
  });
  expect(usesStatusColour, "a marketing chart resolved to a status colour").toBe(false);
});
