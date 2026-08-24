import { mkdirSync, writeFileSync } from "node:fs";

import { expect, type APIRequestContext } from "@playwright/test";

import { test } from "./themed-test";

const API = "http://127.0.0.1:8000";
const FIXTURE_DIR = "D:/main_project/backend/data/e2e_upload";

/**
 * Part 1: comparing two runs of one source, in a real browser.
 *
 * The claim worth proving here is not that a diff renders - it is that the
 * REFUSALS render. A comparison view exists to report change, so the failure
 * that matters is a plausible-looking number where there should be a stated
 * reason: a delta between two runs of different sources, or between figures
 * computed on different value bases.
 *
 * Also asserts that no causal word reaches the page. Two runs differ in every
 * uncontrolled way at once, so nothing here can support a claim about why.
 */

const CAUSAL_WORDS = ["because", "caused", "due to", "drove", "led to", "resulted in", "as a result"];

function rows(n: number, amountFrom: number): string {
  return Array.from({ length: n }, (_, i) => `ORD-${i},widgets,${amountFrom + i},${(i % 5) + 1}`).join("\n");
}

const FIRST = `order_id,category,price,quantity\n${rows(60, 20)}\n`;
const SECOND = `order_id,category,price,quantity\n${rows(60, 40)}\n`;

function writeFixture(name: string, contents: string): string {
  mkdirSync(FIXTURE_DIR, { recursive: true });
  const path = `${FIXTURE_DIR}/${name}`;
  writeFileSync(path, contents, "utf-8");
  return path;
}

async function registerSource(request: APIRequestContext, path: string): Promise<string> {
  const body = await (
    await request.post(`${API}/sources`, { data: { type: "file", connection_config: { path } } })
  ).json();
  return body.id;
}

/**
 * Ingests and returns a COMPLETED run's number.
 *
 * Re-ingesting changed data usually escalates - which is the pipeline working
 * - and an escalated run is not comparable, because exploration and analytics
 * only run on a completed one. So this resolves whatever is pending, exactly
 * as a person would, rather than comparing against a paused run. A first
 * version of this helper returned on `awaiting_approval` and the comparison
 * correctly refused it; the fixture was wrong, not the guard.
 *
 * The resolve is ASYNCHRONOUS: POST /approvals/{id}/resolve returns
 * "resolving" and the graph resumes in the background, through narrate and a
 * real model call. So this polls to a terminal state rather than reading the
 * status once after resolving - a second version of this helper did that and
 * saw a stale `awaiting_approval` from the instant before the resume landed.
 */
async function ingestAndWait(request: APIRequestContext, sourceId: string): Promise<number> {
  const started = await (await request.post(`${API}/ingest/${sourceId}`)).json();
  const runId = started.run_id as string;
  const deadline = Date.now() + 300_000;
  const resolved = new Set<string>();

  while (Date.now() < deadline) {
    const status = await (await request.get(`${API}/ingest/${runId}/status`)).json();

    if (status.status === "completed") return status.run_number as number;
    if (status.status === "failed") throw new Error(`run ${status.run_number} failed`);

    if (status.status === "awaiting_approval") {
      const pending = await (await request.get(`${API}/approvals/pending`)).json();
      const mine = (pending.validation_events ?? []).filter(
        (e: { run_id: string; resolve_id: string }) => e.run_id === runId && !resolved.has(e.resolve_id)
      );
      for (const event of mine) {
        resolved.add(event.resolve_id);
        await request.post(`${API}/approvals/${event.resolve_id}/resolve`, {
          data: { decision: "reject_fix", resolved_by: "compare-e2e" },
        });
      }
    }

    await new Promise((resolve) => setTimeout(resolve, 2000));
  }

  throw new Error(`run did not reach completed within 300s`);
}


/** Two completed runs of ONE source, the second over changed data. */
async function twoRunsOfOneSource(request: APIRequestContext): Promise<[number, number]> {
  const path = writeFixture("compare_orders.csv", FIRST);
  const sourceId = await registerSource(request, path);
  const first = await ingestAndWait(request, sourceId);
  writeFixture("compare_orders.csv", SECOND);
  const second = await ingestAndWait(request, sourceId);
  return [first, second];
}

test("two runs of one source compare, and the change is reported without causal language", async ({ page, request }) => {
  const [a, b] = await twoRunsOfOneSource(request);

  // Read the API first: if the comparison is blocked, the UI assertions below
  // would pass vacuously against a refusal panel.
  const api = await (await request.get(`${API}/compare?run_a=${a}&run_b=${b}`)).json();
  expect(api.comparable, `comparison was blocked: ${api.blocked_reason}`).toBe(true);

  await page.goto(`/compare?run_a=${a}&run_b=${b}`);
  await expect(page.getByRole("heading", { name: "Compare", exact: true })).toBeVisible();
  await expect(page.getByText("Runs compared")).toBeVisible({ timeout: 60_000 });

  // Every section is named, including the ones that cannot be compared -
  // silence would read as "not implemented".
  // Scoped to headings: "Analytics" is also a nav link, and a bare text
  // match resolves to both.
  for (const section of ["Schema", "Data quality", "Exploration", "Analytics"]) {
    await expect(page.getByRole("heading", { name: section, exact: true })).toBeVisible();
  }

  const rendered = ((await page.locator("body").textContent()) ?? "").toLowerCase();
  for (const word of CAUSAL_WORDS) {
    expect(rendered, `the compare page used causal language: ${word}`).not.toContain(word);
  }
});

test("comparing runs of different sources is refused, with the reason shown", async ({ page, request }) => {
  const sourceA = await registerSource(request, writeFixture("compare_a.csv", FIRST));
  const sourceB = await registerSource(request, writeFixture("compare_b.csv", SECOND));
  const runA = await ingestAndWait(request, sourceA);
  const runB = await ingestAndWait(request, sourceB);

  await page.goto(`/compare?run_a=${runA}&run_b=${runB}`);

  await expect(page.getByText("These runs cannot be compared")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/different sources/)).toBeVisible();
  // And no diff table anywhere - a refusal must not also show numbers.
  await expect(page.locator("th", { hasText: "Relative" })).toHaveCount(0);
});

test("a run list offers comparison only once two completed runs are picked", async ({ page, request }) => {
  const path = writeFixture("compare_pick.csv", FIRST);
  const sourceId = await registerSource(request, path);
  await ingestAndWait(request, sourceId);
  writeFixture("compare_pick.csv", SECOND);
  await ingestAndWait(request, sourceId);

  await page.goto("/sources");
  const row = page.locator("tr", { has: page.locator(`code:text-is("${sourceId}")`) });
  await row.getByRole("button", { name: "History" }).click();

  const compareLink = page.getByRole("link", { name: "Compare selected" });
  await expect(compareLink).toBeVisible({ timeout: 30_000 });
  await expect(compareLink).toHaveAttribute("aria-disabled", "true");
  await expect(page.getByText("Tick two completed runs to compare them.")).toBeVisible();

  const boxes = page.locator('input[type="checkbox"][aria-label^="Select run"]');
  await boxes.nth(0).check();
  await expect(compareLink).toHaveAttribute("aria-disabled", "true");
  await boxes.nth(1).check();
  await expect(compareLink).toHaveAttribute("aria-disabled", "false");

  await compareLink.click();
  await expect(page).toHaveURL(/\/compare\?run_a=\d+&run_b=\d+/);
  await expect(page.getByText("Runs compared")).toBeVisible({ timeout: 60_000 });
});
