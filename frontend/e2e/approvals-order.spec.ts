import { mkdirSync, writeFileSync } from "node:fs";

import { expect, type APIRequestContext } from "@playwright/test";

import { test } from "./themed-test";

/**
 * Needs Attention is ordered by CONSEQUENCE, not by how many rows each
 * section has.
 *
 * An escalated validation event is a run PAUSED waiting on a person -
 * nothing downstream of it can proceed. A provisional baseline is
 * informational, normal on a first ingest, and resolvable whenever. With
 * baselines leading, ~45 informational rows sat above the one paused thing
 * and told a reviewer the wrong story about what needed them.
 *
 * Asserted by BOUNDING BOX rather than DOM order, because what matters is
 * what a reviewer's eye reaches first - and in every theme, since a theme
 * must never change structure.
 */

const SECTIONS = [
  "Escalated validation events",
  "Escalated queries",
  "Escalated models",
  "Connector warnings",
  "Provisional baselines",
];

test("Needs Attention sections are ordered by consequence, on screen", async ({ page }) => {
  await page.goto("/approvals");
  await expect(page.getByRole("heading", { name: SECTIONS[0] })).toBeVisible();

  const tops: number[] = [];
  for (const section of SECTIONS) {
    const box = await page.getByRole("heading", { name: section, exact: true }).boundingBox();
    expect(box, `no heading found for ${section}`).not.toBeNull();
    tops.push(box!.y);
  }

  for (let i = 1; i < tops.length; i += 1) {
    expect(tops[i], `${SECTIONS[i]} must appear below ${SECTIONS[i - 1]}`).toBeGreaterThan(tops[i - 1]);
  }
});

test("escalated events appear above provisional baselines", async ({ page }) => {
  await page.goto("/approvals");

  const escalated = page.getByRole("heading", { name: "Escalated validation events", exact: true });
  const baselines = page.getByRole("heading", { name: "Provisional baselines", exact: true });
  await expect(escalated).toBeVisible();
  await expect(baselines).toBeVisible();

  const [a, b] = [await escalated.boundingBox(), await baselines.boundingBox()];
  expect(a!.y).toBeLessThan(b!.y);
});

const API = "http://127.0.0.1:8000";
const FIXTURE_DIR = "D:/main_project/backend/data/e2e_upload";

// Mirrors ApprovalsPage.tsx's BASELINE_COLLAPSE_THRESHOLD: the list collapses
// only ABOVE this many rows.
const COLLAPSE_THRESHOLD = 10;

async function provisionalBaselineCount(request: APIRequestContext): Promise<number> {
  const pending = (await (await request.get(`${API}/approvals/pending`)).json()) as {
    provisional_baselines: unknown[];
  };
  return pending.provisional_baselines.length;
}

/**
 * Tops the database up to more provisional baselines than the collapse
 * threshold, by registering fresh sources and ingesting each once: a source's
 * FIRST ingest is what records a provisional baseline.
 *
 * This used to be `test.skip(count <= 10)`. On any freshly reset database -
 * which is exactly where a reviewer runs the suite - it skipped, and the run
 * reported green over an assertion that had not been made. A skip is a pass
 * that checked nothing; the marketing specs were fixed the same way.
 *
 * The seed file is deliberately clean - no nulls, and numeric columns that
 * vary - because assert_baseline_sane refuses a zero-variance or mostly-null
 * profile, and a refused profile records no baseline at all.
 *
 * KNOWN FAILURE on a fresh database with a model configured: the first time
 * this seeding actually ran, it exposed a backend defect the skip had been
 * hiding. narrate_node holds a database connection across its model calls,
 * so back-to-back ingests exhaust SQLAlchemy's default pool (5 + 10): a
 * status request returned 500 after waiting 30s, and the eighth seeded
 * ingest FAILED with `QueuePool limit ... reached`. That is the product
 * failing, not this test - it fails here, loudly, until that is fixed.
 */
async function seedProvisionalBaselines(request: APIRequestContext, needed: number) {
  mkdirSync(FIXTURE_DIR, { recursive: true });
  const stamp = Date.now();
  for (let i = 0; i < needed; i += 1) {
    const rows = Array.from(
      { length: 40 },
      (_, r) => `${r + 1},${(12.5 + ((r * 7 + i) % 23) * 3.1).toFixed(2)},${1 + ((r + i) % 9)},${["Paris", "Lyon", "Nice"][r % 3]}`
    );
    const path = `${FIXTURE_DIR}/baseline_seed_${stamp}_${i}.csv`;
    writeFileSync(path, ["order_id,amount,quantity,city", ...rows].join("\n"), "utf-8");

    const source = await (
      await request.post(`${API}/sources`, { data: { type: "file", connection_config: { path } } })
    ).json();
    const started = await (await request.post(`${API}/ingest/${source.id}`)).json();

    // The baseline is written during ingestion; waiting for the run to leave
    // "running" is enough, and does not wait on any model call.
    const deadline = Date.now() + 120_000;
    while (Date.now() < deadline) {
      const status = await (await request.get(`${API}/ingest/${started.run_id}/status`)).json();
      if (status.status !== "running") break;
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  }
}

test("a long provisional-baseline list is collapsed, with its count visible", async ({ page, request }) => {
  test.setTimeout(600_000);

  const before = await provisionalBaselineCount(request);
  if (before <= COLLAPSE_THRESHOLD) {
    await seedProvisionalBaselines(request, COLLAPSE_THRESHOLD + 1 - before);
  }

  const count = await provisionalBaselineCount(request);
  // Loud, never a skip: if seeding could not produce enough, that is a broken
  // environment or a regression in baseline recording, and it must fail with
  // the numbers that say which.
  expect(
    count,
    `needed more than ${COLLAPSE_THRESHOLD} provisional baselines to exercise the collapse; had ${before}, ` +
      `seeded ${Math.max(0, COLLAPSE_THRESHOLD + 1 - before)} source(s), and still have only ${count}`
  ).toBeGreaterThan(COLLAPSE_THRESHOLD);

  await page.goto("/approvals");

  // Nothing is hidden: the count is in the summary, so a reviewer can see
  // how much is in there without scrolling past all of it.
  const summary = page.getByText(`Show ${count} provisional baselines`);
  await expect(summary).toBeVisible();

  // Collapsed by default: a <details> keeps its content in the DOM, so
  // what matters is that the rows are not on SCREEN until asked for.
  const rows = page.locator("table").filter({ hasText: "Rows" }).first();
  await expect(rows).not.toBeVisible();

  await summary.click();
  await expect(rows).toBeVisible();
});
