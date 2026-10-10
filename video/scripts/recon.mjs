// Recon only: full-page 1x shots of every route, to plan the storyboard.
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
import { BASE, assertThisApp } from "./guard.mjs";

const OUT = "capture/recon";
mkdirSync(OUT, { recursive: true });
const RUN = process.argv[2] ?? "fda0f658-6b52-4332-9bde-c48d183c6eab";
const routes = [
  ["sources", "/sources"],
  ["approvals", "/approvals"],
  ["reports", `/reports?run=${RUN}`],
  ["audit", `/audit?run=${RUN}`],
  ["compare", `/compare?run=${RUN}`],
  ["ask", `/ask?run=${RUN}`],
  ["predict", `/predict?run=${RUN}`],
  ["analytics", `/analytics?run=${RUN}`],
  ["marketing", `/marketing?run=617e22e1-27c5-48e6-92c5-6fe3ab3dfcf8`],
  ["agriculture", `/agriculture?run=946c7c4d-e2b4-4579-8fa8-aa9025b1f3a1`],
  ["export", `/export?run=${RUN}`],
];

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await assertThisApp(page);
for (const [name, path] of routes) {
  await page.goto(BASE + path);
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(3000);
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
  const h = await page.evaluate(() => document.documentElement.scrollHeight);
  console.log(name, h);
}
await browser.close();
