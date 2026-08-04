import { test, expect } from "./themed-test";

// REGRESSION COVERAGE for the "run '48' not found" report.
//
// Ask and Predict used to ask a human to TYPE a run identifier into a bare
// pair of Source ID / Run ID boxes. Two defects met there: the shared
// resolver behind both endpoints (app/query/pipeline.py::resolve_run) did a
// primary-key-only lookup, so a run NUMBER - the identifier Reports/Audit
// have accepted all along, and the only one the rest of the UI shows - was
// rejected as nonexistent; and the form asked for a source at all, even
// though a run already knows its source and the API ignores source_id
// whenever run_id is present.
//
// What actually needs pinning is therefore the REQUEST these pages build:
// selecting a run must post that run's identifier as run_id, and must not
// post a source_id. Asserting the payload directly (rather than the answer
// that comes back) keeps this test deterministic and free of live Gemini
// latency/quota - the real end-to-end Ask/Predict paths stay covered by
// dashboard-flow.spec.ts and demo-full-charts.spec.ts.
const API_ORIGIN = "http://localhost:8000";

const RUNS_FIXTURE = [
  {
    id: "picker-run-uuid-0002",
    run_number: 902,
    status: "completed",
    source_id: "picker-source-0002",
    source_label: "newest.csv",
    started_at: "2026-08-03T12:00:00",
    completed_at: "2026-08-03T12:00:30",
  },
  {
    id: "picker-run-uuid-0001",
    run_number: 901,
    status: "completed",
    source_id: "picker-source-0001",
    source_label: "orders.csv",
    started_at: "2026-08-03T11:00:00",
    completed_at: "2026-08-03T11:00:30",
  },
];

/** Serves the picker's list plus the per-run column lookup, and captures
 *  whatever body the page posts to `postPath`. */
async function stubRunApis(page: import("@playwright/test").Page, postPath: "/ask" | "/predict") {
  const posted: Record<string, unknown>[] = [];

  await page.route(`${API_ORIGIN}/runs?*`, async (route) => {
    await route.fulfill({ json: RUNS_FIXTURE });
  });

  // The picker reads the SELECTED run's columns from the existing ingest
  // status endpoint - deliberately not from the list (that would rebuild
  // the repaired frame once per row; see app/routers/runs.py).
  await page.route(`${API_ORIGIN}/ingest/*/status*`, async (route) => {
    await route.fulfill({
      json: {
        run_id: "picker-run-uuid-0002",
        run_number: 902,
        status: "completed",
        metadata: { column_types: { order_id: "str", revenue: "float64", region: "str", order_date: "datetime64[us]" } },
      },
    });
  });

  await page.route(`${API_ORIGIN}${postPath}`, async (route) => {
    posted.push(route.request().postDataJSON());
    if (postPath === "/ask") {
      await route.fulfill({
        json: {
          id: "picker-query-0001",
          status: "answered",
          question: "q",
          quality_context_summary: "- nothing notable",
          query_kind: "pandas",
          code: "result = df['revenue'].mean()",
          result: { type: "scalar", value: 1 },
          truncated: false,
          row_count: null,
          columns_referenced: [],
          assumptions: [],
          confidence: 0.9,
          escalation_reason: null,
          escalation_detail: null,
          state: "answered",
        },
      });
      return;
    }
    await route.fulfill({ json: { id: "picker-model-0001", state: "running" } });
  });

  return posted;
}

test.describe("Ask/Predict select a run instead of asking for typed ids", () => {
  test("Ask posts the selected run as run_id, with no source_id and nothing typed", async ({ page }) => {
    const posted = await stubRunApis(page, "/ask");

    await page.goto("/ask");
    await expect(page.getByRole("heading", { name: "Ask", exact: true })).toBeVisible();

    // The Source ID box is GONE - a run already knows its source, and
    // offering both is what let a source UUID and an unrelated run id be
    // submitted together.
    await expect(page.getByLabel("Source ID")).toHaveCount(0);

    // Newest completed run is pre-selected, so the page is usable without
    // any identifier being entered at all.
    const runSelect = page.getByLabel("Run", { exact: true });
    await expect(runSelect).toHaveValue("902");
    await expect(runSelect).toContainText("Run #902 - newest.csv");

    // Choosing the OTHER run is a selection, never a typed id.
    await runSelect.selectOption("901");

    // Part 3: the question field is labelled, and its examples name real
    // columns of the selected run rather than generic placeholder copy.
    const question = page.getByLabel("Question", { exact: true });
    await expect(question).toBeVisible();
    await expect(question).toHaveAttribute("placeholder", /revenue/);
    await expect(page.getByText("Columns:")).toBeVisible();
    await expect(page.getByRole("button", { name: "what is the average revenue?" })).toBeVisible();

    await question.fill("what is the average revenue?");
    await page.getByRole("button", { name: "Ask", exact: true }).click();

    await expect(page.getByRole("heading", { name: "Generated code" })).toBeVisible();
    expect(posted).toHaveLength(1);
    expect(posted[0].run_id).toBe("901");
    expect(posted[0].source_id).toBeUndefined();
  });

  test("Predict posts the selected run as run_id, with no source_id and nothing typed", async ({ page }) => {
    const posted = await stubRunApis(page, "/predict");

    await page.goto("/predict");
    await expect(page.getByRole("heading", { name: "Predict", exact: true })).toBeVisible();
    await expect(page.getByLabel("Source ID")).toHaveCount(0);

    const runSelect = page.getByLabel("Run", { exact: true });
    await expect(runSelect).toHaveValue("902");
    await runSelect.selectOption("901");

    const question = page.getByLabel("Question", { exact: true });
    await expect(question).toHaveAttribute("placeholder", /revenue/);
    await question.fill("forecast monthly revenue");
    await page.getByRole("button", { name: "Predict", exact: true }).click();

    await expect.poll(() => posted.length).toBe(1);
    expect(posted[0].run_id).toBe("901");
    expect(posted[0].source_id).toBeUndefined();
  });

  test("a just-finished run reads as recent, not shifted by the viewer's timezone", async ({ page }) => {
    // The API serialises naive UTC ("2026-08-04T07:15:00" - no Z, no
    // offset) and JS parses that bare form as LOCAL time, so a run that
    // finished seconds ago rendered as "6h ago" for a UTC+5:30 viewer.
    // Built relative to now so this stays meaningful wherever it runs.
    const naiveUtcTwoMinutesAgo = new Date(Date.now() - 2 * 60_000).toISOString().replace(/\.\d+Z$/, "");
    await page.route(`${API_ORIGIN}/runs?*`, async (route) => {
      await route.fulfill({ json: [{ ...RUNS_FIXTURE[0], completed_at: naiveUtcTwoMinutesAgo }] });
    });
    await page.route(`${API_ORIGIN}/ingest/*/status*`, async (route) => route.fulfill({ json: { metadata: { column_types: {} } } }));

    await page.goto("/ask");
    await expect(page.getByLabel("Run", { exact: true })).toContainText("completed 2m ago");
  });

  test("a run number carried in ?run= prefills the picker, so nothing is retyped", async ({ page }) => {
    await stubRunApis(page, "/ask");

    // Exactly what the new "Ask"/"Predict" links on Sources and the report
    // page navigate to (lib/runLinks.ts).
    await page.goto("/ask?run=901");
    await expect(page.getByLabel("Run", { exact: true })).toHaveValue("901");
  });

  test("an unknown run number answers with the runs that DO exist, clickably", async ({ page }) => {
    // The original failure rendered a dead red box. A 404 here means only
    // "no such run", so the actionable information is which runs exist.
    await page.route(`${API_ORIGIN}/runs?*`, async (route) => route.fulfill({ json: RUNS_FIXTURE }));
    await page.route(`${API_ORIGIN}/ingest/*/status*`, async (route) => route.fulfill({ status: 404, json: { detail: "run not found" } }));
    await page.route(`${API_ORIGIN}/ask`, async (route) => route.fulfill({ status: 404, json: { detail: "run '99999' not found" } }));

    await page.goto("/ask");
    await page.getByLabel("Run number").fill("99999");
    await page.getByLabel("Question", { exact: true }).fill("how many rows?");
    await page.getByRole("button", { name: "Ask", exact: true }).click();

    await expect(page.getByText("run '99999' not found")).toBeVisible();
    await expect(page.getByText("Recent runs:")).toBeVisible();

    // Clicking a suggested run repairs the input in place, rather than
    // leaving the user to go find a valid id themselves.
    await page.getByRole("button", { name: "#902", exact: true }).click();
    await expect(page.getByLabel("Run number")).toHaveValue("902");
  });
});
