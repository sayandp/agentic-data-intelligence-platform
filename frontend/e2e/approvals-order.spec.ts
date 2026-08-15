import { expect } from "@playwright/test";

import { test } from "./themed-test";

/**
 * Needs Attention is ordered by CONSEQUENCE, not by how many rows each
 * section has.
 *
 * An escalated validation event is a run PAUSED waiting on a person -
 * nothing downstream of it can proceed. A provisional baseline is
 * informational, normal on a first ingest, and resolvable whenever. With
 * baselines leading, ~45 informational rows sat above the one paused thing
 * and told a reviewer the wrong story about what needed them.
 *
 * Asserted by BOUNDING BOX rather than DOM order, because what matters is
 * what a reviewer's eye reaches first - and in every theme, since a theme
 * must never change structure.
 */

const SECTIONS = [
  "Escalated validation events",
  "Escalated queries",
  "Escalated models",
  "Connector warnings",
  "Provisional baselines",
];

test("Needs Attention sections are ordered by consequence, on screen", async ({ page }) => {
  await page.goto("/approvals");
  await expect(page.getByRole("heading", { name: SECTIONS[0] })).toBeVisible();

  const tops: number[] = [];
  for (const section of SECTIONS) {
    const box = await page.getByRole("heading", { name: section, exact: true }).boundingBox();
    expect(box, `no heading found for ${section}`).not.toBeNull();
    tops.push(box!.y);
  }

  for (let i = 1; i < tops.length; i += 1) {
    expect(tops[i], `${SECTIONS[i]} must appear below ${SECTIONS[i - 1]}`).toBeGreaterThan(tops[i - 1]);
  }
});

test("escalated events appear above provisional baselines", async ({ page }) => {
  await page.goto("/approvals");

  const escalated = page.getByRole("heading", { name: "Escalated validation events", exact: true });
  const baselines = page.getByRole("heading", { name: "Provisional baselines", exact: true });
  await expect(escalated).toBeVisible();
  await expect(baselines).toBeVisible();

  const [a, b] = [await escalated.boundingBox(), await baselines.boundingBox()];
  expect(a!.y).toBeLessThan(b!.y);
});

test("a long provisional-baseline list is collapsed, with its count visible", async ({ page, request }) => {
  const pending = (await (await request.get("http://127.0.0.1:8000/approvals/pending")).json()) as {
    provisional_baselines: unknown[];
  };
  const count = pending.provisional_baselines.length;
  test.skip(count <= 10, `only ${count} provisional baseline(s) - nothing to collapse`);

  await page.goto("/approvals");

  // Nothing is hidden: the count is in the summary, so a reviewer can see
  // how much is in there without scrolling past all of it.
  const summary = page.getByText(`Show ${count} provisional baselines`);
  await expect(summary).toBeVisible();

  // Collapsed by default: a <details> keeps its content in the DOM, so
  // what matters is that the rows are not on SCREEN until asked for.
  const rows = page.locator("table").filter({ hasText: "Rows" }).first();
  await expect(rows).not.toBeVisible();

  await summary.click();
  await expect(rows).toBeVisible();
});
