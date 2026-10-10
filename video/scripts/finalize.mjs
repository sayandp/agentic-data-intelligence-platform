// Delivery pass: Remotion's JPEG-frame pipeline tags the stream full-range
// (yuvj420p), which some players and upload pipelines mis-handle. Convert to
// limited-range yuv420p H.264 with the moov atom up front (+faststart), and
// check the result.
//   node scripts/finalize.mjs        v1 (silent), after rendering to out/raw/
//   node scripts/finalize.mjs v2     v2: also muxes out/raw/<name>.wav, the
//                                    soundtrack mastered by scripts/master_audio.py
import { execFileSync } from "node:child_process";
import { mkdirSync, statSync } from "node:fs";

const npx = process.platform === "win32" ? "npx.cmd" : "npx";
const run = (args) => execFileSync(npx, ["remotion", ...args], { encoding: "utf8", shell: process.platform === "win32" });

mkdirSync("out", { recursive: true });
const v2 = process.argv[2] === "v2";
const names = v2 ? ["agentic-platform-horizontal-v2", "agentic-platform-vertical-v2"] : ["agentic-platform-horizontal", "agentic-platform-vertical"];
for (const name of names) {
  const src = `out/raw/${name}.mp4`;
  const dst = `out/${name}.mp4`;
  const video = ["-vf", "scale=in_range=full:out_range=tv,format=yuv420p", "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-profile:v", "high", "-color_range", "tv"];
  const audio = v2 ? ["-i", `out/raw/${name}.wav`, "-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "192k", "-shortest"] : ["-an"];
  run(["ffmpeg", "-y", "-v", "error", "-i", src, ...audio, ...video, "-movflags", "+faststart", dst]);
  const probe = run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,pix_fmt,width,height,sample_rate", "-show_entries", "format=duration", "-of", "compact", dst]).trim();
  const mb = statSync(dst).size / 1e6;
  if (!probe.includes("pix_fmt=yuv420p") || !probe.includes("codec_name=h264")) throw new Error(`${dst}: ${probe}`);
  if (mb >= 50) throw new Error(`${dst} is ${mb.toFixed(1)} MB (limit 50)`);
  console.log(`${dst}  ${mb.toFixed(1)} MB  ${probe.replace(/\n/g, " ")}`);
}
