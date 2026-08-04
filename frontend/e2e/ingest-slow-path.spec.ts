import { test, expect } from "@playwright/test";

// Covers the property STEP 3 of the ingest-hang investigation required:
// "an ingest that escalates with the LLM unavailable/exhausted must still
// end with the UI showing a resolvable state, not a spinner." Simulating a
// genuinely exhausted Gemini key live would mean either burning real quota
// (nondeterministic - depends on how much quota happens to be left) or
// temporarily breaking GEMINI_API_KEYS on the shared dev backend and
// restarting it, which risks leaving a real, currently-in-use stack
// mid-broken if anything goes wrong. Network-level interception gets the
// same guarantee under test deterministically and in isolation: this
// mocks GET /ingest/{run_id}/status to stay "running" for several polls
// before finally resolving, exercising this test's OWN pollIngestStatus
// (frontend/src/api/client.ts) and SourcesPage.tsx's progress-message
// wiring exactly as a slow/backoff-heavy LLM path would, without needing
// the real backend's LLM path to actually be slow.
test.describe("Ingest survives a slow backend (simulated LLM backoff)", () => {
  test("UI shows progress throughout and reaches a resolvable state, never a stuck spinner", async ({ page }) => {
    const FAKE_RUN_ID = "fake-run-slow-path-0001";
    const FAKE_SOURCE_ID = "fake-source-slow-path-0001";
    let statusPollCount = 0;
    const SLOW_POLLS_BEFORE_DONE = 4; // ~4 seconds of simulated "still running" at the app's 1s poll interval

    // Scoped to the BACKEND origin specifically (localhost:8000) - a bare
    // "**/sources" glob also matches the frontend's OWN client-side route
    // navigation (localhost:5173/sources, served by Vite), intercepting
    // the page load itself instead of just the API call underneath it.
    const API_ORIGIN = "http://localhost:8000";

    await page.route(`${API_ORIGIN}/sources`, async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({ json: [{ id: FAKE_SOURCE_ID, type: "file", connection_config: { path: "fake.csv" }, created_at: new Date().toISOString() }] });
        return;
      }
      await route.continue();
    });

    await page.route(`${API_ORIGIN}/ingest/${FAKE_SOURCE_ID}`, async (route) => {
      // Matches the fixed contract: POST /ingest returns {run_id, status:
      // "running"} immediately - never held open, regardless of how slow
      // the graph behind it is.
      await route.fulfill({ json: { run_id: FAKE_RUN_ID, status: "running" } });
    });

    await page.route(`${API_ORIGIN}/ingest/${FAKE_RUN_ID}/status`, async (route) => {
      statusPollCount += 1;
      if (statusPollCount <= SLOW_POLLS_BEFORE_DONE) {
        await route.fulfill({ json: { run_id: FAKE_RUN_ID, status: "running" } });
        return;
      }
      await route.fulfill({
        json: {
          run_id: FAKE_RUN_ID,
          status: "awaiting_approval",
          metadata: { row_count: 1, column_types: {}, source_type: "file" },
          validation_failure_count: 1,
          baseline: { id: "fake-baseline", is_active: true, is_provisional: false },
          baseline_rejected_reasons: null,
          findings: { available: false, run_id: FAKE_RUN_ID, url: null },
          report: { available: false, run_id: FAKE_RUN_ID, url: null, generation_mode: null },
        },
      });
    });

    await page.goto("/sources");
    await expect(page.getByRole("heading", { name: "Sources", exact: true })).toBeVisible();

    // The id renders via CopyableId as the full UUID text (components/ui.tsx).
    const sourceRow = page.locator("tr", { has: page.locator(`code:text-is("${FAKE_SOURCE_ID}")`) });
    await expect(sourceRow).toBeVisible();
    await sourceRow.getByRole("button", { name: "Ingest", exact: true }).click();

    // While "running", the button must show it's busy (not silently
    // idle - the pre-fix bug's failure mode was indistinguishable from
    // this looking fine while actually being stuck).
    await expect(sourceRow.getByRole("button", { name: /Ingesting/ })).toBeVisible();

    // The elapsed-time progress message must actually update at least
    // once while still running - proof this is polling and reporting
    // real progress, not just showing one static "please wait" string
    // indistinguishable from a hang.
    await expect(sourceRow.locator("text=/\\d+s elapsed/")).toBeVisible({ timeout: 10_000 });

    // And it must NOT throw / show an error during the slow-but-fine
    // period - a slow LLM call is "working, waiting", not a failure.
    await expect(sourceRow.locator("text=/Ingest failed/")).not.toBeVisible();

    // Eventually - once the mocked backend finally reports a terminal
    // status - the UI reaches a real, resolvable state.
    await expect(sourceRow.locator("text=/Ingest finished:.*status=awaiting_approval/")).toBeVisible({ timeout: 15_000 });
    await expect(sourceRow.getByRole("button", { name: "Ingest", exact: true })).toBeEnabled();
  });
});

// Same property, same technique, for the SECOND place a graph resume can
// hold an HTTP request open: POST /approvals/{id}/resolve for a
// validation_event (app/routers/approvals.py::_resolve_validation_event -
// see its RESOLVE-HANG FIX docstring). Mocked deterministically for the
// same reason as above: a real exhausted-quota resolve is nondeterministic
// and this session's cumulative Gemini usage today makes it unreliable to
// depend on for a repeatable regression test.
test.describe("Resolving an escalation survives a slow backend (simulated LLM backoff)", () => {
  test("Approvals UI shows progress throughout and clears the item, never a stuck button", async ({ page }) => {
    const FAKE_RUN_ID = "fake-run-resolve-slow-0001";
    const FAKE_RESOLVE_ID = "fake-resolve-slow-0001";
    let statusPollCount = 0;
    const SLOW_POLLS_BEFORE_DONE = 4;

    const API_ORIGIN = "http://localhost:8000";
    const pendingGroup = {
      resolve_id: FAKE_RESOLVE_ID,
      correlation_group_id: null,
      run_id: FAKE_RUN_ID,
      run_number: 1, // dashboard UX pass, Part 1 - EventGroup now always carries this
      rules_failed: ["null_threshold:price"],
      columns: ["price"],
      diagnosis: { likely_cause: "simulated for the slow-path test", suggested_fix: { action: "escalate" } },
      risk_level: "high",
      gate_reasons: ["action 'escalate' not permitted for this rule - no fixes are permitted for this rule by the applicability matrix"],
      action_taken: null,
      created_at: new Date().toISOString(),
    };
    // Once resolved, the item must actually disappear from a re-fetched
    // pending list - a mock that always returns the same group would make
    // this test pass even if ApprovalsPage never removed anything.
    let resolved = false;

    // Dashboard UX pass, Part 2: ApprovalsPage unconditionally reads
    // recently_resolved/summary off every /approvals/pending response now -
    // a mock missing them would throw client-side, not just render
    // incompletely.
    await page.route(`${API_ORIGIN}/approvals/pending`, async (route) => {
      await route.fulfill({
        json: {
          provisional_baselines: [],
          validation_events: resolved ? [] : [pendingGroup],
          escalated_queries: [],
          escalated_models: [],
          connector_warnings: [],
          recently_resolved: { provisional_baselines: [], validation_events: [], connector_warnings: [], queries: [], models: [] },
          summary: { total_resolved: 0, distinct_runs: 0 },
        },
      });
    });

    await page.route(`${API_ORIGIN}/approvals/${FAKE_RESOLVE_ID}/resolve`, async (route) => {
      // Matches the fixed contract: returns {status: "resolving", run_id}
      // immediately - never held open, regardless of how slow the
      // resumed graph's narrate_node call is.
      await route.fulfill({
        json: { id: FAKE_RESOLVE_ID, type: "validation_event", decision: "reject_fix", status: "resolving", run_id: FAKE_RUN_ID },
      });
    });

    await page.route(`${API_ORIGIN}/ingest/${FAKE_RUN_ID}/status*`, async (route) => {
      statusPollCount += 1;
      if (statusPollCount <= SLOW_POLLS_BEFORE_DONE) {
        await route.fulfill({ json: { run_id: FAKE_RUN_ID, status: "running" } });
        return;
      }
      resolved = true;
      await route.fulfill({
        json: {
          run_id: FAKE_RUN_ID,
          status: "completed",
          resolution: { id: FAKE_RESOLVE_ID, type: "validation_event", decision: "reject_fix" },
        },
      });
    });

    await page.goto("/approvals");
    await expect(page.getByRole("heading", { name: "Approvals", exact: true })).toBeVisible();

    // Runs render via RunLabel now ("Run #N", not a raw id) - the full id
    // still lives on the label span's title attribute (components/ui.tsx).
    // Dashboard redesign (Instrument Panel): the outer Card wrapper and this
    // inner event-group div now share the same rounded-md radius token (the
    // prior design's rounded-xl only happened to disambiguate them by
    // accident) - .p-4 is what's actually unique to the inner group (Card
    // itself uses p-5).
    const eventGroup = page.locator("div.rounded-md.p-4", { has: page.locator(`span[title="${FAKE_RUN_ID}"]`) });
    await expect(eventGroup).toBeVisible();
    await eventGroup.getByRole("button", { name: "Keep data as-is", exact: true }).click();

    // While resolving, the button shows it's busy - not silently idle.
    await expect(eventGroup.getByRole("button", { name: /Rejecting fix/ })).toBeVisible();

    // The elapsed-time progress message must actually update at least
    // once while still resolving - proof this is polling and reporting
    // real progress, not indistinguishable from a hang.
    await expect(eventGroup.locator("text=/\\d+s elapsed/")).toBeVisible({ timeout: 10_000 });

    // Eventually - once the mocked backend finally reports the resolution
    // - the item clears and the page reaches a real, resolvable state,
    // never a permanently stuck button.
    await expect(eventGroup).not.toBeVisible({ timeout: 15_000 });
  });
});
