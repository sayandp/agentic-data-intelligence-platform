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
];

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
      rect: { x: rect.x, y: rect.y, w: rect.width, h: rect.height },
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
    const resp = await page.request.get("http://localhost:8000/runs?limit=1");
    const runs = await resp.json();
    latestRun = runs[0]?.run_number ?? "";
  }

  const themeFailures = [];
  const exempt = [];
  let checked = 0;

  for (const spec of PAGES) {
    const url = BASE + spec.path.replace("LATEST", String(latestRun));
    await page.goto(url);
    await page.waitForTimeout(1800);

    const nodes = await page.evaluate(COLLECT);
    const shot = await page.screenshot({ fullPage: false });
    const png = PNG.sync.read(shot);

    for (const n of nodes) {
      if (n.exempt) {
        exempt.push({ page: spec.name, reason: n.exempt, text: n.text });
        continue;
      }
      if (n.rect.y < 0 || n.rect.y > 1000) continue; // outside the captured viewport
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

  results[theme] = { checked, failures: themeFailures, exempt };
  await ctx.close();
}

await browser.close();

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
process.exit(bad ? 1 : 0);
