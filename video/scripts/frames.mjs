// One frame grab per section + a thumbnail per video, cut from the
// compositions (same timeline as the MP4s).
//   node scripts/frames.mjs
//
// Section frames are scene start + a moment inside it. Scene starts follow
// SCENES_H / SCENES_V (12f seams); the script checks the totals against the
// compositions' real durations so a timing edit cannot silently shift grabs.
import { execSync } from "node:child_process";
import { mkdirSync, renameSync, readdirSync, rmSync } from "node:fs";

const H = [
  ["01-hook", 90], ["02-problem", 260], ["03-architecture", 560], ["04-ingest", 820],
  ["05-self-healing-keyline", 1320], ["06-self-healing-revert", 1430], ["07-self-healing-kept", 1570],
  ["08-self-healing-decision", 1670], ["09-insights-narrative", 1955], ["10-insights-ask", 2262],
  ["11-domains", 2590], ["12-trust-egress", 2860], ["13-trust-measured", 3040], ["14-trust-themes", 3200],
  ["15-limits", 3290], ["16-cta", 3450],
];
const V = [["v1-hook", 80], ["v2-self-healing-revert", 596], ["v3-reports", 1195], ["v4-cta", 1560]];
const THUMB = { Horizontal: 1430, Vertical: 590 };
const EXPECT = { Horizontal: 3522, Vertical: 1619 };

const list = execSync("npx remotion compositions src/index.ts", { encoding: "utf8" });
for (const [id, n] of Object.entries(EXPECT)) {
  const m = list.match(new RegExp(`${id}\\s+\\d+\\s+\\S+\\s+(\\d+)`));
  if (!m || Number(m[1]) !== n) throw new Error(`${id} is ${m?.[1]} frames, frames.mjs expects ${n}: update the section frames`);
}

const grab = (comp, items, dir) => {
  const tmp = `out/grab_${comp}`;
  rmSync(tmp, { recursive: true, force: true });
  execSync(`npx remotion render src/index.ts ${comp} ${tmp} --frames=${items.map((i) => i[1]).join(",")} --image-format=png`, { stdio: "inherit" });
  mkdirSync(dir, { recursive: true });
  const files = readdirSync(tmp).sort();
  items.forEach(([name], i) => renameSync(`${tmp}/${files[i]}`, `${dir}/${name}.png`));
  rmSync(tmp, { recursive: true, force: true });
};

grab("Horizontal", H, "out/frames");
grab("Vertical", V, "out/frames");
for (const [comp, f] of Object.entries(THUMB)) {
  execSync(`npx remotion still src/index.ts ${comp} out/agentic-platform-${comp.toLowerCase()}-thumb.png --frame=${f}`, { stdio: "inherit" });
}
