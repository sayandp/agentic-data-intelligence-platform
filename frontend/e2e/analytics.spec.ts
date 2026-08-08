import { test, expect } from "./themed-test";

// The Business Analytics page. The property that matters most is the one the
// capability layer exists for: an analysis that could not run is shown WITH
// the requirement it was missing, so an empty-looking page never leaves a
// reader guessing whether an analysis found nothing or was never eligible.
//
// Stubbed rather than driven through a live ingest: this asserts what the
// PAGE does with a response, and the response itself is covered by the
// backend's own tests against real data. That keeps this spec free of
// Gemini latency, which is what makes the other real-ingest specs slow.
const API_ORIGIN = "http://localhost:8000";

const ANALYTICS_FIXTURE = {
  run_id: "analytics-run-uuid",
  run_number: 901,
  schema_version: 1,
  generated_at: "2026-08-04T10:00:00",
  detected_roles: {
    row_count: 500,
    minimum_usable_confidence: "medium",
    assigned: {
      entity_id: { column: "customer_id", role: "entity_id", score: 0.8, confidence: "high", reasons: [] },
      monetary: { column: "revenue", role: "monetary", score: 0.85, confidence: "high", reasons: [] },
    },
    unconfirmed_candidates: [],
  },
  applicability: [
    { analysis: "abc_pareto", applicable: true, resolved_columns: { monetary: "revenue" }, missing_requirements: [], near_misses: [] },
    {
      analysis: "market_basket",
      applicable: false,
      resolved_columns: {},
      missing_requirements: ["needs a line-item identifier that groups within a transaction; none detected"],
      near_misses: [
        { role: "item_id", column: "customer_id", confidence: "low", score: 0.0, why_rejected: "exactly 1.00 distinct value(s) per order_id" },
      ],
    },
  ],
  results: [
    {
      analysis: "abc_pareto",
      ran: true,
      not_run_reason: null,
      parameters: { band_cutoffs: [0.8, 0.95], value_column: "revenue", grouped_by: "customer_id" },
      findings: [
        {
          id: "abc_pareto-concentration_band-0",
          analysis: "abc_pareto",
          finding_type: "concentration_band",
          columns: ["customer_id", "revenue"],
          payload: {
            finding_type: "concentration_band",
            band: "A",
            entity_count: 120,
            entity_share: 0.24,
            value_total: 8000,
            value_share: 0.8,
            cumulative_value_share: 0.8,
            top_entities: ["c1", "c2", "c3"],
          },
          evidence: { sample_size: 500, entity_count: 500, total_value: 10000, parameters: {} },
        },
      ],
    },
    {
      analysis: "market_basket",
      ran: false,
      not_run_reason: "needs a line-item identifier that groups within a transaction; none detected",
      parameters: {},
      findings: [],
    },
  ],
};

async function stub(page: import("@playwright/test").Page) {
  await page.route(`${API_ORIGIN}/runs?*`, async (route) =>
    route.fulfill({
      json: [
        {
          id: "analytics-run-uuid",
          run_number: 901,
          status: "completed",
          source_id: "s1",
          source_label: "orders.csv",
          started_at: "2026-08-04T09:00:00",
          completed_at: "2026-08-04T09:01:00",
        },
      ],
    })
  );
  await page.route(`${API_ORIGIN}/ingest/*/status*`, async (route) =>
    route.fulfill({ json: { metadata: { column_types: { customer_id: "int64", revenue: "float64" } } } })
  );
  await page.route(`${API_ORIGIN}/analytics/*`, async (route) => route.fulfill({ json: ANALYTICS_FIXTURE }));
}

test.describe("Business analytics page", () => {
  test("shows results AND the not-applicable list with its reason", async ({ page }) => {
    await stub(page);
    await page.goto("/analytics?run=901");

    await expect(page.getByRole("heading", { name: "Analytics", exact: true })).toBeVisible();

    // The refusal is present, counted, and carries its precise requirement -
    // the whole point of the capability layer.
    await expect(page.getByRole("heading", { name: /Not applicable to this data \(1 of 2\)/ })).toBeVisible();
    await expect(page.getByText("needs a line-item identifier that groups within a transaction; none detected")).toBeVisible();
    // ...and the closest candidate, so a human can confirm it if it was right.
    await expect(page.getByText(/Closest candidate/)).toBeVisible();

    // The analysis that DID run renders its numbers as text, not only as a
    // chart - nothing depends on Plotly having loaded.
    await expect(page.getByRole("heading", { name: "ABC / Pareto concentration" })).toBeVisible();
    await expect(page.getByText("Band A")).toBeVisible();
    await expect(page.getByText("80.0%")).toBeVisible();
  });

  test("exposes the parameters every analysis used", async ({ page }) => {
    await stub(page);
    await page.goto("/analytics?run=901");

    // A result whose thresholds are invisible is not reproducible.
    await page.getByText("Parameters used").first().click();
    await expect(page.getByText(/band_cutoffs/)).toBeVisible();
  });

  test("names the column filling each detected role", async ({ page }) => {
    await stub(page);
    await page.goto("/analytics?run=901");

    // Scoped to the roles line: `customer_id` legitimately appears three
    // times on this page (the roles line, the picker's column hints, and
    // the near-miss explanation), so a bare text match is ambiguous rather
    // than wrong.
    const rolesLine = page.locator("div").filter({ hasText: /^Run #901.*Roles detected:/ }).last();
    await expect(rolesLine).toContainText("entity_id");
    await expect(rolesLine).toContainText("customer_id");
    await expect(rolesLine).toContainText("revenue");
  });
});
