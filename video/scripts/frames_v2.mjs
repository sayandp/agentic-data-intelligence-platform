// v2 frame grabs (one per scene / hero beat) + thumbnails, from the same
// compositions as the MP4s. Rendered with audio off: the @remotion/media
// mixer cannot run in image-sequence mode (EINVAL), and stills need no sound.
//   node scripts/frames_v2.mjs
import { execSync } from "node:child_process";
import { mkdirSync, readdirSync, renameSync, rmSync, writeFileSync } from "node:fs";

const H = [
  ["01-hook-title", 120], ["02-problem", 250], ["03-how-llm-agents", 560], ["04-hero-detect", 620],
  ["05-hero-diagnose", 660], ["06-hero-fix-split", 730], ["07-hero-verify", 830], ["08-hero-rewind", 945],
  ["09-hero-caught", 1100], ["10-human-freeze", 1150], ["11-human-status-flip", 1260], ["12-ask-typing", 1365],
  ["13-ask-answer", 1500], ["14-report-writes", 1595], ["15-report-themes", 1760], ["16-trust-tokens", 1825],
  ["17-trust-egress", 1865], ["18-trust-stat", 1910], ["19-domains", 2040], ["20-end", 2240],
];
const V = [["v01-hook", 120], ["v02-detect", 200], ["v03-fix", 330], ["v04-rewind", 520], ["v05-caught", 640], ["v06-ask", 760], ["v07-answer", 920], ["v08-stat", 1060], ["v09-end", 1160]];
const THUMB = { Horizontal2: 1100, Vertical2: 640 };

writeFileSync("out/props-stills.json", JSON.stringify({ byline: "by @sayandp", gain: 1, audio: false }));
const grab = (comp, items) => {
  const tmp = `out/grab_${comp}`;
  rmSync(tmp, { recursive: true, force: true });
  execSync(`npx remotion render src/index.ts ${comp} ${tmp} --props=out/props-stills.json --frames=${items.map((i) => i[1]).join(",")} --image-format=png`, { stdio: "inherit" });
  mkdirSync("out/frames-v2", { recursive: true });
  const files = readdirSync(tmp).sort();
  items.forEach(([name], i) => renameSync(`${tmp}/${files[i]}`, `out/frames-v2/${name}.png`));
  rmSync(tmp, { recursive: true, force: true });
};
grab("Horizontal2", H);
grab("Vertical2", V);
for (const [comp, f] of Object.entries(THUMB)) {
  const name = comp === "Horizontal2" ? "horizontal" : "vertical";
  execSync(`npx remotion still src/index.ts ${comp} out/agentic-platform-${name}-v2-thumb.png --props=out/props-stills.json --frame=${f}`, { stdio: "inherit" });
}
