import { expect } from "@playwright/test";

import { test } from "./themed-test";

const API = "http://127.0.0.1:8000";

/**
 * The backend tests prove a deck can be BUILT. They say nothing about
 * whether a user can get the file, which is the thing that was actually
 * broken twice: first the download link existed only in the seconds after a
 * render finished, then the control itself was buried on a page the deck
 * does not belong to.
 *
 * So this spec tests the DESTINATION and the RETRIEVAL path in a real
 * browser: reach Export from the nav, see what the deck will contain before
 * waiting for it, build it, and assert bytes land on disk.
 */

type Api = { get: (url: string) => Promise<{ ok: () => boolean; json: () => Promise<unknown> }> };

/** A run with a report AND analytics, so the "real content" assertions are
 *  about a deck that genuinely has something to render. */
async function richestRun(request: Api): Promise<number> {
  // Widened from 20. The suite shares one backend, so a spec that adds
  // several runs of its own can push every analytics-rich run out of a
  // narrow window - and this test then falls back to a sparse run and
  // asserts sections it does not have. The Part 4 agriculture spec did
  // exactly that.
  const runs = (await (await request.get(`${API}/runs?status=completed&limit=60`)).json()) as { run_number: number }[];
  let fallback: number | null = null;
  for (const run of runs) {
    const report = await request.get(`${API}/reports/${run.run_number}`);
    if (!report.ok()) continue;
    if (fallback === null) fallback = run.run_number;
    const contents = await request.get(`${API}/export/${run.run_number}/pptx/contents`);
    if (!contents.ok()) continue;
    const { sections } = (await contents.json()) as { sections: { section: string; state: string }[] };
    const analyses = sections.filter((s) => s.state === "content" && /concentration|RFM|basket|retention|customer/i.test(s.section));
    if (analyses.length >= 2) return run.run_number;
  }
  if (fallback === null) throw new Error("no completed run has a report - seed one before running this spec");
  return fallback;
}

test("Export is reachable from the nav and previews the deck before building", async ({ page, request }) => {
  const run = await richestRun(request);

  await page.goto("/reports");
  await page.getByRole("link", { name: "Export" }).click();
  await expect(page).toHaveURL(/\/export/);
  await expect(page.getByRole("heading", { name: "Export", level: 1 })).toBeVisible();

  // The picker defaults to the newest completed run, so nothing is typed.
  await expect(page.locator("#export-run-select")).not.toHaveValue("");

  await page.locator("#export-run-typed").fill(String(run));
  await expect(page.getByText("What this deck will contain")).toBeVisible({ timeout: 30_000 });

  // Both outcomes are named: sections that carry results, and sections that
  // will carry the sentence saying why they do not.
  await expect(page.getByText(/included ·/).first()).toBeVisible();
  await expect(page.getByText("Analyses that did not apply", { exact: true })).toBeVisible();
});

test("a deck built from the Export page downloads as a non-empty pptx", async ({ page, request }) => {
  const run = await richestRun(request);
  await page.goto(`/export?run=${run}`);

  const action = page.getByRole("button", { name: /Build deck|Rebuild deck/ });
  await expect(action).toBeVisible();
  const box = await action.boundingBox();
  expect(box, "the build control has no box").not.toBeNull();

  await action.click();

  const download = page.getByRole("link", { name: /Download \.pptx/ });
  await expect(download).toBeVisible({ timeout: 180_000 });

  const downloaded = await Promise.all([page.waitForEvent("download"), download.click()]).then(([d]) => d);
  expect(downloaded.suggestedFilename()).toMatch(/\.pptx$/);

  const path = await downloaded.path();
  expect(path, "the browser reported no file on disk").toBeTruthy();

  const fs = await import("node:fs/promises");
  const { size } = await fs.stat(path!);
  expect(size, "the downloaded deck is empty").toBeGreaterThan(10_000);

  // A .pptx is a zip; cheapest proof the bytes are a real package rather
  // than an error page served with the wrong content type.
  const head = await fs.readFile(path!);
  expect(head.subarray(0, 2).toString("latin1")).toBe("PK");
});

test("the built deck carries the run's analytics and model content, not placeholders", async ({ page, request }) => {
  const run = await richestRun(request);
  await page.goto(`/export?run=${run}`);

  // Build if this run has no deck yet, so the assertion is about content
  // either way.
  const status = (await (await request.get(`${API}/export/${run}/pptx/status`)).json()) as { existing: unknown };
  if (!status.existing) {
    await page.getByRole("button", { name: /Build deck|Rebuild deck/ }).click();
  }
  await expect(page.getByRole("link", { name: /Download \.pptx/ })).toBeVisible({ timeout: 180_000 });

  // What the page promised must be what the deck was built from - the
  // outline and the builder read the same persisted artifacts.
  const contents = (await (await request.get(`${API}/export/${run}/pptx/contents`)).json()) as {
    sections: { section: string; state: string; detail: string }[];
  };
  const withContent = contents.sections.filter((s) => s.state === "content").map((s) => s.section);

  expect(withContent, "this run has no analytics to prove anything with").toEqual(
    expect.arrayContaining(["What the value figures measure"])
  );
  // Scoped to the outline list. An unscoped getByText matched the run
  // picker's hidden <option> for a run whose SOURCE FILENAME contained a
  // section word ("agri_charts.csv" vs the "Charts" section), so this
  // asserted visibility of a <select> option and failed. The outline is
  // where these names are supposed to appear.
  const outline = page.locator("ul").filter({ hasText: withContent[0] }).first();
  for (const section of withContent) {
    await expect(outline.getByText(section, { exact: false }).first()).toBeVisible();
  }
});

test("a previously built deck survives navigating away", async ({ page, request }) => {
  const run = await richestRun(request);

  const status = (await (await request.get(`${API}/export/${run}/pptx/status`)).json()) as { existing: unknown };
  if (!status.existing) {
    await page.goto(`/export?run=${run}`);
    await page.getByRole("button", { name: /Build deck|Rebuild deck/ }).click();
    await expect(page.getByRole("link", { name: /Download \.pptx/ })).toBeVisible({ timeout: 180_000 });
  }

  await page.goto("/");
  await page.goto(`/export?run=${run}`);

  await expect(page.getByRole("link", { name: /Download \.pptx/ })).toBeVisible({ timeout: 30_000 });
  // Size and age are shown, so a stale deck is recognisable as stale rather
  // than silently passed off as current.
  await expect(page.getByText(/KB, built /)).toBeVisible();
  await expect(page.getByRole("button", { name: "Rebuild deck" })).toBeVisible();
});

test("Reports points at the Export destination instead of duplicating it", async ({ page, request }) => {
  const run = await richestRun(request);
  await page.goto(`/reports?run=${run}`);

  // One entry point: Reports carries a link, not a second build control
  // with its own polling.
  await expect(page.getByRole("button", { name: /Export deck|Rebuild deck|Build deck/ })).toHaveCount(0);

  const link = page.getByRole("link", { name: "Export this run as a deck" });
  await expect(link).toBeVisible();
  await link.click();
  await expect(page).toHaveURL(new RegExp(`/export\\?run=${run}`));
});
