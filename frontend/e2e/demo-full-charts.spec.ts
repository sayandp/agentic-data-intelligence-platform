import { test, expect } from "@playwright/test";

// Dashboard demo pass, Part 2/3: data/demo_full.csv (backend/scripts/
// generate_demo_full.py) is the one fixture in this repo with a real trend
// AND a real correlation strong enough to produce EVERY deterministic
// chart type app/narrative/charts.py can draw at once - every other E2E
// fixture in this suite was built narrower (one escalation path at a time).
// This also doubles as the Predict page's own demo: revenue (the trending
// column) is real enough to beat the naive baseline.
const DEMO_FULL_CSV_PATH = "D:/main_project/backend/data/demo_full.csv";

test.describe("Demo dataset exercises every chart type, and Predict forecasts it", () => {
  test("ingest demo_full.csv -> report shows line+scatter+bar+histogram -> Predict forecasts revenue", async ({ page }) => {
    // Ingesting ~5,500 rows (exploration + a real LLM narrative call) and
    // then training a real forecast (Prophet + ARIMA across several CV
    // folds, plus a full refit for the chart) is genuinely more work than
    // this suite's other fixtures - the default 3-minute suite timeout
    // (playwright.config.ts) is cutting it close for both steps combined.
    test.setTimeout(5 * 60 * 1000);

    await page.goto("/sources");
    await expect(page.getByRole("heading", { name: "Sources", exact: true })).toBeVisible();

    await page.locator("textarea").fill(JSON.stringify({ path: DEMO_FULL_CSV_PATH }));
    await page.getByRole("button", { name: "Register", exact: true }).click();

    const registeredLine = page.locator("text=Registered:").first();
    await expect(registeredLine).toBeVisible();
    const sourceId = (await registeredLine.locator("code").textContent())?.trim();
    expect(sourceId, "source id must be readable from the Sources page after registering").toBeTruthy();

    const sourceRow = page.locator("tr", { has: page.locator(`code:text-is("${sourceId}")`) });
    await expect(sourceRow).toBeVisible();
    await sourceRow.getByRole("button", { name: "Ingest", exact: true }).click();

    const ingestResult = sourceRow.locator("text=/Ingest finished:/");
    await expect(ingestResult).toBeVisible({ timeout: 90_000 });
    const ingestText = (await ingestResult.textContent()) ?? "";
    expect(ingestText, `expected a clean ingest to complete, got "${ingestText}"`).toContain("status=completed");
    const runNumberMatch = ingestText.match(/Run #(\d+)/);
    expect(runNumberMatch, `expected "Run #<number>" in "${ingestText}"`).toBeTruthy();
    const runNumber = runNumberMatch![1];

    // "status=completed" only means the RUN finished (app/graph/nodes.py::
    // explore_node marks it completed BEFORE narrate_node ever runs) - a
    // real LLM narrative call over ~8 findings for a dataset this size can
    // still be mid-flight past SourcesPage's own grace period. Poll for
    // report.available directly rather than assume "View report" is safe
    // to click yet (same nuance dashboard-flow.spec.ts already handles).
    const reportDeadline = Date.now() + 90_000;
    let reportReady = false;
    while (Date.now() < reportDeadline) {
      const statusResp = await page.request.get(`http://localhost:8000/ingest/${runNumber}/status`);
      const statusBody = await statusResp.json();
      if (statusBody.report?.available) {
        reportReady = true;
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
    expect(reportReady, `report for Run #${runNumber} never became available within 90s`).toBe(true);

    // --- Report: every deterministic chart type, rendered as a real
    // Plotly SVG - a chart that fails to render still shows its <Muted>
    // title text but no .js-plotly-plot svg.main-svg underneath it. ---
    const reportLink = sourceRow.getByRole("link", { name: "View report", exact: true });
    await expect(reportLink).toBeVisible();
    await reportLink.click();

    await expect(page.getByRole("heading", { name: "Data quality context" })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole("heading", { name: "Charts" })).toBeVisible();

    const renderedPlots = page.locator(".js-plotly-plot svg.main-svg");
    await expect(renderedPlots.first()).toBeVisible({ timeout: 20_000 });
    // revenue/cost histograms, satisfaction_score bar, category/region bars, scatter, line - at least 4, comfortably.
    expect(await renderedPlots.count()).toBeGreaterThanOrEqual(4);

    // .first(): Plotly duplicates the chart title inside its own SVG
    // <title> element, alongside this page's <Muted> caption - both are a
    // legitimate match, so this isn't about ambiguity, just picking either.
    await expect(page.getByText("revenue - distribution").first()).toBeVisible(); // continuous numeric -> histogram
    await expect(page.getByText("satisfaction_score - value counts").first()).toBeVisible(); // discrete 1-5 rating -> bar
    await expect(page.getByText("cost vs revenue").first()).toBeVisible(); // genuine correlation -> scatter
    await expect(page.getByText("revenue over order_date").first()).toBeVisible(); // genuine trend -> line

    // --- Predict: forecast revenue (the trending column) via a natural-
    // language question, exactly as a human would type it - no target_column
    // shortcut, no manual id entry (the source id from registration above
    // resolves to this source's latest completed run). ---
    await page.goto("/predict");
    await expect(page.getByRole("heading", { name: "Predict", exact: true })).toBeVisible();
    await page.getByPlaceholder("source_id (or leave blank and give a run_id)").fill(sourceId!);
    await page.getByPlaceholder("Forecast monthly revenue").fill("Forecast monthly revenue");
    await page.getByRole("button", { name: "Predict", exact: true }).click();

    // While training, the button shows it's busy (not silently idle) - the
    // same "never a stuck spinner" guarantee ingest/resolve already have.
    await expect(page.getByRole("button", { name: /Predicting/ })).toBeVisible();

    await expect(page.getByRole("heading", { name: "Data quality context" })).toBeVisible({ timeout: 180_000 });
    await expect(page.locator("span.rounded-full", { hasText: "forecast" })).toBeVisible(); // Task badge, exact match on the pill itself

    // The comparison IS the point - the model's own score sits directly
    // next to its baseline, not on a separate screen.
    await expect(page.getByRole("heading", { name: "Out-of-sample performance" })).toBeVisible();
    await expect(page.getByText(/baseline \(naive\):/)).toBeVisible();

    // Deterministic forecast chart: actuals + forecast + interval band.
    await expect(page.getByRole("heading", { name: "Forecast chart" })).toBeVisible();
    const forecastPlot = page.locator(".js-plotly-plot svg.main-svg").last();
    await expect(forecastPlot).toBeVisible({ timeout: 20_000 });

    // Excluded features - the leakage-prevention evidence, not a black box.
    await expect(page.getByText("order_id")).toBeVisible();
  });
});
