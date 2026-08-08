// WCAG AA contrast audit against the REAL rendered page, per theme.
//
// Two independent methods, because neither alone is trustworthy here:
//
//   1. COMPOSITE - walks each text node's ancestor chain and alpha-composites
//      every background it sits on. Exact for opaque and translucent fills.
//
//   2. PIXEL - screenshots the page and samples actual rendered pixels in a
//      ring just outside each text node's box. This is the only method that
//      sees a gradient ground or a `backdrop-filter` blur, which method 1
//      cannot compute. Frosted panels are judged on this number.
//
// The reported ratio is the WORSE of the two, so a frosted surface can never
// pass on its computed value while failing on screen. Body text and every
// status/decision label must clear AA (4.5:1 normal, 3:1 for >=18.66px bold
// or >=24px).
//
// Usage: node scripts/contrast-audit.mjs [--theme default,dark,aurora]

import { chromium } from "playwright";
import { PNG } from "pngjs";

const BASE = "http://localhost:5173";
const VIEWPORT_WIDTH = 1400;
//: Tall enough for every page in this app; a page beyond it reports its
//: unmeasured nodes rather than quietly sampling pixels outside the capture.
const MAX_CAPTURE_HEIGHT = 12000;
const THEMES = (process.argv.includes("--theme")
  ? process.argv[process.argv.indexOf("--theme") + 1]
  : "default,dark,aurora"
).split(",");

const PAGES = [
  { path: "/sources", name: "Sources" },
  { path: "/approvals", name: "Approvals" },
  { path: "/reports?run=LATEST", name: "Reports" },
  { path: "/audit?run=LATEST", name: "Audit" },
  { path: "/ask", name: "Ask" },
  { path: "/predict", name: "Predict" },
  // Needs ?run= to render anything at all: the page is a run picker until a
  // run is chosen, so auditing it bare would measure an empty shell and
  // report a clean pass over text that was never on screen.
  { path: "/analytics?run=LATEST", name: "Analytics" },
];

// --only <substring> restricts the run to matching pages, for checking one
// screen against a specific run without waiting on the others.
//: A PASS is only meaningful over the page a user actually sees. Two things
//: previously let a measurement over a loading shell report success: a fixed
//: wait that finished before the data did (403 nodes in one theme, 77 in
//: another, both "PASS"), and the fact that nothing compared the themes to
//: each other. Both are now assertions, not observations.
//:
//: The floor is per PAGE, not per theme total, so a single page failing to
//: load cannot hide behind six that did.
const MIN_NODES_PER_PAGE = 12;
const ONLY = process.argv.includes("--only") ? process.argv[process.argv.indexOf("--only") + 1].toLowerCase() : null;
const ACTIVE_PAGES = ONLY ? PAGES.filter((p) => p.name.toLowerCase().includes(ONLY)) : PAGES;

function srgbToLinear(c) {
  const s = c / 255;
  return s <= 0.04045 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
}
function luminance([r, g, b]) {
  return 0.2126 * srgbToLinear(r) + 0.7152 * srgbToLinear(g) + 0.0722 * srgbToLinear(b);
}
function contrast(fg, bg) {
  const l1 = luminance(fg);
  const l2 = luminance(bg);
  const [hi, lo] = l1 > l2 ? [l1, l2] : [l2, l1];
  return (hi + 0.05) / (lo + 0.05);
}
function required(fontSizePx, fontWeight) {
  const large = fontSizePx >= 24 || (fontSizePx >= 18.66 && Number(fontWeight) >= 700);
  return large ? 3.0 : 4.5;
}

/** Collects every visible text node with its color, its alpha-composited
 *  background, and its box - all computed in the page. */
const COLLECT = () => {
  const parse = (c) => {
    const m = c.match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(",").map((x) => parseFloat(x.trim()));
    return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
  };
  const over = (fg, bg) => {
    const a = fg[3];
    return [fg[0] * a + bg[0] * (1 - a), fg[1] * a + bg[1] * (1 - a), fg[2] * a + bg[2] * (1 - a), 1];
  };

  const out = [];
  const els = document.querySelectorAll("body *");
  for (const el of els) {
    // Only elements that render their OWN text.
    const ownText = Array.from(el.childNodes)
      .filter((n) => n.nodeType === 3)
      .map((n) => n.textContent.trim())
      .join(" ")
      .trim();
    if (!ownText) continue;

    const cs = getComputedStyle(el);
    if (cs.visibility === "hidden" || cs.display === "none" || parseFloat(cs.opacity) === 0) continue;

    // Laid out but NOT PAINTED. Content inside a collapsed <details> keeps a
    // real bounding rect while nothing of it is on screen, so the pixel
    // sampler read whatever else occupied those coordinates - which is how
    // "Baseline for source" (inside the closed "Recently resolved"
    // disclosure) reported 3.36:1 against a composite of 17.33:1. Neither
    // number described anything a user could see.
    //
    // A closed <details> still paints its <summary>, so that stays measured.
    const closedDisclosure = el.closest("details:not([open])");
    if (closedDisclosure && !el.closest("summary")) continue;
    if (typeof el.checkVisibility === "function" &&
        !el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true, contentVisibilityAuto: true })) continue;

    const rect = el.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) continue;

    // WCAG 1.4.3 exempts INACTIVE user-interface components. A disabled
    // button is dimmed on purpose to say "not available", and forcing it to
    // AA would defeat the affordance. Counted and reported separately rather
    // than silently dropped.
    if (el.closest("button:disabled, [aria-disabled='true'], fieldset:disabled")) {
      out.push({ exempt: "disabled-control", text: ownText.slice(0, 60) });
      continue;
    }

    let fg = parse(cs.color);
    if (!fg) continue;

    // Composite the ancestor background stack.
    let bg = [255, 255, 255, 1];
    const stack = [];
    for (let node = el; node && node !== document.documentElement; node = node.parentElement) {
      stack.push(node);
    }
    stack.push(document.documentElement);
    // Whether the composite is EXACT, or only an approximation that a
    // screenshot must arbitrate. Alpha-composited maths is exact for a stack
    // of opaque/translucent flat fills; it cannot express a gradient or a
    // backdrop blur. Only the latter case needs the pixel method, and using
    // it everywhere would let antialiasing/clipping noise create phantom
    // failures on perfectly opaque surfaces.
    let needsPixel = false;
    let acc = null;
    for (const node of stack.reverse()) {
      const ns = getComputedStyle(node);
      if (ns.backgroundImage && ns.backgroundImage !== "none") needsPixel = true;
      const bf = ns.backdropFilter || ns.webkitBackdropFilter;
      if (bf && bf !== "none") needsPixel = true;
      const c = parse(ns.backgroundColor);
      if (!c || c[3] === 0) continue;
      if (c[3] < 1) needsPixel = true;
      acc = acc === null ? c : over(c, acc);
    }
    if (acc) bg = acc[3] < 1 ? over(acc, [255, 255, 255, 1]) : acc;
    if (fg[3] < 1) fg = over(fg, bg);

    out.push({
      text: ownText.slice(0, 60),
      tag: el.tagName.toLowerCase(),
      cls: (el.className && String(el.className).slice(0, 70)) || "",
      fg: [fg[0], fg[1], fg[2]],
      bgComposite: [bg[0], bg[1], bg[2]],
      fontSize: parseFloat(cs.fontSize),
      fontWeight: cs.fontWeight,
      needsPixel,
      // DOCUMENT coordinates. The viewport is grown to the whole page
      // before this runs, so scroll offsets are zero and these index
      // directly into the capture. Pairing a viewport-relative rect with a
      // shorter capture sampled a clipped sliver for anything straddling
      // the fold, which invented a 3.35:1 failure on text measuring 17:1.
      rect: { x: rect.x + window.scrollX, y: rect.y + window.scrollY, w: rect.width, h: rect.height },
    });
  }
  return out;
};

/** The DOMINANT rendered pixel colour inside the text box.
 *
 *  Sampling inside (not around) the box is what makes this correct for an
 *  element that paints its own fill - a button's own background is what its
 *  label sits on, not the page behind the button. Glyph strokes cover a
 *  minority of a text box's pixels, so the modal colour is the background;
 *  quantising to 8-level buckets keeps antialiased glyph edges from
 *  splintering that mode.
 *
 *  This is also the only method that sees a gradient ground or a
 *  `backdrop-filter` blur, neither of which computed styles can express. */
function sampleDominant(png, rect, dpr) {
  const x0 = Math.max(0, Math.round(rect.x * dpr));
  const y0 = Math.max(0, Math.round(rect.y * dpr));
  const x1 = Math.min(png.width - 1, Math.round((rect.x + rect.w) * dpr));
  const y1 = Math.min(png.height - 1, Math.round((rect.y + rect.h) * dpr));
  if (x1 <= x0 || y1 <= y0) return null;

  const buckets = new Map();
  for (let y = y0; y <= y1; y++) {
    for (let x = x0; x <= x1; x++) {
      const idx = (png.width * y + x) << 2;
      const r = png.data[idx];
      const g = png.data[idx + 1];
      const b = png.data[idx + 2];
      const key = `${r >> 3}:${g >> 3}:${b >> 3}`;
      let e = buckets.get(key);
      if (!e) buckets.set(key, (e = { n: 0, r: 0, g: 0, b: 0 }));
      e.n++;
      e.r += r;
      e.g += g;
      e.b += b;
    }
  }
  let best = null;
  for (const e of buckets.values()) if (!best || e.n > best.n) best = e;
  if (!best) return null;
  return [best.r / best.n, best.g / best.n, best.b / best.n];
}

const browser = await chromium.launch();
const results = {};
let latestRun = null;

for (const theme of THEMES) {
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 1000 }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  await page.addInitScript((t) => localStorage.setItem("adip-theme", t), theme);

  if (latestRun === null) {
    // --run pins LATEST to a specific run. The newest run is usually a small
    // test fixture, which renders none of the conditional panels (weak
    // concentration, held-out entities, a monetary column with returns).
    // Auditing those needs a run that actually has them.
    const pinned = process.argv.includes("--run") ? process.argv[process.argv.indexOf("--run") + 1] : null;
    if (pinned) {
      latestRun = pinned;
    } else {
      const resp = await page.request.get("http://localhost:8000/runs?limit=1");
      const runs = await resp.json();
      latestRun = runs[0]?.run_number ?? "";
    }
  }

  const themeFailures = [];
  const exempt = [];
  let checked = 0;
  let skippedOffscreen = 0;
  const perPage = {};

  for (const spec of ACTIVE_PAGES) {
    const url = BASE + spec.path.replace("LATEST", String(latestRun));
    await page.goto(url, { waitUntil: "domcontentloaded" });
    // Wait for the DATA, not a fixed guess. A flat timeout let a slow
    // /analytics fetch finish in one theme and not another, so the same page
    // was audited at 403 nodes in one pass and 77 in the next - and a PASS
    // over 77 nodes is a pass over the loading shell, not the page.
    await page.waitForLoadState("networkidle", { timeout: 45000 }).catch(() => {});
    await page.waitForTimeout(1200);

    // Grow the viewport to the whole document instead of stitching a
    // fullPage screenshot (which hangs on the Plotly pages). With no scroll
    // offset, viewport coordinates ARE document coordinates, so every rect
    // indexes correctly into the capture - including everything that used to
    // sit below the fold and go unmeasured.
    const pageHeight = await page.evaluate(() =>
      Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)
    );
    const captureHeight = Math.min(pageHeight + 40, MAX_CAPTURE_HEIGHT);
    await page.setViewportSize({ width: VIEWPORT_WIDTH, height: captureHeight });
    await page.waitForTimeout(700); // reflow before positions are read

    const nodes = await page.evaluate(COLLECT);
    const shot = await page.screenshot({ fullPage: false });
    const png = PNG.sync.read(shot);

    for (const n of nodes) {
      if (n.exempt) {
        exempt.push({ page: spec.name, reason: n.exempt, text: n.text });
        continue;
      }
      // Only skip what genuinely falls outside the capture. The old bound
      // was a hardcoded 1000px that matched no actual viewport: nodes
      // between the fold and 1000 were sampled from pixels the screenshot
      // never contained (inventing failures), and everything past 1000 was
      // silently never audited at all.
      if (n.rect.y < 0 || n.rect.y + n.rect.h > captureHeight) {
        skippedOffscreen++;
        continue;
      }
      const need = required(n.fontSize, n.fontWeight);
      const cComposite = contrast(n.fg, n.bgComposite);
      // Frosted/gradient surfaces are judged on real pixels, and on the
      // WORSE of the two numbers - a translucent panel can never pass on a
      // computed value while failing on screen. Opaque surfaces are exact
      // by composition, so pixel noise never invents a failure there.
      let cPixel = cComposite;
      if (n.needsPixel) {
        const sampled = sampleDominant(png, n.rect, 1);
        if (sampled) cPixel = contrast(n.fg, sampled);
      }
      const ratio = Math.min(cComposite, cPixel);
      checked++;
      perPage[spec.name] = (perPage[spec.name] || 0) + 1;
      if (ratio < need) {
        themeFailures.push({
          page: spec.name,
          text: n.text,
          cls: n.cls,
          size: n.fontSize,
          weight: n.fontWeight,
          need,
          composite: +cComposite.toFixed(2),
          pixel: +cPixel.toFixed(2),
          ratio: +ratio.toFixed(2),
        });
      }
    }
  }

  results[theme] = { checked, failures: themeFailures, exempt, skippedOffscreen, perPage };
  await ctx.close();
}

await browser.close();

// --- Guards on the MEASUREMENT itself, before any verdict on the pages ---
//
// These fail the run. An audit that cannot vouch for what it measured must
// not be allowed to report PASS - that is exactly how the 77-node result
// slipped through as a clean bill of health.
const measurementProblems = [];
const themeNames = Object.keys(results);

for (const [theme, r] of Object.entries(results)) {
  for (const spec of ACTIVE_PAGES) {
    const n = r.perPage[spec.name] || 0;
    if (n < MIN_NODES_PER_PAGE) {
      measurementProblems.push(
        `[${theme}] ${spec.name}: only ${n} node(s) measured (floor ${MIN_NODES_PER_PAGE}) - the page almost certainly had not finished loading`
      );
    }
  }
  if (r.skippedOffscreen) {
    measurementProblems.push(`[${theme}] ${r.skippedOffscreen} node(s) fell outside the capture and were never measured`);
  }
}

// The same pages in three themes must yield the same node count. Themes
// change colour, never content, so a divergence means one pass saw a
// different page than another - a timing artefact, and grounds to distrust
// every ratio in that run.
if (themeNames.length > 1) {
  for (const spec of ACTIVE_PAGES) {
    const counts = themeNames.map((t) => results[t].perPage[spec.name] || 0);
    if (new Set(counts).size > 1) {
      measurementProblems.push(
        `${spec.name}: node counts differ across themes (${themeNames.map((t, i) => `${t}=${counts[i]}`).join(", ")}) - ` +
          "themes change colour, not content, so one pass measured a different page"
      );
    }
  }
}

if (measurementProblems.length) {
  console.log("\n=== MEASUREMENT NOT TRUSTWORTHY ===");
  for (const problem of measurementProblems) console.log(`  ${problem}`);
}

let bad = 0;
for (const [theme, r] of Object.entries(results)) {
  console.log(`\n=== ${theme.toUpperCase()} — ${r.checked} text nodes measured ===`);
  if (!r.failures.length) {
    console.log("  PASS: every text node meets WCAG AA.");
    continue;
  }
  bad += r.failures.length;
  // Worst first.
  r.failures.sort((a, b) => a.ratio - b.ratio);
  for (const f of r.failures) {
    console.log(
      `  FAIL ${String(f.ratio).padStart(5)}:1 (need ${f.need}) [${f.page}] "${f.text}"\n` +
        `        ${f.size}px/${f.weight}  composite=${f.composite} pixel=${f.pixel}  ${f.cls}`
    );
  }
}
console.log(`\nTOTAL FAILURES: ${bad}`);
if (measurementProblems.length) {
  console.log(`MEASUREMENT PROBLEMS: ${measurementProblems.length} - a PASS above cannot be trusted`);
}
// An audit that cannot vouch for WHAT it measured must not exit 0. The
// 77-node run reported a clean bill of health over a loading shell; that
// now fails here rather than being reported as success.
process.exit(bad || measurementProblems.length ? 1 : 0);
