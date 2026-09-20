/**
 * Break each pagination guarantee; report which test notices.
 *
 * A pager is a thing that HIDES data. Every claim it makes - that the total is
 * stated, that it appears only when a list overflows, that a page number never
 * outlives the list it was chosen for - is a claim about something the reader
 * can no longer see for themselves. So each one is broken here on purpose, and
 * a guarantee nothing fails for is reported as unproven rather than assumed.
 *
 * Each case ASSERTS THE MUTATION LANDED before trusting the result. A mutation
 * that silently fails to apply reports NOT CAUGHT, which reads exactly like a
 * missing test and is the more dangerous of the two.
 *
 *   node scripts/falsify-pagination.mjs
 *
 * Requires the dev stack (../start.ps1) to be running.
 */

import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";

const SPEC = "e2e/pagination.spec.ts";
const UI = "src/components/ui.tsx";
const AGRI = "src/pages/AgriculturePage.tsx";

const CASES = [
  {
    name: "the pager stops stating the total, and shows only the range",
    file: UI,
    from: `        of <span className="font-medium text-ink">{paged.total}</span> {plural}`,
    to: `        {plural}`,
    expect: "a long list is paged, and states the count it is paging",
  },
  {
    name: "the page size is ignored and the whole list renders",
    file: UI,
    from: `  const visible = items.slice(firstIndex, firstIndex + pageSize);`,
    to: `  const visible = items;`,
    expect: "a long list is paged, and states the count it is paging",
  },
  {
    name: "the pager renders even when the list already fits",
    file: UI,
    from: `  if (paged.total <= paged.pageSize) return null;`,
    to: `  if (false) return null;`,
    expect: "a list that already fits gets no pager at all",
  },
  {
    name: "a page number outlives the list it was chosen for (filter changes, page kept)",
    file: UI,
    from: `  const requested = chosen.key === listKey ? chosen.page : 0;`,
    to: `  const requested = chosen.page;`,
    expect: "a page number does not outlive the run it was chosen for",
  },
  {
    // The chip row is a second wiring of the same component. This mutation is
    // the plausible mistake: paging the state but rendering the whole list.
    name: "the chip row renders every chip while its pager claims a page",
    file: AGRI,
    from: `                {scopePage.visible.map((scope) => {`,
    to: `                {scopes.map((scope) => {`,
    expect: "the chip row is paged by the same component, against its own page size",
  },
  {
    name: "the clamp is removed, so a page can outrun a list that shrank",
    file: UI,
    from: `  const page = Math.min(Math.max(requested, 0), pageCount - 1); // see (2)`,
    to: `  const page = requested;`,
    expect: "filtering while deep in a list never strands the reader on an empty page",
    // EXPECTED UNPROVEN. While the list-identity guard stands, every route a
    // reader can take to a too-large page also changes the list's identity,
    // so the page has already been reset before the clamp could matter. Kept
    // for making the invalid state unrepresentable, reported as unproven
    // rather than covered. A NOT CAUGHT here is the known result, not a
    // regression; the other four are.
    expectedUnproven: true,
  },
];

function runTest(title) {
  try {
    execFileSync(
      "npx",
      ["playwright", "test", SPEC, "--project=default", "--reporter=line", "-g", title],
      { stdio: "pipe", shell: true }
    );
    return "passed";
  } catch {
    return "failed";
  }
}

let caught = 0;
const results = [];

for (const c of CASES) {
  const original = readFileSync(c.file, "utf-8");
  if (!original.includes(c.from)) {
    // The anchor moved. Reporting this as a failed case rather than a passing
    // one is the whole point: a no-op mutation looks identical to a guard that
    // nothing tests.
    results.push({ name: c.name, verdict: "MUTATION DID NOT APPLY", detail: "anchor not found" });
    console.log(`[SKIP] ${c.name}\n       anchor not found in ${c.file}`);
    continue;
  }

  const mutated = original.replace(c.from, c.to);
  writeFileSync(c.file, mutated, "utf-8");

  // Prove the file on disk actually changed before believing any verdict.
  const onDisk = readFileSync(c.file, "utf-8");
  if (onDisk === original || onDisk.includes(c.from)) {
    writeFileSync(c.file, original, "utf-8");
    results.push({ name: c.name, verdict: "MUTATION DID NOT APPLY", detail: "write did not land" });
    console.log(`[SKIP] ${c.name}\n       write did not land`);
    continue;
  }

  let verdict;
  try {
    const outcome = runTest(c.expect);
    verdict = outcome === "failed" ? "CAUGHT" : "NOT CAUGHT";
    if (outcome === "failed") caught += 1;
  } finally {
    writeFileSync(c.file, original, "utf-8");
  }

  results.push({ name: c.name, verdict, detail: c.expect });
  console.log(`[${verdict}] ${c.name}\n          by: ${c.expect}`);
}

console.log(`\n${caught}/${CASES.length} guards caught.`);
const unexpected = results.filter((r) => r.verdict !== "CAUGHT" && !CASES.find((c) => c.name === r.name)?.expectedUnproven);
for (const r of results.filter((r) => r.verdict !== "CAUGHT")) {
  const known = CASES.find((c) => c.name === r.name)?.expectedUnproven ? " (expected - see comment)" : "";
  console.log(`  UNPROVEN${known}: ${r.name}`);
}
process.exitCode = unexpected.length > 0 ? 1 : 0;
