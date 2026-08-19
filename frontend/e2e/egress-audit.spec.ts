import { mkdirSync, writeFileSync } from "node:fs";

import { expect, type APIRequestContext } from "@playwright/test";

import { test } from "./themed-test";

const API = "http://127.0.0.1:8000";
const FIXTURE_DIR = "D:/main_project/backend/data/e2e_upload";

/**
 * The Egress section of the run audit view.
 *
 * Two claims are worth proving in a browser. That the section reports what
 * actually left - agent, provider, model, policy, how much, which columns
 * were included and which were masked. And that it reports it WITHOUT the
 * payload: no cell, no expander, and no API field holds the rows themselves.
 *
 * The second is the one that matters. An audit log that proved protection by
 * storing the data it protected would be a second unclassified copy of
 * exactly the wrong thing, so this searches the rendered page AND the API
 * response for the fixture's real values.
 *
 * Setup goes through the API rather than the Sources page. The subject here
 * is the audit view, not the upload flow (e2e/marketing-upload.spec.ts owns
 * that), and driving an unrelated screen to reach this one only adds a way
 * for this spec to fail for reasons that have nothing to do with egress.
 */

const PII_CSV = [
  "customer_name,email,notes,amount",
  ...Array.from(
    { length: 60 },
    (_, i) => `Person ${i},p${i}@example.com,handwritten note about order ${i},${(i + 1) * 10}`
  ),
].join("\n");

const CLEAN_CSV = ["region,amount", ...Array.from({ length: 60 }, (_, i) => `north,${i}`)].join("\n");

/** Values that must not appear on the page or in the API response. */
const SECRETS = ["p3@example.com", "Person 3", "handwritten note about order 7"];

function writeFixture(name: string, contents: string): string {
  mkdirSync(FIXTURE_DIR, { recursive: true });
  const path = `${FIXTURE_DIR}/${name}`;
  writeFileSync(path, contents, "utf-8");
  return path;
}

async function ingest(request: APIRequestContext, name: string, contents: string): Promise<number> {
  const path = writeFixture(name, contents);
  const source = await (
    await request.post(`${API}/sources`, {
      data: { type: "file", connection_config: { path } },
    })
  ).json();

  const started = await (await request.post(`${API}/ingest/${source.id}`)).json();
  const runId = started.run_id;

  const deadline = Date.now() + 240_000;
  let status = started.status;
  while (Date.now() < deadline) {
    const body = await (await request.get(`${API}/ingest/${runId}/status`)).json();
    status = body.status;
    if (["completed", "failed", "awaiting_approval"].includes(status)) {
      expect(body.run_number, "the run must have a number to open in the audit view").toBeTruthy();
      return body.run_number;
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error(`ingest did not settle within 240s (last status: ${status})`);
}

/**
 * A run reports "completed" BEFORE the narrative stage has finished - the
 * same completed-but-not-yet-narrated window ReportsPage polls through. The
 * narrative call is the one this spec depends on, so waiting for the run
 * status alone reads the audit trail a beat too early and sees an empty
 * egress list. Waits for the report, which is the signal that narrate ran.
 */
async function waitForNarrative(request: APIRequestContext, runNumber: number) {
  const deadline = Date.now() + 180_000;
  while (Date.now() < deadline) {
    const audit = await (await request.get(`${API}/audit/${runNumber}`)).json();
    if (audit.report) return audit;
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error(`run ${runNumber} never produced a report within 180s`);
}

async function openAudit(page: import("@playwright/test").Page, runNumber: number) {
  await page.goto(`/audit?run=${runNumber}`);
  await expect(page.getByText("Egress - what left this machine")).toBeVisible({ timeout: 60_000 });
}

test("the Egress section reports every outbound call as shape only", async ({ page, request }) => {
  const runNumber = await ingest(request, "egress_people.csv", PII_CSV);

  // Read the API first: if the run recorded nothing, the UI assertions below
  // would pass vacuously against an empty table.
  const audit = await waitForNarrative(request, runNumber);
  expect(audit.egress.length, "the run must have called a model for this test to mean anything").toBeGreaterThan(0);

  await openAudit(page, runNumber);

  const table = page.locator("table").filter({ hasText: "Provider / model" });
  await expect(table).toBeVisible();
  await expect(table).toContainText(audit.egress[0].provider);
  await expect(table).toContainText(audit.egress[0].agent);
  // Column names ARE shown - the model receives the schema anyway, and a
  // reader needs to know which columns were involved.
  await expect(table).toContainText("amount");
  // The policy in force is part of what was disclosed.
  await expect(table).toContainText(audit.egress[0].policy);
});

test("neither the page nor the API carries the data that was sent", async ({ page, request }) => {
  const runNumber = await ingest(request, "egress_people_2.csv", PII_CSV);

  const audit = await waitForNarrative(request, runNumber);
  expect(audit.egress.length).toBeGreaterThan(0);

  const apiBody = JSON.stringify(audit.egress);
  for (const secret of SECRETS) {
    expect(apiBody, `the /audit egress payload leaked ${secret}`).not.toContain(secret);
  }
  // Not even a masked token: a token is derived from a value.
  for (const token of ["<EMAIL_", "<NAME_", "<TEXT_"]) {
    expect(apiBody, `the /audit egress payload leaked a ${token} token`).not.toContain(token);
  }

  // And there is no field that could hold rows in the first place.
  for (const entry of audit.egress) {
    expect(Object.keys(entry).sort()).toEqual([
      "agent",
      "columns",
      "created_at",
      "id",
      "masked_value_counts",
      "model",
      "policy",
      "provider",
      "redacted_columns",
      "row_count",
      "unit",
    ]);
  }

  await openAudit(page, runNumber);
  const rendered = (await page.locator("body").textContent()) ?? "";
  for (const secret of SECRETS) {
    expect(rendered, `the audit page rendered ${secret}`).not.toContain(secret);
  }
});

test("a run that disclosed nothing says so rather than showing an empty table", async ({ page, request }) => {
  const runNumber = await ingest(request, "egress_clean.csv", CLEAN_CSV);
  const audit = await waitForNarrative(request, runNumber);

  await openAudit(page, runNumber);
  const section = page.locator("div").filter({ hasText: "Egress - what left this machine" }).last();

  if (audit.egress.length === 0) {
    await expect(section).toContainText("sent nothing to a third-party model");
  } else {
    // It did call out - then it must show the calls, not the empty state.
    await expect(section).toContainText("Provider / model");
    await expect(section).not.toContainText("sent nothing to a third-party model");
  }
});
