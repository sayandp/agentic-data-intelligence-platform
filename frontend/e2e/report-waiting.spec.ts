import { expect } from "@playwright/test";

import { test } from "./themed-test";

/**
 * What the Reports page says while a report does not exist yet.
 *
 * It used to say "Exploration finished and the narrative is still being
 * written" for the whole wait, whatever was actually happening, because the
 * run's status was "completed". That status is set when a run passes
 * validation - before role detection, exploration or analytics run - so a run
 * still inside exploration was told its exploration had finished. On a
 * 1,067,371-row export that claim was false for 9 minutes.
 *
 * Both responses are stubbed rather than driven through a real ingest: the
 * window is a few seconds on any fixture small enough to ingest in a test,
 * so catching the two phases live would be a race. What is under test is the
 * page's reading of `findings.available`, and a stub states that exactly.
 */

const STATUS = (findingsAvailable: boolean) => ({
  run_id: "stub-run",
  run_number: 4242,
  status: "completed",
  findings: { available: findingsAvailable, run_id: "stub-run", url: null },
  report: { available: false, run_id: "stub-run", url: null, generation_mode: null },
});

async function stub(page: import("@playwright/test").Page, findingsAvailable: boolean) {
  await page.route("**/ingest/**/status*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(STATUS(findingsAvailable)) })
  );
  await page.route("**/reports/**", (route) =>
    route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ detail: "no report yet (stubbed)" }),
    })
  );
}

test("while exploration is still running, the page does not claim it finished", async ({ page }) => {
  await stub(page, false);
  await page.goto("/reports?run=4242");

  const waiting = page.getByTestId("report-waiting");
  await expect(waiting).toBeVisible({ timeout: 30_000 });
  await expect(waiting).toContainText("analysis is still running");
  await expect(waiting).not.toContainText("Exploration finished");
});

test("once exploration has finished, the page says so", async ({ page }) => {
  await stub(page, true);
  await page.goto("/reports?run=4242");

  const waiting = page.getByTestId("report-waiting");
  await expect(waiting).toBeVisible({ timeout: 30_000 });
  await expect(waiting).toContainText("Exploration finished");
  await expect(waiting).not.toContainText("analysis is still running");
});
