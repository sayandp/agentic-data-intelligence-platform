import { mkdirSync, writeFileSync } from "node:fs";

import { expect, type APIRequestContext } from "@playwright/test";

import { test } from "./themed-test";

const API = "http://127.0.0.1:8000";
const FIXTURE_DIR = "D:/main_project/backend/data/e2e_upload";

/**
 * The Privacy section, driven as a person would drive it.
 *
 * Two things are worth proving in a browser rather than in a unit test.
 *
 * First, that the section states the SPLIT. Candidate columns are masked on
 * the diagnosis, query and modeling paths and sent in the clear to the
 * narrative path, because that is what the measurements said: diagnosis lost
 * nothing to redaction, the narrative lost a finding. A UI that said only
 * "masked" would be claiming a protection the system does not provide on
 * every path, which is worse than saying nothing.
 *
 * Second, that clearing a column WORKS from the page, and that the one
 * clearing the system refuses - a column detection verified as personal - is
 * actually refused rather than accepted and quietly ignored.
 */

const PII_CSV = [
  "customer_name,email,notes,amount",
  ...Array.from(
    { length: 60 },
    (_, i) => `Person ${i},p${i}@example.com,handwritten note about order ${i},${(i + 1) * 10}`
  ),
].join("\n");

function writeFixture(name: string, contents: string): string {
  mkdirSync(FIXTURE_DIR, { recursive: true });
  const path = `${FIXTURE_DIR}/${name}`;
  writeFileSync(path, contents, "utf-8");
  return path;
}

/** Upload and ingest through the Sources page, returning the run number. */
async function ingestFixture(page: import("@playwright/test").Page, path: string): Promise<number> {
  await page.goto("/sources");
  await page.locator("#sources-upload-file").setInputFiles(path);
  await page.getByRole("button", { name: "Upload", exact: true }).click();

  const registered = page.locator("text=Uploaded and registered:").first();
  await expect(registered).toBeVisible({ timeout: 30_000 });
  const sourceId = (await registered.locator("code").textContent())?.trim();

  const row = page.locator("tr", { has: page.locator(`code:text-is("${sourceId}")`) });
  await row.getByRole("button", { name: "Ingest", exact: true }).click();

  const result = row.locator("text=/Ingest finished:/");
  await expect(result).toBeVisible({ timeout: 180_000 });
  const match = ((await result.textContent()) ?? "").match(/Run #(\d+)/);
  expect(match).toBeTruthy();
  return Number(match![1]);
}

/**
 * The Privacy section renders only once the run HAS a report, and a run
 * reports "completed" before the narrative stage finishes - the same
 * completed-but-not-yet-narrated window ReportsPage itself polls through.
 * Navigating on run completion alone lands on the page a beat early and the
 * section is simply not there yet, which reads as a missing feature rather
 * than as a race.
 */
async function waitForReport(request: APIRequestContext, runNumber: number) {
  const deadline = Date.now() + 180_000;
  while (Date.now() < deadline) {
    const response = await request.get(`${API}/reports/${runNumber}`);
    if (response.ok()) return;
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error(`run ${runNumber} never produced a report within 180s`);
}

async function openPrivacy(
  page: import("@playwright/test").Page,
  request: APIRequestContext,
  runNumber: number
) {
  await waitForReport(request, runNumber);
  await page.goto(`/reports?run=${runNumber}`);
  await expect(page.getByRole("heading", { name: "Privacy", exact: true })).toBeVisible({ timeout: 60_000 });
}

test("the Privacy section names which columns are masked and on which paths", async ({ page, request }) => {
  const runNumber = await ingestFixture(page, writeFixture("privacy_people.csv", PII_CSV));
  await openPrivacy(page, request, runNumber);

  // Verified PII: masked on every path, with its evidence.
  const everywhere = page.locator("li", { hasText: "email" }).first();
  await expect(everywhere).toContainText("masked");
  await expect(everywhere).toContainText("100% of values matched");

  // A candidate: masked by default, and the section says exactly where it is
  // NOT. Asserted on the two path lists themselves, not with a regex over the
  // section - a loose regex here matched the static prose underneath and kept
  // passing with the split deliberately broken.
  await expect(page.getByTestId("privacy-strict-paths")).toHaveText("diagnosis, query, modeling");
  await expect(page.getByTestId("privacy-permissive-paths")).toHaveText("narrative");
  await expect(page.locator("li", { hasText: "customer_name" }).first()).toContainText("masked by default");
});

test("a person can mark a candidate not personal, and the page says who did", async ({ page, request }) => {
  const runNumber = await ingestFixture(page, writeFixture("privacy_people.csv", PII_CSV));
  await openPrivacy(page, request, runNumber);

  page.once("dialog", (dialog) => dialog.accept("sayan"));
  await page
    .locator("li", { hasText: "notes" })
    .first()
    .getByRole("button", { name: "mark not personal" })
    .click();

  const cleared = page.locator("li", { hasText: "notes" }).first();
  await expect(cleared).toContainText("cleared", { timeout: 30_000 });
  await expect(cleared).toContainText("marked by sayan");

  // The decision is stored, not just rendered: a reload shows it too.
  await page.reload();
  await expect(page.locator("li", { hasText: "notes" }).first()).toContainText("marked by sayan", {
    timeout: 180_000,
  });
});

test("a decision can be undone from the page", async ({ page, request }) => {
  const runNumber = await ingestFixture(page, writeFixture("privacy_people.csv", PII_CSV));
  await openPrivacy(page, request, runNumber);

  page.once("dialog", (dialog) => dialog.accept("sayan"));
  await page.locator("li", { hasText: "notes" }).first().getByRole("button", { name: "mark not personal" }).click();
  await expect(page.locator("li", { hasText: "notes" }).first()).toContainText("cleared", { timeout: 30_000 });

  await page.locator("li", { hasText: "notes" }).first().getByRole("button", { name: "undo" }).click();

  await expect(page.locator("li", { hasText: "notes" }).first()).toContainText("masked by default", {
    timeout: 30_000,
  });
});

test("verified personal data cannot be cleared, and the refusal is shown", async ({ page, request }) => {
  const runNumber = await ingestFixture(page, writeFixture("privacy_people.csv", PII_CSV));

  // The UI offers no button for this, which is the first line of defence.
  // The API refusing it is the one that matters, because the button's
  // absence is not a guarantee.
  const response = await request.post(`${API}/privacy/${runNumber}/decisions`, {
    data: { column: "email", decision: "not_personal", marked_by: "sayan" },
  });

  expect(response.status()).toBe(400);
  expect((await response.json()).detail).toContain("cannot be marked not personal");

  await openPrivacy(page, request, runNumber);
  await expect(page.locator("li", { hasText: "email" }).first()).toContainText("masked");
});
