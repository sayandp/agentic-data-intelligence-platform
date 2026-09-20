import { mkdirSync, writeFileSync } from "node:fs";

import { expect, type APIRequestContext } from "@playwright/test";

import { test } from "./themed-test";

const API = "http://127.0.0.1:8000";
const FIXTURE_DIR = "D:/main_project/backend/data/e2e_upload";

/**
 * Paging the result lists.
 *
 * These lists are bounded by the DATA, not by anything in the code. Measured
 * on 7,680 rows of district-crop statistics, one agriculture run produced
 * 1,328 findings and 655 scope chips, and the only way to learn how many
 * there were was to scroll to the end of the page.
 *
 * Two fixtures, sized from what the API actually returned rather than from a
 * prediction - the counts below were read back off a real run:
 *
 *   30 districts (720 rows):  66 warnings    -> paged, 7 pages of 10
 *                             43 scope chips -> NOT paged (they fit in 48)
 *                             <=10 improvements -> NOT paged, the CONTROL
 *                             case. Without a list that stays whole, these
 *                             tests would pass just as well against a pager
 *                             that rendered unconditionally.
 *
 *   60 districts (1440 rows): enough district-crop pairs to push the chip row
 *                             past 48, which the narrow fixture never does.
 *                             Added because the chips are a SECOND wiring of
 *                             the same component - a different page size and
 *                             a different source list - and the narrow
 *                             fixture left that wiring unexercised.
 *
 * Every count is re-read from the API inside each test; none is hardcoded, so
 * a rule change that shifts them fails loudly instead of passing quietly.
 */

const WARNINGS_PER_PAGE = 10;
const CHIPS_PER_PAGE = 48;

function cropCsv(districtCount: number): string {
  const header = "District_Name,Crop_Year,Season,Crop,Area,Production,Rainfall_mm";
  const seasons = ["Kharif", "Rabi", "Whole Year"];
  const rows: string[] = [];
  // Deterministic, so a rerun measures the same run rather than a new one.
  let seed = 11;
  const rand = () => {
    seed = (seed * 1103515245 + 12345) % 2147483648;
    return seed / 2147483648;
  };
  for (let year = 2015; year < 2023; year += 1) {
    for (let d = 0; d < districtCount; d += 1) {
      for (const crop of ["Rice", "Wheat", "Maize"]) {
        const collapsing = d === 0 && crop === "Rice" && year === 2022;
        const area = (500 + rand() * 1500) * (collapsing ? 0.4 : 1);
        const perHa = (2 + rand() * 7) * (collapsing ? 0.3 : 1);
        rows.push(
          `District_${String(d).padStart(2, "0")},${year},${seasons[year % 3]},${crop},` +
            `${area.toFixed(2)},${(area * perHa).toFixed(2)},${(1800 + rand() * 1200).toFixed(1)}`
        );
      }
    }
  }
  return [header, ...rows].join("\n");
}

const CROP_CSV = cropCsv(30);
const WIDE_CROP_CSV = cropCsv(60);

function distinctScopes(body: { findings?: { payload?: { scope?: string | null } }[] }): Set<string> {
  const scopes = new Set<string>();
  for (const f of body.findings ?? []) {
    const scope = f.payload?.scope;
    if (scope) scopes.add(scope);
  }
  return scopes;
}

async function ingest(request: APIRequestContext, name: string, contents: string): Promise<number> {
  mkdirSync(FIXTURE_DIR, { recursive: true });
  const path = `${FIXTURE_DIR}/${name}`;
  writeFileSync(path, contents, "utf-8");
  const source = await (
    await request.post(`${API}/sources`, { data: { type: "file", connection_config: { path } } })
  ).json();
  const started = await (await request.post(`${API}/ingest/${source.id}`)).json();

  const deadline = Date.now() + 300_000;
  while (Date.now() < deadline) {
    const status = await (await request.get(`${API}/ingest/${started.run_id}/status`)).json();
    if (status.status === "completed") return status.run_number as number;
    if (status.status === "failed") throw new Error("ingest failed");
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error("ingest did not complete within 300s");
}

async function waitForAgriculture(request: APIRequestContext, runNumber: number) {
  const deadline = Date.now() + 120_000;
  while (Date.now() < deadline) {
    const response = await request.get(`${API}/agriculture/${runNumber}`);
    if (response.ok()) {
      const body = await response.json();
      if (body.applicable !== undefined) return body;
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error(`run ${runNumber} produced no agriculture analysis within 120s`);
}

// A full pipeline run, the same budget the other pipeline-driving specs declare.
test.describe.configure({ timeout: 600_000 });

test("a long list is paged, and states the count it is paging", async ({ page, request }) => {
  const runNumber = await ingest(request, "pagination_crops.csv", CROP_CSV);
  const body = await waitForAgriculture(request, runNumber);

  const warnings = (body.findings ?? []).filter((f: { severity: string }) => f.severity === "warning");
  expect(warnings.length, "fixture must produce more warnings than fit on a page").toBeGreaterThan(
    WARNINGS_PER_PAGE
  );

  await page.goto(`/agriculture?run=${runNumber}`);
  const card = page.locator(".panel").filter({ hasText: "Needs a decision" });
  await expect(card.getByRole("heading", { name: "Needs a decision" })).toBeVisible({ timeout: 120_000 });

  // Only one page renders...
  await expect(card.locator("li")).toHaveCount(WARNINGS_PER_PAGE);

  // ...and the reader is told how many there are in total. This is the part
  // that matters: this platform's promise is that every warning reaches a
  // person, so a pager that hid 76 warnings behind a count of 10 would break
  // the guarantee rather than tidy the page.
  await expect(card.getByTestId("pager-status")).toContainText(`of ${warnings.length} warnings`);
  await expect(card.getByTestId("pager-status")).toContainText(`1\u2013${WARNINGS_PER_PAGE}`);
});

test("paging forward moves through the list and stops at the end", async ({ page, request }) => {
  const runNumber = await ingest(request, "pagination_crops_fwd.csv", CROP_CSV);
  const body = await waitForAgriculture(request, runNumber);
  const total = (body.findings ?? []).filter((f: { severity: string }) => f.severity === "warning").length;
  const pages = Math.ceil(total / WARNINGS_PER_PAGE);

  await page.goto(`/agriculture?run=${runNumber}`);
  const card = page.locator(".panel").filter({ hasText: "Needs a decision" });
  await expect(card.getByTestId("pager-position")).toBeVisible({ timeout: 120_000 });

  await expect(card.getByTestId("pager-position")).toHaveText(`Page 1 of ${pages}`);
  await expect(card.getByTestId("pager-prev")).toBeDisabled();

  const firstRow = await card.locator("li").first().innerText();
  await card.getByTestId("pager-next").click();

  await expect(card.getByTestId("pager-position")).toHaveText(`Page 2 of ${pages}`);
  await expect(card.getByTestId("pager-prev")).toBeEnabled();
  expect(await card.locator("li").first().innerText(), "page 2 repeats page 1").not.toBe(firstRow);

  // Walk to the end; Next must be disabled there and nowhere earlier.
  for (let i = 2; i < pages; i += 1) {
    await expect(card.getByTestId("pager-next")).toBeEnabled();
    await card.getByTestId("pager-next").click();
  }
  await expect(card.getByTestId("pager-position")).toHaveText(`Page ${pages} of ${pages}`);
  await expect(card.getByTestId("pager-next")).toBeDisabled();
});

test("a list that already fits gets no pager at all", async ({ page, request }) => {
  const runNumber = await ingest(request, "pagination_crops_ctl.csv", CROP_CSV);
  const body = await waitForAgriculture(request, runNumber);

  const improvements = (body.findings ?? []).filter((f: { severity: string }) => f.severity === "improvement");
  expect(improvements.length, "control case must fit on one page").toBeLessThanOrEqual(WARNINGS_PER_PAGE);
  expect(improvements.length, "control case must not be empty").toBeGreaterThan(0);

  await page.goto(`/agriculture?run=${runNumber}`);
  const improvementsCard = page.locator(".panel").filter({ hasText: "Improvements" });
  await expect(improvementsCard.getByRole("heading", { name: "Improvements" })).toBeVisible({ timeout: 120_000 });

  // Every improvement is on screen, and nothing was added to say so.
  await expect(improvementsCard.locator("li")).toHaveCount(improvements.length);
  await expect(improvementsCard.getByTestId("pager-status")).toHaveCount(0);

  // The warnings card on this same page IS paged - so this assertion is
  // about the list fitting, not about the pager being absent everywhere.
  const warningsCard = page.locator(".panel").filter({ hasText: "Needs a decision" });
  await expect(warningsCard.getByTestId("pager-status")).toBeVisible();
});

test("filtering while deep in a list never strands the reader on an empty page", async ({ page, request }) => {
  const runNumber = await ingest(request, "pagination_crops_filter.csv", CROP_CSV);
  await waitForAgriculture(request, runNumber);

  await page.goto(`/agriculture?run=${runNumber}`);
  const card = page.locator(".panel").filter({ hasText: "Needs a decision" });
  await expect(card.getByTestId("pager-position")).toBeVisible({ timeout: 120_000 });

  // Go deep enough that the page number cannot survive the filter.
  await card.getByTestId("pager-next").click();
  await card.getByTestId("pager-next").click();
  await card.getByTestId("pager-next").click();
  await expect(card.getByTestId("pager-position")).toHaveText(/Page 4 of/);

  // Now narrow to a single district-crop, whose warnings are far fewer than
  // three pages. A page number kept from the unfiltered list would render an
  // empty card here, with nothing on screen to say anything had been found.
  await page.getByTestId("agriculture-scope-chip").first().click();

  const rows = card.locator("li");
  await expect(rows.first()).toBeVisible();
  expect(await rows.count(), "filtering left the reader on an empty page").toBeGreaterThan(0);
});

test("a page number does not outlive the run it was chosen for", async ({ page, request }) => {
  // The Load button swaps the run's data IN PLACE - no navigation, so the
  // component is never remounted and React state is not reset for us. Two
  // runs of the same fixture both have ~7 pages of warnings, so page 4 is a
  // VALID page number in each: a stale page would not be clamped away, and
  // the reader would silently land in the middle of a list they just opened.
  // This is the case that distinguishes the list-identity guard from the
  // clamp; a single-scope filter cannot, because the filtered list is short
  // enough that the clamp alone produces the right answer.
  const runA = await ingest(request, "pagination_crops_runA.csv", CROP_CSV);
  const runB = await ingest(request, "pagination_crops_runB.csv", CROP_CSV);
  await waitForAgriculture(request, runA);
  await waitForAgriculture(request, runB);

  await page.goto(`/agriculture?run=${runA}`);
  const card = page.locator(".panel").filter({ hasText: "Needs a decision" });
  await expect(card.getByTestId("pager-position")).toBeVisible({ timeout: 120_000 });

  await card.getByTestId("pager-next").click();
  await card.getByTestId("pager-next").click();
  await card.getByTestId("pager-next").click();
  await expect(card.getByTestId("pager-position")).toHaveText(/Page 4 of/);

  await page.locator("#agriculture-run").fill(String(runB));
  await page.getByRole("button", { name: "Load" }).click();

  await expect(card.getByTestId("pager-position")).toHaveText(/Page 1 of/);
  await expect(card.getByTestId("pager-status")).toContainText("1–");
});

test("the chip row is paged by the same component, against its own page size", async ({ page, request }) => {
  // The chips are a SECOND wiring of the Pager: a different page size, a
  // different source list, and a container that wraps rather than stacks.
  // The narrow fixture produces 43 chips, so it never exercises this at all.
  const runNumber = await ingest(request, "pagination_crops_wide.csv", WIDE_CROP_CSV);
  const body = await waitForAgriculture(request, runNumber);

  const scopes = distinctScopes(body);
  expect(scopes.size, "wide fixture must overflow the chip row").toBeGreaterThan(CHIPS_PER_PAGE);

  await page.goto(`/agriculture?run=${runNumber}`);
  const chipCard = page.locator(".panel").filter({ hasText: "District and crop" });
  await expect(chipCard.getByTestId("pager-status")).toBeVisible({ timeout: 120_000 });

  await expect(page.getByTestId("agriculture-scope-chip")).toHaveCount(CHIPS_PER_PAGE);
  await expect(chipCard.getByTestId("pager-status")).toContainText(`of ${scopes.size} district-crop pairs`);

  const firstChip = await page.getByTestId("agriculture-scope-chip").first().innerText();
  await chipCard.getByTestId("pager-next").click();
  expect(await page.getByTestId("agriculture-scope-chip").first().innerText()).not.toBe(firstChip);
});
