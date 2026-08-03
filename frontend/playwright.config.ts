import { defineConfig } from "@playwright/test";

// This drives the REAL dashboard (the Vite app start.ps1 serves) against the
// REAL, already-running local backend - not a mocked API, not the Streamlit
// "Platform test console". Both servers must already be up (run .\start.ps1
// first); this deliberately does NOT start them itself, since the whole
// point is proving the actual demo stack works, not a test-only variant of it.
export default defineConfig({
  testDir: "./e2e",
  // Both the ingest and approvals-resolve paths poll a background graph
  // resume that can involve a real, possibly-retrying LLM chain - 3
  // minutes gives real headroom above their own per-step timeouts (60s
  // each) without the multi-minute allowance the pre-fix resolve endpoint
  // used to need.
  timeout: 3 * 60 * 1000,
  expect: { timeout: 15_000 },
  fullyParallel: false, // the suite is one linear human flow against shared backend state, not independent cases
  workers: 1, // multiple spec files hitting the SAME shared dev backend concurrently is resource contention, not parallelism
  retries: 0, // a flaky pass here is a stale-state bug, never a signal to hide by retrying
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],
  use: {
    baseURL: "http://localhost:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
});
