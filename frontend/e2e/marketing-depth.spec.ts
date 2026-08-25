import { mkdirSync, writeFileSync } from "node:fs";

import { expect, type APIRequestContext } from "@playwright/test";

import { test } from "./themed-test";

const API = "http://127.0.0.1:8000";
const FIXTURE_DIR = "D:/main_project/backend/data/e2e_upload";

/**
 * Part 3: marketing depth, driven in a real browser.
 *
 * The claim worth proving here is the INTERACTIVITY, and specifically that it
 * is deterministic: selecting an ad set narrows the view using findings the
 * run already persisted, with no new request and no model call. So the test
 * counts network requests across the interaction as well as reading the page.
 *
 * Account-wide figures stay visible under a filter on purpose - an ad set is
 * read against the account it sits in, and hiding the totals would leave a
 * reader comparing a number to nothing.
 */

const ADSETS = ["Prospecting", "Retargeting", "Lookalike", "Scaling"] as const;
const SPEND: Record<string, number> = { Prospecting: 200, Retargeting: 120, Lookalike: 40, Scaling: 60 };

const ADS_CSV = (() => {
  const header =
    "Reporting starts,Ad set name,Amount spent (USD),Impressions,Link clicks,Frequency,Results,Purchase conversion value";
  const rows: string[] = [];
  for (let day = 0; day < 28; day += 1) {
    const date = new Date(Date.UTC(2026, 0, 1 + day)).toISOString().slice(0, 10);
    for (const adset of ADSETS) {
      const tiring = adset === "Retargeting";
      const scaling = adset === "Scaling";
      const impressions = 10000 + day * 10;
      const ctr = 0.05 - (tiring ? day * 0.001 : 0) + (scaling ? day * 0.0005 : 0);
      const clicks = Math.max(1, Math.floor(impressions * ctr));
      const spend = SPEND[adset];
      rows.push(
        `${date},${adset},${spend.toFixed(2)},${impressions},${clicks},${(2 + (tiring || scaling ? day * 0.12 : 0)).toFixed(2)},${Math.max(1, Math.floor(clicks * 0.05))},${(spend * 1.5).toFixed(2)}`
      );
    }
  }
  return [header, ...rows].join("\n");
})();

function writeFixture(name: string, contents: string): string {
  mkdirSync(FIXTURE_DIR, { recursive: true });
  const path = `${FIXTURE_DIR}/${name}`;
  writeFileSync(path, contents, "utf-8");
  return path;
}

async function ingestAds(request: APIRequestContext, name: string): Promise<number> {
  const path = writeFixture(name, ADS_CSV);
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

/** The marketing row is written by the analytics stage, a beat after the run
 *  reports completed - the same completed-but-not-yet-written window the
 *  report and summary views both poll through. */
async function waitForMarketing(request: APIRequestContext, runNumber: number) {
  const deadline = Date.now() + 120_000;
  while (Date.now() < deadline) {
    const response = await request.get(`${API}/marketing/${runNumber}`);
    if (response.ok()) {
      const body = await response.json();
      if (body.applicable !== undefined) return body;
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error(`run ${runNumber} never produced a marketing analysis within 120s`);
}

test("the depth analyses appear on the marketing page", async ({ page, request }) => {
  const runNumber = await ingestAds(request, "depth_ads.csv");

  // Read the API first, so the UI assertions cannot pass vacuously.
  const marketing = await waitForMarketing(request, runNumber);
  expect(marketing.applicable, `run did not qualify: ${marketing.not_applicable_reason}`).toBe(true);
  const kinds = new Set((marketing.findings ?? []).map((f: { finding_type: string }) => f.finding_type));
  expect(kinds.has("spend_concentration"), "spend concentration did not run").toBe(true);

  await page.goto(`/marketing?run=${runNumber}`);
  await expect(page.getByRole("heading", { name: "Where the budget goes, and where the return comes from" })).toBeVisible({
    timeout: 120_000,
  });
  await expect(page.getByRole("heading", { name: "Ad sets", exact: true })).toBeVisible();
});

test("selecting an ad set filters the view without any new request", async ({ page, request }) => {
  const runNumber = await ingestAds(request, "depth_ads_filter.csv");
  await waitForMarketing(request, runNumber);
  await page.goto(`/marketing?run=${runNumber}`);
  await expect(page.getByRole("heading", { name: "Ad sets", exact: true })).toBeVisible({ timeout: 120_000 });

  // Count backend requests made from here on. Deterministic filtering means
  // zero: the findings are already on the page.
  const requests: string[] = [];
  page.on("request", (r) => {
    if (r.url().includes("127.0.0.1:8000") || r.url().includes("localhost:8000")) requests.push(r.url());
  });

  const chip = page.getByTestId("marketing-adset-chip").filter({ hasText: "Retargeting" }).first();
  await chip.click();

  await expect(page.getByTestId("marketing-filter-note")).toContainText("Retargeting");
  await expect(chip).toHaveAttribute("aria-pressed", "true");
  expect(requests, `filtering issued backend requests: ${requests.join(", ")}`).toEqual([]);
});

test("account-wide figures stay visible while an ad set is selected", async ({ page, request }) => {
  const runNumber = await ingestAds(request, "depth_ads_scope.csv");
  await waitForMarketing(request, runNumber);
  await page.goto(`/marketing?run=${runNumber}`);
  await expect(page.getByRole("heading", { name: "Ad sets", exact: true })).toBeVisible({ timeout: 120_000 });

  await page.getByTestId("marketing-adset-chip").filter({ hasText: "Retargeting" }).first().click();

  // Key values are account-wide and carry no scope, so they must survive.
  await expect(page.getByRole("heading", { name: "Key values", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Where the budget goes, and where the return comes from" })).toBeVisible();
});

test("selecting the same ad set again clears the filter", async ({ page, request }) => {
  const runNumber = await ingestAds(request, "depth_ads_toggle.csv");
  await waitForMarketing(request, runNumber);
  await page.goto(`/marketing?run=${runNumber}`);
  await expect(page.getByRole("heading", { name: "Ad sets", exact: true })).toBeVisible({ timeout: 120_000 });

  const chip = page.getByTestId("marketing-adset-chip").filter({ hasText: "Retargeting" }).first();
  await chip.click();
  await expect(page.getByTestId("marketing-filter-note")).toBeVisible();

  await chip.click();
  await expect(page.getByTestId("marketing-filter-note")).toHaveCount(0);
  await expect(chip).toHaveAttribute("aria-pressed", "false");
});
