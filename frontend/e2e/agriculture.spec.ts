import { mkdirSync, writeFileSync } from "node:fs";

import { expect, type APIRequestContext } from "@playwright/test";

import { test } from "./themed-test";

const API = "http://127.0.0.1:8000";
const FIXTURE_DIR = "D:/main_project/backend/data/e2e_upload";

/**
 * Part 4: the agriculture domain pack, in a real browser.
 *
 * The claims worth proving here are the ones about the SEAMS, not about the
 * rules - those are covered deterministically in the backend suite. What a
 * browser can show is that a second domain pack behaves like the first: its
 * tab appears only when a run qualifies, a run that does not qualify sees the
 * reason rather than a blank page, and the report, comparison and summary all
 * work on an agriculture run with no agriculture-specific handling.
 */

const DISTRICTS = ["Palakkad", "Thrissur", "Wayanad", "Idukki"];
const CROPS = ["Rice", "Coconut", "Banana"];
const SEASONS = ["Kharif", "Rabi", "Whole Year"];
const BASE_AREA: Record<string, number> = { Rice: 1200, Coconut: 800, Banana: 300 };
const BASE_YIELD: Record<string, number> = { Rice: 3, Coconut: 6, Banana: 12 };

const CROP_CSV = (() => {
  const header = "District_Name,Crop_Year,Season,Crop,Area,Production,Rainfall_mm";
  const rows: string[] = [];
  for (let year = 2015; year < 2023; year += 1) {
    for (const district of DISTRICTS) {
      for (const crop of CROPS) {
        const collapsing = district === "Wayanad" && crop === "Rice" && year === 2022;
        const area = BASE_AREA[crop] * (collapsing ? 0.5 : 1);
        const perHa = BASE_YIELD[crop] * (collapsing ? 0.35 : 1);
        rows.push(
          `${district},${year},${SEASONS[year % SEASONS.length]},${crop},${area.toFixed(2)},${(area * perHa).toFixed(2)},${(2400 + (year % 5) * 40).toFixed(1)}`
        );
      }
    }
  }
  return [header, ...rows].join("\n");
})();

const ORDERS_CSV = ["order_id,amount,city", ...Array.from({ length: 60 }, (_, i) => `${i},10.0,Paris`)].join("\n");

function writeFixture(name: string, contents: string): string {
  mkdirSync(FIXTURE_DIR, { recursive: true });
  const path = `${FIXTURE_DIR}/${name}`;
  writeFileSync(path, contents, "utf-8");
  return path;
}

async function ingest(request: APIRequestContext, name: string, contents: string): Promise<number> {
  const path = writeFixture(name, contents);
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

/** The agriculture row is written by the same stage the marketing one is,
 *  a beat after the run reports completed. */
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
  throw new Error(`run ${runNumber} never produced an agriculture analysis within 120s`);
}

test("a qualifying run shows key values, then what needs a decision", async ({ page, request }) => {
  const runNumber = await ingest(request, "agri_crops.csv", CROP_CSV);

  const body = await waitForAgriculture(request, runNumber);
  expect(body.applicable, `run did not qualify: ${body.not_applicable_reason}`).toBe(true);

  await page.goto(`/agriculture?run=${runNumber}`);
  await expect(page.getByRole("heading", { name: "Key values", exact: true })).toBeVisible({ timeout: 120_000 });
  await expect(page.getByRole("heading", { name: "Needs a decision", exact: true })).toBeVisible();
  await expect(page.getByText("Total production")).toBeVisible();
});

test("every warning states its rule, observed value, threshold and comparison basis", async ({ page, request }) => {
  const runNumber = await ingest(request, "agri_crops_warn.csv", CROP_CSV);
  const body = await waitForAgriculture(request, runNumber);

  const warnings = (body.findings ?? []).filter((f: { severity: string }) => f.severity === "warning");
  expect(warnings.length, "the fixture must produce at least one warning").toBeGreaterThan(0);

  await page.goto(`/agriculture?run=${runNumber}`);
  await expect(page.getByRole("heading", { name: "Needs a decision", exact: true })).toBeVisible({ timeout: 120_000 });

  const section = page.locator("li").filter({ hasText: "Compared against" }).first();
  await expect(section).toContainText("observed");
  await expect(section).toContainText("threshold");
  await expect(section).toContainText("Compared against");
});

test("a non-agricultural run says why it does not qualify and names what was missing", async ({ page, request }) => {
  const runNumber = await ingest(request, "agri_orders.csv", ORDERS_CSV);
  const body = await waitForAgriculture(request, runNumber);

  expect(body.applicable).toBe(false);

  await page.goto(`/agriculture?run=${runNumber}`);
  await expect(page.getByRole("heading", { name: "This run is not agricultural production data" })).toBeVisible({
    timeout: 120_000,
  });
  await expect(page.getByText(/Roles not found/)).toBeVisible();
});

test("agriculture charts render from the persisted spec", async ({ page, request }) => {
  const runNumber = await ingest(request, "agri_figures.csv", CROP_CSV);
  const body = await waitForAgriculture(request, runNumber);
  expect(body.charts?.length, "the run must persist chart specs").toBeGreaterThan(0);

  await page.goto(`/agriculture?run=${runNumber}`);
  await expect(page.getByRole("heading", { name: "Average yield by crop year" })).toBeVisible({ timeout: 120_000 });

  // Rendered from the persisted figure_json, not derived in the browser.
  const plot = page.locator("#agriculture-chart-yield_over_seasons .plot-container").first();
  await expect(plot).toBeVisible({ timeout: 30_000 });
});

test("the run comparison and session summary work on an agriculture run", async ({ request }) => {
  // Two COMPLETE pipeline runs, each through a real model on the narrative and
  // summary stages. That is inherently slower than the 3-minute default, and
  // it timed out under full-suite load while passing standalone. Declared here
  // rather than trimming the test: comparing two runs needs two runs.
  test.setTimeout(600_000);
  const path = writeFixture("agri_compare.csv", CROP_CSV);
  const source = await (
    await request.post(`${API}/sources`, { data: { type: "file", connection_config: { path } } })
  ).json();

  const runs: number[] = [];
  for (let i = 0; i < 2; i += 1) {
    const started = await (await request.post(`${API}/ingest/${source.id}`)).json();
    const deadline = Date.now() + 300_000;
    while (Date.now() < deadline) {
      const status = await (await request.get(`${API}/ingest/${started.run_id}/status`)).json();
      if (status.status === "completed") {
        runs.push(status.run_number);
        break;
      }
      if (status.status === "failed") throw new Error("ingest failed");
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  }
  expect(runs.length).toBe(2);
  await waitForAgriculture(request, runs[1]);

  // The comparison iterates the domain-pack registry: agriculture must appear
  // as its own section with no consumer edit.
  const comparison = await (await request.get(`${API}/compare?run_a=${runs[0]}&run_b=${runs[1]}`)).json();
  const sections = (comparison.sections ?? []).map((s: { name: string }) => s.name);
  expect(sections, `agriculture is not a comparison section: ${sections}`).toContain("agriculture");
  expect(sections).toContain("marketing");

  // And the summary is written for the run like any other.
  const deadline = Date.now() + 180_000;
  let summary: { available?: boolean } = {};
  while (Date.now() < deadline) {
    const response = await request.get(`${API}/summary/${runs[1]}`);
    if (response.ok()) {
      summary = await response.json();
      if (summary.available) break;
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  expect(summary.available, "an agriculture run must still get a session summary").toBe(true);
});
