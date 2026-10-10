// Delivery pass: Remotion's JPEG-frame pipeline tags the stream full-range
// (yuvj420p), which some players and upload pipelines mis-handle. Convert to
// limited-range yuv420p H.264 with the moov atom up front (+faststart), and
// check the result.
//   node scripts/finalize.mjs        (after rendering to out/raw/)
import { execFileSync } from "node:child_process";
import { mkdirSync, statSync } from "node:fs";

const npx = process.platform === "win32" ? "npx.cmd" : "npx";
const run = (args) => execFileSync(npx, ["remotion", ...args], { encoding: "utf8", shell: process.platform === "win32" });

mkdirSync("out", { recursive: true });
for (const name of ["agentic-platform-horizontal", "agentic-platform-vertical"]) {
  const src = `out/raw/${name}.mp4`;
  const dst = `out/${name}.mp4`;
  run(["ffmpeg", "-y", "-v", "error", "-i", src, "-vf", "scale=in_range=full:out_range=tv,format=yuv420p", "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-profile:v", "high", "-color_range", "tv", "-movflags", "+faststart", "-an", dst]);
  const probe = run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,pix_fmt,width,height", "-show_entries", "format=duration", "-of", "compact", dst]).trim();
  const mb = statSync(dst).size / 1e6;
  if (!probe.includes("pix_fmt=yuv420p") || !probe.includes("codec_name=h264")) throw new Error(`${dst}: ${probe}`);
  if (mb >= 50) throw new Error(`${dst} is ${mb.toFixed(1)} MB (limit 50)`);
  console.log(`${dst}  ${mb.toFixed(1)} MB  ${probe.replace(/\n/g, " ")}`);
}
