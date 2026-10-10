// v2 re-captures - only what v1's captures can't show:
//   ask-type-NN.png   the Question box, one frame per typed character (the
//                     typewriter is the dashboard's own render, not overlaid text)
//   audit-resolved    run #648's Audit page AFTER the on-camera decision, so
//                     the status flip cuts between two real states
//   node scripts/capture_v2.mjs
import { chromium } from "playwright";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { BASE, assertThisApp } from "./guard.mjs";

const OUT = "capture/public/shots";
const LAYOUT = "capture/public/layout.json";
mkdirSync(OUT, { recursive: true });
const layout = JSON.parse(readFileSync(LAYOUT, "utf8"));
const state = JSON.parse(readFileSync("capture/state.json", "utf8"));

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
const page = await ctx.newPage();
await assertThisApp(page);
await page.selectOption("#theme-select", "default");

// Typing, one keystroke at a time, clipped to the Ask form card.
await page.goto(`${BASE}/ask?run=${state.sql.run_id}`);
await page.getByRole("heading", { name: "Ask", exact: true }).waitFor();
await page.waitForTimeout(1500);
const box = page.locator("textarea").first();
await box.click();
const q = "what is the total quantity by category?";
const clip = { x: 288, y: 220, width: 1120, height: 190 };
await page.screenshot({ path: `${OUT}/ask-type-00.png`, clip });
for (let i = 1; i <= q.length; i++) {
  await page.keyboard.type(q[i - 1]);
  await page.waitForTimeout(40);
  await page.screenshot({ path: `${OUT}/ask-type-${String(i).padStart(2, "0")}.png`, clip });
}
layout["ask-type"] = { w: clip.width, h: clip.height, boxes: {}, frames: q.length + 1 };
console.log(`  ask-type 0..${q.length}`);

// Run #648 after "Keep data as-is" was clicked on camera.
await page.goto(`${BASE}/audit?run=${state.dollar.run_id}`);
await page.getByRole("heading", { name: /Timeline/ }).waitFor({ timeout: 60_000 });
await page.waitForLoadState("networkidle").catch(() => {});
await page.waitForTimeout(2000);
await page.screenshot({ path: `${OUT}/audit-resolved.png`, fullPage: true });
const size = await page.evaluate(() => ({ w: document.documentElement.scrollWidth, h: document.documentElement.scrollHeight }));
layout["audit-resolved"] = { w: size.w, h: size.h, boxes: {} };
console.log(`  audit-resolved ${size.w}x${size.h}`);

writeFileSync(LAYOUT, JSON.stringify(layout, null, 2));
await browser.close();
