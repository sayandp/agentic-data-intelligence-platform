import { mkdirSync, writeFileSync } from "node:fs";

import { expect, type APIRequestContext } from "@playwright/test";

import { test } from "./themed-test";

const API = "http://127.0.0.1:8000";
const FIXTURE_DIR = "D:/main_project/backend/data/e2e_upload";

/**
 * Part 2: the Session Summary, in a real browser.
 *
 * Three claims worth proving here rather than in a unit test. That the summary
 * appears at the TOP of the run view, above the detailed report - position is
 * the whole point of a summary for someone who will not read the statistics.
 * That quality context is rendered FIRST within it, so the caveat is never
 * read after the conclusion it qualifies. And that no causal language reaches
 * the page.
 */

const CAUSAL_WORDS = ["because", "caused", "due to", "drove", "led to", "resulted in"];

const CSV = `region,amount\n${Array.from({ length: 60 }, (_, i) => `north,${i}`).join("\n")}\n`;

function writeFixture(name: string, contents: string): string {
  mkdirSync(FIXTURE_DIR, { recursive: true });
  const path = `${FIXTURE_DIR}/${name}`;
  writeFileSync(path, contents, "utf-8");
  return path;
}

async function ingest(request: APIRequestContext, name: string): Promise<number> {
  const path = writeFixture(name, CSV);
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

/** The summary is written by the summarise node after narrate, so it appears
 *  a beat after the run reports completed. */
async function waitForSummary(request: APIRequestContext, runNumber: number) {
  // Waits for `available`, NOT for a 2xx. The endpoint answers 200 with
  // available:false while the summary does not exist yet - an expected
  // absence is not an error - so a response.ok() check returns instantly and
  // the page then renders the empty state. That is exactly what this helper
  // did after the route changed, and the suite caught it.
  const deadline = Date.now() + 180_000;
  while (Date.now() < deadline) {
    const response = await request.get(`${API}/summary/${runNumber}`);
    if (response.ok()) {
      const body = await response.json();
      if (body.available) return body;
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error(`run ${runNumber} never produced a session summary within 180s`);
}

test("the summary appears at the top of the run view, above the detailed report", async ({ page, request }) => {
  const runNumber = await ingest(request, "summary_top.csv");
  await waitForSummary(request, runNumber);

  await page.goto(`/reports?run=${runNumber}`);
  await expect(page.getByRole("heading", { name: "Summary", exact: true })).toBeVisible({ timeout: 120_000 });

  // Position, asserted structurally rather than by eye: the summary heading
  // must come before the narrative body in document order.
  const summaryBox = await page.getByRole("heading", { name: "Summary", exact: true }).boundingBox();
  const privacyBox = await page.getByRole("heading", { name: "Privacy", exact: true }).boundingBox();
  expect(summaryBox).not.toBeNull();
  expect(privacyBox).not.toBeNull();
  expect(summaryBox!.y).toBeLessThan(privacyBox!.y);
});

test("quality context is rendered before the summary text", async ({ page, request }) => {
  const runNumber = await ingest(request, "summary_quality.csv");
  const summary = await waitForSummary(request, runNumber);

  await page.goto(`/reports?run=${runNumber}`);
  // Scoped to the Summary card: the detailed report below has its own
  // "Data quality context" heading, and an unscoped match resolves to both.
  const card = page.locator("section, div").filter({ has: page.getByRole("heading", { name: "Summary", exact: true }) }).last();
  await expect(card.getByText("Data quality context", { exact: true })).toBeVisible({ timeout: 120_000 });

  const qualityBox = await card.getByText("Data quality context", { exact: true }).boundingBox();
  const summaryTextBox = await card.getByText(String(summary.summary_text).slice(0, 40), { exact: false }).first().boundingBox();

  expect(qualityBox).not.toBeNull();
  expect(summaryTextBox).not.toBeNull();
  expect(qualityBox!.y).toBeLessThan(summaryTextBox!.y);
});

test("a summary written without a model says so, with its reason", async ({ page, request }) => {
  const runNumber = await ingest(request, "summary_mode.csv");
  const summary = await waitForSummary(request, runNumber);

  await page.goto(`/reports?run=${runNumber}`);
  await expect(page.getByRole("heading", { name: "Summary", exact: true })).toBeVisible({ timeout: 120_000 });

  if (summary.generation_mode === "template") {
    await expect(page.getByText("written without a model")).toBeVisible();
    await expect(page.getByText(String(summary.fallback_reason))).toBeVisible();
  } else {
    await expect(page.getByText("written by a model")).toBeVisible();
  }
});

test("the summary page uses no causal language", async ({ page, request }) => {
  const runNumber = await ingest(request, "summary_causal.csv");
  await waitForSummary(request, runNumber);

  await page.goto(`/reports?run=${runNumber}`);
  await expect(page.getByRole("heading", { name: "Summary", exact: true })).toBeVisible({ timeout: 120_000 });

  const rendered = ((await page.locator("body").textContent()) ?? "").toLowerCase();
  for (const word of CAUSAL_WORDS) {
    expect(rendered, `the run view used causal language: ${word}`).not.toContain(word);
  }
});
