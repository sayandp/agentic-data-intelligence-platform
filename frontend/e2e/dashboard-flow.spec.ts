import { test, expect, type Page, type ConsoleMessage } from "./themed-test";
import { mkdirSync, writeFileSync } from "node:fs";

// Drives the REAL React dashboard end to end against the REAL, already
// running local backend (http://127.0.0.1:8000) - no mocks, no API-level
// shortcuts. Every prior verification this project has had was via HTTP
// calls (curl/PowerShell); this is the first thing that actually clicks
// buttons in a browser and reads what's rendered, which is the only way to
// catch "backend is fine but the UI never shows it" - stale state, a
// button wired to the wrong handler, a chart that silently fails to render.

const FIXTURE_DIR = "D:/main_project/backend/data/e2e_test";
const FIXTURE_PATH = `${FIXTURE_DIR}/orders.csv`;
const API_ORIGIN = "http://localhost:8000";

const CLEAN_CSV = [
  "order_id,price,quantity,category",
  ...Array.from({ length: 60 }, (_, i) => `E2E-${i + 1},${(20 + i * 1.5).toFixed(2)},${(i % 5) + 1},widgets`),
].join("\n");

// >5% null rate in price (the null_threshold tolerance) against the baseline
// just established above - deterministically escalates, no auto-fix exists
// for null_threshold by design (see app/gate.py's applicability matrix), so
// this is guaranteed to land in "Escalated validation events", never silently
// auto-applied - a stable target for the Approvals step below.
const CORRUPT_CSV = [
  "order_id,price,quantity,category",
  ...Array.from({ length: 60 }, (_, i) => `E2E-${i + 1},${i % 3 === 0 ? "" : (20 + i * 1.5).toFixed(2)},${(i % 5) + 1},widgets`),
].join("\n");

function trackDiagnostics(page: Page) {
  const consoleErrors: string[] = [];
  const failedRequests: string[] = [];

  page.on("console", (msg: ConsoleMessage) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  page.on("requestfailed", (req) => {
    failedRequests.push(`${req.method()} ${req.url()} - ${req.failure()?.errorText}`);
  });
  page.on("response", (res) => {
    if (res.status() >= 400) failedRequests.push(`${res.status()} ${res.request().method()} ${res.url()}`);
  });

  return { consoleErrors, failedRequests };
}

function reportDiagnostics(testInfo: import("@playwright/test").TestInfo, consoleErrors: string[], failedRequests: string[]) {
  if (consoleErrors.length) {
    testInfo.annotations.push({ type: "console-errors", description: consoleErrors.join("\n") });
  }
  if (failedRequests.length) {
    testInfo.annotations.push({ type: "failed-requests", description: failedRequests.join("\n") });
  }
}

test.describe("Real dashboard - full human flow", () => {
  test("register -> ingest -> confirm baseline -> escalate -> resolve -> report -> audit", async ({ page, context }, testInfo) => {
    // Two real LLM narrative calls in this flow (first run's report, then
    // the escalated/resolved run's), each polled up to 90s - the 3-minute
    // suite default (playwright.config.ts) doesn't leave enough room above
    // this file's own other real-timing waits once both land near their
    // worst case. Same reasoning as demo-full-charts.spec.ts's override.
    test.setTimeout(6 * 60 * 1000);
    const { consoleErrors, failedRequests } = trackDiagnostics(page);
    // Part 1 COPY BUTTON verification below reads the real OS/browser
    // clipboard - needs the permission granted up front (Chromium only,
    // which is what this suite runs under; see playwright.config.ts).
    await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin: "http://localhost:5173" });

    mkdirSync(FIXTURE_DIR, { recursive: true });
    writeFileSync(FIXTURE_PATH, CLEAN_CSV, "utf-8");

    // --- Step 1-2: Sources page - register + ingest the clean file ---
    await page.goto("/sources");
    await expect(page.getByRole("heading", { name: "Sources", exact: true })).toBeVisible();

    const configTextarea = page.locator("textarea");
    await configTextarea.fill(JSON.stringify({ path: FIXTURE_PATH }));
    await page.getByRole("button", { name: "Register", exact: true }).click();

    const registeredLine = page.locator("text=Registered:").first();
    await expect(registeredLine).toBeVisible();
    // Every id in the dashboard now renders via CopyableId as the FULL
    // UUID (not a truncated/hover form) - see components/ui.tsx.
    const sourceId = (await registeredLine.locator("code").textContent())?.trim();
    expect(sourceId, "source id must be readable from the Sources page after registering").toBeTruthy();

    const sourceRow = page.locator("tr", { has: page.locator(`code:text-is("${sourceId}")`) });
    await expect(sourceRow).toBeVisible();
    await sourceRow.getByRole("button", { name: "Ingest", exact: true }).click();

    // Dashboard UX pass, Part 1: runs are now identified as "Run #N", not a
    // raw run_id, everywhere they're shown - including this ingest-result
    // banner.
    const firstIngestResult = sourceRow.locator("text=/Ingest finished:/");
    await expect(firstIngestResult).toBeVisible({ timeout: 30_000 });
    const firstIngestText = (await firstIngestResult.textContent()) ?? "";
    const firstRunMatch = firstIngestText.match(/Run #(\d+)/);
    expect(firstRunMatch, `expected "Run #<number>" in "${firstIngestText}"`).toBeTruthy();
    expect(firstIngestText).toContain("status=completed");
    const firstRunNumber = firstRunMatch![1];

    // Same explore/narrate timing gap the second run's report wait below
    // already accounts for (app/graph/nodes.py::explore_node marks the run
    // "completed" before narrate_node runs) - poll report.available directly
    // before clicking through, rather than assume "status=completed" means
    // the report is ready. Real LLM latency varies with provider load/quota,
    // so this is a generous bound, not a tight one.
    const firstReportDeadline = Date.now() + 90_000;
    let firstReportReady = false;
    while (Date.now() < firstReportDeadline) {
      const statusResp = await page.request.get(`${API_ORIGIN}/ingest/${firstRunNumber}/status`);
      const statusBody = await statusResp.json();
      if (statusBody.report?.available) {
        firstReportReady = true;
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
    expect(firstReportReady, `report for Run #${firstRunNumber} never became available within 90s`).toBe(true);

    // --- Part 2 (UX pass): kill the copy-paste workflow - jump to this
    // run's report via the "View report" link the Sources page shows right
    // next to a finished ingest, no manual id entry anywhere. ---
    const firstRunReportLink = sourceRow.getByRole("link", { name: "View report", exact: true });
    await expect(firstRunReportLink).toBeVisible();
    await firstRunReportLink.click();
    // Part 1: the link navigates with the run NUMBER, not the raw id.
    await expect(page).toHaveURL(new RegExp(`/reports\\?run=${firstRunNumber}$`));

    const firstReportQualityHeading = page.getByRole("heading", { name: "Data quality context" });
    await expect(firstReportQualityHeading).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(`Run #${firstRunNumber}`)).toBeVisible();

    // Part 1 COPY BUTTON: the run header's copy icon still copies the FULL
    // UUID (for API use), never the short "Run #N" form - read the real id
    // straight from the RunLabel's own title attribute (the one place it's
    // exposed) rather than re-deriving it some other way.
    const runLabelSpan = page.locator(`span[title]:has-text("Run #${firstRunNumber}")`).first();
    const firstRunId = await runLabelSpan.getAttribute("title");
    expect(firstRunId, "RunLabel must expose the full run id via its title attribute").toBeTruthy();

    const runHeaderCopyButton = page.getByRole("button", { name: "Copy full run ID (for API use)" });
    await expect(runHeaderCopyButton).toBeVisible();
    await runHeaderCopyButton.click();
    const clipboardText = await page.evaluate(() => navigator.clipboard.readText());
    expect(clipboardText).toBe(firstRunId);
    await expect(page.getByRole("button", { name: "Copied" })).toBeVisible();

    // --- Step 3: Approvals page - confirm the provisional baseline ---
    await page.goto("/approvals");
    await expect(page.getByRole("heading", { name: "Approvals", exact: true })).toBeVisible();

    const baselineRow = page.locator("tr", { has: page.locator(`code:text-is("${sourceId}")`) });

    // Provisional baselines collapse above ~10 entries, so they cannot bury
    // the sections that actually need a decision. Past that threshold a
    // real user expands the list first, and so does this test.
    //
    // Polled rather than checked once: the card renders "Loading..." before
    // the pending payload arrives, so a single up-front check can run while
    // there is nothing to expand yet and then miss the collapsed list
    // entirely. Clicking is guarded on `open` so this stays idempotent
    // across retries rather than toggling the list shut.
    await expect(async () => {
      const summary = page.getByText(/^Show \d+ provisional baselines$/);
      if (await summary.count()) {
        const details = page.locator("details", { has: summary });
        if (!(await details.evaluate((el) => (el as HTMLDetailsElement).open))) {
          await summary.click();
        }
      }
      await expect(baselineRow).toBeVisible({ timeout: 2000 });
    }).toPass({ timeout: 30_000 });
    await baselineRow.getByRole("button", { name: "Confirm", exact: true }).click();

    // No manual reload - this only passes if the app's own post-resolve
    // refresh actually re-renders the list, which is exactly the class of
    // bug ("backend resolved it, UI never noticed") a curl check can't see.
    // Deliberately NOT asserting the whole card reads "None pending." here -
    // this is a shared dev backend, and another source's provisional
    // baseline sitting there legitimately isn't this test's business; the
    // only thing that matters is THIS row is gone.
    await expect(baselineRow).not.toBeVisible({ timeout: 15_000 });

    // --- Corrupt the SAME source's underlying file, re-ingest through the UI ---
    writeFileSync(FIXTURE_PATH, CORRUPT_CSV, "utf-8");

    await page.goto("/sources");
    const sourceRowAgain = page.locator("tr", { has: page.locator(`code:text-is("${sourceId}")`) });
    await sourceRowAgain.getByRole("button", { name: "Ingest", exact: true }).click();

    const secondIngestResult = sourceRowAgain.locator("text=/Ingest finished:/");
    await expect(secondIngestResult).toBeVisible({ timeout: 30_000 });
    const secondIngestText = (await secondIngestResult.textContent()) ?? "";
    expect(secondIngestText, `expected the corrupted re-ingest to escalate, got "${secondIngestText}"`).toContain(
      "status=awaiting_approval",
    );
    const secondRunMatch = secondIngestText.match(/Run #(\d+)/);
    expect(secondRunMatch).toBeTruthy();
    const secondRunNumber = secondRunMatch![1];

    // --- Step 4: Approvals page - resolve the escalated event ---
    // POST /approvals/{id}/resolve for a validation_event used to call
    // graph.invoke(Command(resume=...)) SYNCHRONOUSLY - the same
    // architectural bug narrate_node's LLM calls caused in POST /ingest,
    // just in a second location, discovered by an earlier version of this
    // test. Now fixed identically (app/routers/approvals.py's
    // RESOLVE-HANG FIX docstring): the resolve returns immediately and the
    // graph resumes via BackgroundTasks, so a real UI click here - not a
    // direct-backend-poll workaround - is a genuine test of the fix.
    await page.goto("/approvals");
    // Dashboard redesign (Instrument Panel): the outer Card wrapper and this
    // inner event-group div now share the same rounded-md radius token (the
    // prior design's rounded-xl only happened to disambiguate them by
    // accident) - .p-4 is what's actually unique to the inner group (Card
    // itself uses p-5).
    const eventGroup = page.locator("div.rounded-md.p-4", { has: page.locator(`text="Run #${secondRunNumber}"`) });
    await expect(eventGroup).toBeVisible({ timeout: 15_000 });
    await eventGroup.getByRole("button", { name: "Keep data as-is", exact: true }).click();

    // No manual reload - same reasoning as the baseline step above: this
    // only passes if the app's own post-resolve refresh (now preceded by
    // pollIngestStatus, per ApprovalsPage.tsx's resolve()) actually
    // re-renders the list once the background resume completes.
    await expect(eventGroup).not.toBeVisible({ timeout: 60_000 });

    // --- VERIFY: the resolved item shows up under "Recently resolved" with
    // its decision - the Part 2 UX pass's whole point (distinguishing
    // "nothing ever" from "everything handled"). ---
    // The tightest (innermost) <div> ancestor of the heading is this Card's
    // own wrapping div (Card renders the heading as a direct child) - the
    // LAST match in document order among every div that contains it, since
    // document order lists outer/ancestor divs before their descendants.
    const validationEventsCard = page.locator("div", { has: page.getByRole("heading", { name: "Escalated validation events" }) }).last();
    await validationEventsCard.locator("summary", { hasText: "Recently resolved" }).click();
    // Exactly one item has ever been resolved in this section by this test
    // run, so checking these two facts anywhere within the card - rather
    // than trying to isolate one exact row div, which "Run #N" text alone
    // can match at more than one nesting depth - is unambiguous.
    // .first(): a shared dev backend may already have earlier resolved
    // items with the same decision from a prior test run - presence is
    // what matters here, not uniqueness.
    await expect(validationEventsCard.getByText("rejected_fix_data_acceptable").first()).toBeVisible();
    await expect(validationEventsCard.locator(`text="Run #${secondRunNumber}"`).last()).toBeVisible();

    // --- Step 5: Reports page ---
    // The escalated-and-resolved run's own report - resolving doesn't
    // wait for report-availability itself (only for the resolve OUTCOME),
    // so the same explore/narrate timing gap POST /ingest has applies
    // here too (app/graph/nodes.py::explore_node marks the run
    // "completed" before narrate_node runs). Poll for it directly rather
    // than assume the resolve completing means the report is ready - via
    // the run NUMBER, exercising the exact lookup a human typing one into
    // the load box gets (app/id_lookup.py::resolve_run).
    const reportDeadline = Date.now() + 90_000;
    let reportReady = false;
    while (Date.now() < reportDeadline) {
      const statusResp = await page.request.get(`${API_ORIGIN}/ingest/${secondRunNumber}/status`);
      const statusBody = await statusResp.json();
      if (statusBody.report?.available) {
        reportReady = true;
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
    expect(reportReady, `report for Run #${secondRunNumber} never became available within 90s`).toBe(true);

    // VERIFY: Reports loads via typing the run NUMBER ALONE - no id, no
    // prefix, nothing pasted from anywhere else.
    await page.goto("/reports");
    await page.getByPlaceholder("run number (e.g. 17) or full run id").fill(secondRunNumber);
    await page.getByRole("button", { name: "Load", exact: true }).click();

    const qualityHeading = page.getByRole("heading", { name: "Data quality context" });
    await expect(qualityHeading).toBeVisible({ timeout: 15_000 });
    const narrativeHeading = page.getByRole("heading", { name: "Narrative" });
    await expect(narrativeHeading).toBeVisible();
    await expect(page.getByText(`Run #${secondRunNumber}`)).toBeVisible();

    const qualityBox = await qualityHeading.boundingBox();
    const narrativeBox = await narrativeHeading.boundingBox();
    expect(qualityBox && narrativeBox && qualityBox.y < narrativeBox.y, "quality context must render ABOVE the narrative").toBe(
      true,
    );

    await expect(page.getByText("Generation mode:")).toBeVisible();

    // Chart rendering depends on Plotly loading from a CDN
    // (cdn.plot.ly) - this is exactly the kind of dependency a curl check
    // against the API can never see fail. Wait generously and check for
    // Plotly's own rendered SVG, not just the container div existing.
    const renderedChart = page.locator(".js-plotly-plot svg.main-svg").first();
    await expect(renderedChart).toBeVisible({ timeout: 20_000 });

    // --- Step 6: Audit page ---
    // Same plain-number load as Reports above.
    await page.goto("/audit");
    await page.getByPlaceholder("run number (e.g. 17) or full run id").fill(secondRunNumber);
    await page.getByRole("button", { name: "Load", exact: true }).click();

    await expect(page.getByRole("heading", { name: /Timeline/ })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(`Run #${secondRunNumber}`)).toBeVisible();
    for (const edge of ["await_human", "resolve", "explore"]) {
      await expect(page.locator(`td code:text-is("${edge}")`).first()).toBeVisible();
    }

    reportDiagnostics(testInfo, consoleErrors, failedRequests);
    expect(consoleErrors, `unexpected browser console errors during the flow:\n${consoleErrors.join("\n")}`).toEqual([]);
  });
});
