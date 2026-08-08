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
      monetary: {
        column: "revenue",
        role: "monetary",
        score: 0.85,
        confidence: "high",
        reasons: [],
        details: { negative_count: 5, negative_fraction: 5e-6, non_null_count: 1067371, max_negative_fraction: 0.05 },
      },
    },
    unconfirmed_candidates: [],
  },
  value_definition: {
    monetary_column: "Price",
    quantity_column: "Quantity",
    derived: true,
    label: "Price x Quantity",
    note:
      "Value computed as `Price` x `Quantity`. `Price` reads as a unit price, so summing it alone would rank by how expensive one unit is rather than by how much value changed hands.",
  },
  quantity_confirmation: null,
  applicability: [
    { analysis: "abc_pareto", applicable: true, resolved_columns: { monetary: "revenue" }, missing_requirements: [], near_misses: [] },
    {
      analysis: "market_basket",
      applicable: false,
      resolved_columns: {},
      missing_requirements: ["needs a line-item identifier that groups within a transaction; none detected"],
      missing_roles: [{ role: "item_id", description: "a line-item identifier that groups within a transaction" }],
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
        {
          id: "abc_pareto-concentration_curve-1",
          analysis: "abc_pareto",
          finding_type: "concentration_curve",
          columns: ["customer_id", "revenue"],
          payload: {
            finding_type: "concentration_curve",
            entity_rank: [1, 2, 3],
            cumulative_entity_share: [0.2, 0.6, 1.0],
            cumulative_value_share: [0.374, 0.7, 1.0],
            top_20_percent_value_share: 0.374,
            top_20_percent_entity_count: 100,
            top_20_percent_entity_share: 0.2,
            concentration_floor: 0.5,
            concentration_is_weak: true,
            concentration_note:
              "The top 20% of customer_id hold 37.4% of total value. That is below the 50% floor this check uses, so concentration is weak for this data and the A/B/C bands separate it less sharply than the method's name implies.",
          },
          evidence: { sample_size: 500, entity_count: 500, total_value: 10000, parameters: {} },
        },
        {
          id: "abc_pareto-non_contributing_entities-2",
          analysis: "abc_pareto",
          finding_type: "non_contributing_entities",
          columns: ["customer_id", "revenue"],
          payload: {
            finding_type: "non_contributing_entities",
            entity_count: 284,
            zero_net_count: 283,
            negative_net_count: 1,
            net_value_total: -147614.08,
            combined_value_total: 4962621.63,
            examples: ["Adjust bad debt", "17129c", "20713"],
            note:
              "284 customer_id value(s) net to zero or below over this period - 283 netting to exactly zero and 1 netting below zero (-147,614.08 combined). They are held out of the A/B/C bands and the concentration curve, which describe how positive value is distributed. Ranking them alongside small contributors would present a full return as a small purchase.",
          },
          evidence: { sample_size: 500, entity_count: 284, total_value: -147614.08, parameters: {} },
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

test.describe("Pareto concentration flag", () => {
  test("states the actual figure and flags that the premise fails", async ({ page }) => {
    await stub(page);
    await page.goto("/analytics?run=901");

    // The flag is ABOVE the bands, not a footnote under them - a reader who
    // takes A/B/C at face value on flat data has already been misled by the
    // time a note below the table would reach them.
    const card = page.locator("section, div").filter({ hasText: "ABC / Pareto concentration" }).last();
    await expect(card.getByText("Concentration is weak for this data", { exact: true })).toBeVisible();
    await expect(card.getByText(/top 20% of customer_id hold 37\.4% of total value/)).toBeVisible();

    const flagBox = await card.getByText("Concentration is weak for this data", { exact: true }).boundingBox();
    const bandRow = await card.getByText("Band A").boundingBox();
    expect(flagBox!.y).toBeLessThan(bandRow!.y);
  });

  test("reports the figure without a warning when concentration holds", async ({ page }) => {
    await stub(page);
    await page.route(`${API_ORIGIN}/analytics/*`, async (route) => {
      const strong = JSON.parse(JSON.stringify(ANALYTICS_FIXTURE));
      const curve = strong.results[0].findings[1].payload;
      curve.top_20_percent_value_share = 0.81;
      curve.concentration_is_weak = false;
      curve.concentration_note =
        "The top 20% of customer_id hold 81.0% of total value. Concentration clears the 50% floor this check uses.";
      await route.fulfill({ json: strong });
    });
    await page.goto("/analytics?run=901");

    await expect(page.getByText(/hold 81\.0% of total value/)).toBeVisible();
    await expect(page.getByText("Concentration is weak for this data", { exact: true })).toHaveCount(0);
  });
});

test.describe("Confirming a column role", () => {
  test("offers a picker per missing role and never pre-selects one", async ({ page }) => {
    await stub(page);
    await page.goto("/analytics?run=901");

    const select = page.getByLabel(/item_id/);
    await expect(select).toBeVisible();
    // An unconfirmed candidate stays unused: a default selection would be
    // the machine guessing while looking like a human decision.
    await expect(select).toHaveValue("");
    await expect(page.getByRole("button", { name: "Confirm" })).toBeDisabled();
    await expect(page.getByText(/Would let Market basket run/)).toBeVisible();
  });

  test("confirming re-runs the analyses that needed the role", async ({ page }) => {
    await stub(page);

    // The POST returns the same document a GET would, with the analysis now
    // applicable - so the page re-renders from the response it already reads.
    let posted: Record<string, unknown> | null = null;
    await page.route(`${API_ORIGIN}/analytics/*/confirmed-roles`, async (route) => {
      posted = route.request().postDataJSON();
      const after = JSON.parse(JSON.stringify(ANALYTICS_FIXTURE));
      after.detected_roles.confirmed_roles = { item_id: "product" };
      after.detected_roles.assigned.item_id = {
        column: "product",
        role: "item_id",
        score: 1.0,
        confidence: "confirmed",
        reasons: ["confirmed by a human"],
      };
      after.applicability[1].applicable = true;
      after.applicability[1].missing_requirements = [];
      after.applicability[1].missing_roles = [];
      after.results[1] = { analysis: "market_basket", ran: true, not_run_reason: null, parameters: {}, findings: [] };
      await route.fulfill({ json: after });
    });

    await page.goto("/analytics?run=901");
    await page.getByLabel(/item_id/).selectOption("customer_id");
    await page.getByRole("button", { name: "Confirm" }).click();

    await expect(page.getByRole("heading", { name: /Not applicable to this data/ })).toHaveCount(0);
    expect(posted).toEqual({ role: "item_id", column: "customer_id" });
    // The confirmation is shown as a human decision, with a way back.
    await expect(page.getByRole("button", { name: "Clear" })).toBeVisible();
  });
});

test.describe("Returns in a monetary column", () => {
  test("says the monetary role was accepted WITH negatives present", async ({ page }) => {
    await stub(page);
    await page.goto("/analytics?run=901");

    // Next to the role, not buried in a details pane: this is the one fact
    // that changes how every summed figure on the page should be read.
    const rolesLine = page.locator("div").filter({ hasText: /^Run #901.*Roles detected:/ }).last();
    await expect(rolesLine).toContainText("5 negative");
    await expect(rolesLine).toContainText("returns net off");
  });

  test("reports entities held out of the bands instead of hiding them", async ({ page }) => {
    await stub(page);
    await page.goto("/analytics?run=901");

    const card = page.locator("section, div").filter({ hasText: "ABC / Pareto concentration" }).last();
    await expect(card.getByText("284 held out of the bands (net zero or below)", { exact: true })).toBeVisible();
    await expect(card.getByText(/283 netting to exactly zero and 1 netting below zero/)).toBeVisible();
    // Named, so a reader can go and look at them.
    await expect(card.getByText(/Adjust bad debt/)).toBeVisible();
  });
});

test.describe("Which quantity the numbers describe", () => {
  test("states the value definition above every figure that depends on it", async ({ page }) => {
    await stub(page);
    await page.goto("/analytics?run=901");

    await expect(page.getByText("Value computed as", { exact: true })).toBeVisible();
    await expect(page.getByText("Price x Quantity", { exact: true })).toBeVisible();
    await expect(page.getByText(/summing it alone would rank by how expensive one unit is/)).toBeVisible();

    // Above the results, not below them: a reader who takes a revenue total
    // at face value when it is really a unit-price total has been misled by
    // the time a footnote reaches them.
    const note = await page.getByText("Value computed as", { exact: true }).boundingBox();
    const firstResult = await page.getByRole("heading", { name: "ABC / Pareto concentration" }).boundingBox();
    expect(note!.y).toBeLessThan(firstResult!.y);
  });

  test("a column used as-is says so rather than staying silent", async ({ page }) => {
    await stub(page);
    await page.route(`${API_ORIGIN}/analytics/*`, async (route) => {
      const direct = JSON.parse(JSON.stringify(ANALYTICS_FIXTURE));
      direct.value_definition = {
        monetary_column: "Amount",
        quantity_column: null,
        derived: false,
        label: "Amount",
        note: "Value taken directly from `Amount` - `Amount` reads as a line total already, so it is NOT multiplied by `Quantity`.",
      };
      await route.fulfill({ json: direct });
    });
    await page.goto("/analytics?run=901");

    await expect(page.getByText("Value taken directly from", { exact: true })).toBeVisible();
    await expect(page.getByText(/is NOT multiplied by/)).toBeVisible();
  });

  test("an undetected quantity is asked about, not guessed", async ({ page }) => {
    await stub(page);
    await page.route(`${API_ORIGIN}/analytics/*`, async (route) => {
      const gap = JSON.parse(JSON.stringify(ANALYTICS_FIXTURE));
      gap.value_definition = {
        monetary_column: "Price",
        quantity_column: null,
        derived: false,
        label: "Price",
        note: "Value taken directly from `Price` - no quantity column was detected, so there is nothing to multiply by.",
      };
      gap.quantity_confirmation = {
        role: "quantity",
        reason:
          "`Price` reads as a unit price, so these analyses would normally rank by `Price` x quantity. No quantity column was detected, so value is being summed from `Price` alone.",
        candidates: [],
      };
      await route.fulfill({ json: gap });
    });
    await page.goto("/analytics?run=901");

    const select = page.getByLabel(/quantity/);
    await expect(select).toBeVisible();
    await expect(select).toHaveValue("");
    await expect(page.getByText(/Would change how value is summed/)).toBeVisible();
  });
});
