import { expect } from "@playwright/test";

import { test } from "./themed-test";

const API = "http://127.0.0.1:8000";

/**
 * The 23 backend tests prove a deck can be BUILT. They say nothing about
 * whether a user can get the file, which is the thing that was actually
 * broken: the download link existed only in the seconds after a render
 * finished and disappeared on navigation.
 *
 * So this spec deliberately tests the RETRIEVAL path, in a real browser:
 * click the control, wait for the render, and assert that bytes arrive on
 * disk - then navigate away, come back, and assert the file is still
 * reachable. A file you can only catch mid-flight is not saved.
 */

async function newestRunWithAReport(request: {
  get: (url: string) => Promise<{ ok: () => boolean; json: () => Promise<unknown> }>;
}): Promise<number> {
  const response = await request.get(`${API}/runs?status=completed&limit=20`);
  const runs = (await response.json()) as { run_number: number }[];
  for (const run of runs) {
    const report = await request.get(`${API}/reports/${run.run_number}`);
    if (report.ok()) return run.run_number;
  }
  throw new Error("no completed run has a report - seed one before running this spec");
}

test("a deck can be exported and the file actually downloads", async ({ page, request }) => {
  const run = await newestRunWithAReport(request);
  await page.goto(`/reports?run=${run}`);

  const action = page.getByRole("button", { name: /Export deck|Rebuild deck/ });
  await expect(action).toBeVisible();

  // Reachable without scrolling: this control sits in the page header, not
  // at the bottom of one tab's content.
  const box = await action.boundingBox();
  expect(box, "the export control has no box").not.toBeNull();
  expect(box!.y).toBeLessThan(page.viewportSize()!.height);

  await action.click();

  // Rendering every chart through kaleido is genuinely slow, so the control
  // reports elapsed time rather than freezing. Wait on the outcome.
  const download = page.getByRole("link", { name: /Download \.pptx/ });
  await expect(download).toBeVisible({ timeout: 150_000 });

  const downloaded = await Promise.all([page.waitForEvent("download"), download.click()]).then(([d]) => d);

  expect(downloaded.suggestedFilename()).toMatch(/\.pptx$/);
  const path = await downloaded.path();
  expect(path, "the browser reported no file on disk").toBeTruthy();

  const { size } = await (await import("node:fs/promises")).stat(path!);
  expect(size, "the downloaded deck is empty").toBeGreaterThan(10_000);

  // A .pptx is a zip; this is the cheapest proof the bytes are a real
  // package rather than an error page served with the wrong content type.
  const head = await (await import("node:fs/promises")).readFile(path!);
  expect(head.subarray(0, 2).toString("latin1")).toBe("PK");
});

test("a previously generated deck survives navigating away", async ({ page, request }) => {
  const run = await newestRunWithAReport(request);

  // Whatever the previous test built is still on disk; if this spec runs
  // alone, build it now so the assertion is about retrieval either way.
  const status = (await (await request.get(`${API}/export/${run}/pptx/status`)).json()) as {
    existing: unknown;
  };
  if (!status.existing) {
    await page.goto(`/reports?run=${run}`);
    await page.getByRole("button", { name: /Export deck|Rebuild deck/ }).click();
    await expect(page.getByRole("link", { name: /Download \.pptx/ })).toBeVisible({ timeout: 150_000 });
  }

  await page.goto("/");
  await page.goto(`/reports?run=${run}`);

  const download = page.getByRole("link", { name: /Download \.pptx/ });
  await expect(download).toBeVisible({ timeout: 30_000 });

  // The label says when it was made and how big it is, so a stale deck is
  // recognisable as stale rather than silently passed off as current.
  await expect(page.getByText(/KB, built /)).toBeVisible();
  await expect(page.getByRole("button", { name: "Rebuild deck" })).toBeVisible();
});
