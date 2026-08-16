import { mkdirSync, writeFileSync } from "node:fs";

import { expect, type APIRequestContext } from "@playwright/test";

import { test } from "./themed-test";

const API = "http://127.0.0.1:8000";
const FIXTURE_DIR = "D:/main_project/backend/data/e2e_upload";

/**
 * THE DELIVERABLE OF THIS CHANGE.
 *
 * The Marketing page's upload is an ENTRY POINT, not a second pipeline. It
 * posts to the same /sources/upload and /ingest endpoints the Sources page
 * uses and goes through the same detection, validation and graph run.
 *
 * The way to prove that is not to read the code - it is to put the SAME FILE
 * through both entry points in a real browser and assert the two runs are
 * indistinguishable. Two ingest paths that must agree is the failure this
 * codebase has hit twice already (two resolve_run functions; client-side vs
 * backend chart derivation), and a claim of "same pipeline" is worth exactly
 * as much as the test behind it.
 */

const ADS_CSV = (() => {
  const header = "Reporting starts,Ad set name,Amount spent (USD),Impressions,Link clicks,Frequency,Results,Purchase conversion value";
  const rows: string[] = [];
  const start = new Date("2026-01-01T00:00:00Z");
  for (let day = 0; day < 21; day += 1) {
    const date = new Date(start.getTime() + day * 86_400_000).toISOString().slice(0, 10);
    for (const adset of ["Prospecting - Broad", "Retargeting - 7d", "Lookalike 1%"]) {
      const frequency = adset === "Retargeting - 7d" ? 4.6 : 2.2;
      const conversions = day >= 11 && adset === "Prospecting - Broad" ? 2 : 9;
      const clicks = day >= 11 && adset === "Prospecting - Broad" ? 70 : 210;
      rows.push(
        `${date},${adset},"$${(140 + day).toFixed(2)}",${12000 + day * 25},${clicks},${frequency},${conversions},${700 + day * 3}`
      );
    }
  }
  return [header, ...rows].join("\n");
})();

const NON_ADS_CSV = [
  "order_id,order_date,cost,region",
  ...Array.from({ length: 40 }, (_, i) => {
    const date = new Date(Date.UTC(2026, 0, 1 + i)).toISOString().slice(0, 10);
    return `ORD-${i + 1},${date},${(10 + i).toFixed(2)},${["north", "south", "east"][i % 3]}`;
  }),
].join("\n");

function writeFixture(name: string, contents: string): string {
  mkdirSync(FIXTURE_DIR, { recursive: true });
  const path = `${FIXTURE_DIR}/${name}`;
  writeFileSync(path, contents, "utf-8");
  return path;
}

/** Everything about a run that must NOT depend on which page uploaded it. */
async function runShape(request: APIRequestContext, runNumber: number) {
  const status = await (await request.get(`${API}/ingest/${runNumber}/status`)).json();
  const marketing = await (await request.get(`${API}/marketing/${runNumber}`)).json();
  const audit = await (await request.get(`${API}/audit/${runNumber}`)).json();

  const events = (audit.validation_events ?? []).map(
    (e: { rule_failed: string; column_name: string | null; state: string }) =>
      `${e.rule_failed.split(":")[0]}|${e.column_name}|${e.state}`
  );

  const findings = (marketing.findings ?? []).map(
    (f: { finding_type: string; severity: string; payload: { scope?: string | null } }) =>
      `${f.finding_type}|${f.severity}|${f.payload?.scope ?? ""}`
  );

  return {
    terminalStatus: status.status,
    columnTypes: status.metadata?.column_types ?? {},
    validationEvents: [...events].sort(),
    applicable: marketing.applicable,
    resolvedRoles: Object.fromEntries(
      Object.entries(marketing.resolved_roles ?? {}).map(([role, v]) => [role, (v as { column: string }).column])
    ),
    missingRoles: [...(marketing.missing_roles ?? [])].sort(),
    findings: [...findings].sort(),
    skippedRules: [...(marketing.skipped_rules ?? []).map((s: { rule: string }) => s.rule)].sort(),
  };
}

/** Upload via the Sources page, then ingest from its table row. */
async function uploadFromSources(page: import("@playwright/test").Page, path: string): Promise<number> {
  await page.goto("/sources");
  await expect(page.getByRole("heading", { name: "Sources", exact: true })).toBeVisible();

  await page.locator("#sources-upload-file").setInputFiles(path);
  await page.getByRole("button", { name: "Upload", exact: true }).click();

  const registered = page.locator("text=Uploaded and registered:").first();
  await expect(registered).toBeVisible({ timeout: 30_000 });
  const sourceId = (await registered.locator("code").textContent())?.trim();
  expect(sourceId).toBeTruthy();

  const row = page.locator("tr", { has: page.locator(`code:text-is("${sourceId}")`) });
  await row.getByRole("button", { name: "Ingest", exact: true }).click();

  const result = row.locator("text=/Ingest finished:/");
  await expect(result).toBeVisible({ timeout: 120_000 });
  const text = (await result.textContent()) ?? "";
  const match = text.match(/Run #(\d+)/);
  expect(match, `expected a run number in "${text}"`).toBeTruthy();
  return Number(match![1]);
}

/** Upload via the Marketing page, which uploads AND ingests in one action. */
async function uploadFromMarketing(page: import("@playwright/test").Page, path: string): Promise<number> {
  await page.goto("/marketing");
  await expect(page.getByRole("heading", { name: "Upload an ad-platform export" })).toBeVisible();

  await page.locator("#marketing-upload-file").setInputFiles(path);
  await page.getByRole("button", { name: "Upload and analyse", exact: true }).click();

  // It lands on the run's own Marketing view - the only permitted difference
  // between the two entry points.
  await expect(page).toHaveURL(/\/marketing\?run=\d+/, { timeout: 180_000 });
  const runNumber = Number(new URL(page.url()).searchParams.get("run"));
  expect(Number.isFinite(runNumber)).toBe(true);
  return runNumber;
}

test("the same file through both entry points produces equivalent runs", async ({ page, request }) => {
  const path = writeFixture("equivalence_ads.csv", ADS_CSV);

  const sourcesRun = await uploadFromSources(page, path);
  const marketingRun = await uploadFromMarketing(page, path);
  expect(sourcesRun).not.toBe(marketingRun);

  const viaSources = await runShape(request, sourcesRun);
  const viaMarketing = await runShape(request, marketingRun);

  // Everything that describes WHAT HAPPENED must match. Only the page the
  // user landed on afterwards is allowed to differ.
  expect(viaMarketing.terminalStatus, "terminal state differs between entry points").toBe(viaSources.terminalStatus);
  expect(viaMarketing.columnTypes, "detected column types differ").toEqual(viaSources.columnTypes);
  expect(viaMarketing.validationEvents, "validation events differ").toEqual(viaSources.validationEvents);
  expect(viaMarketing.applicable, "marketing applicability differs").toBe(viaSources.applicable);
  expect(viaMarketing.resolvedRoles, "semantic roles differ").toEqual(viaSources.resolvedRoles);
  expect(viaMarketing.missingRoles, "missing roles differ").toEqual(viaSources.missingRoles);
  expect(viaMarketing.findings, "marketing findings differ").toEqual(viaSources.findings);
  expect(viaMarketing.skippedRules, "skipped rules differ").toEqual(viaSources.skippedRules);

  // And the run genuinely did something, so the comparison is not two
  // identical empties passing trivially.
  expect(viaSources.applicable, "the ads fixture should qualify").toBe(true);
  expect(viaSources.findings.length).toBeGreaterThan(0);
});

test("a non-ads file uploaded from Marketing completes and reports what was missing", async ({ page, request }) => {
  const path = writeFixture("equivalence_non_ads.csv", NON_ADS_CSV);
  const runNumber = await uploadFromMarketing(page, path);

  // NOT an error, and the run is not discarded - it is a valid run that
  // simply has no marketing findings.
  const shape = await runShape(request, runNumber);
  expect(shape.terminalStatus).toBe("completed");
  expect(shape.applicable).toBe(false);

  // The existing capability report is what the user sees, naming what was
  // missing rather than showing a failure.
  await expect(page.getByText("This run is not an ad-campaign export")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/Marketing analysis (also )?needs/)).toBeVisible();
  await expect(page.getByText(/^Missing: /)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Key values" })).toHaveCount(0);
});

test("upload progress reports elapsed time and never shows an unbounded spinner", async ({ page }) => {
  const path = writeFixture("equivalence_ads.csv", ADS_CSV);
  await page.goto("/marketing");
  await page.locator("#marketing-upload-file").setInputFiles(path);
  await page.getByRole("button", { name: "Upload and analyse", exact: true }).click();

  // Either an elapsed-time tick or the finished line - never a spinner with
  // no number attached to it.
  await expect(
    page.getByText(/\(\d+s elapsed - still running, not stuck\)|Ingest finished:/).first()
  ).toBeVisible({ timeout: 120_000 });
  await expect(page).toHaveURL(/\/marketing\?run=\d+/, { timeout: 180_000 });
});

test("Sources still uploads and ingests unchanged after the extraction", async ({ page, request }) => {
  const path = writeFixture("equivalence_ads.csv", ADS_CSV);
  const runNumber = await uploadFromSources(page, path);

  const shape = await runShape(request, runNumber);
  expect(shape.terminalStatus).toBe("completed");
  expect(shape.applicable).toBe(true);
});
