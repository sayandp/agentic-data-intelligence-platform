// Copies the v2 soundtrack from the video-shotcraft skill's bundled library
// into capture/public/audio (gitignored, like all footage). Every file below
// has a traceable Mixkit source URL (assets/audio/ATTRIBUTION.md in the skill);
// files the skill marks "cannot be traced" are deliberately not used.
//   SHOTCRAFT=<path to the video-shotcraft skill> node scripts/audio_assets.mjs
import { copyFileSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const SK = process.env.SHOTCRAFT;
if (!SK) throw new Error("set SHOTCRAFT to the video-shotcraft skill directory");

export const AUDIO = [
  // [library path, title, Mixkit URL, licence]
  ["bgm/house-vibez.mp3", "House Vibez — Lily J", "https://assets.mixkit.co/music/745/745.mp3", "Mixkit Stock Music Free License"],
  ["sfx/data/glitch-static.mp3", "Glitch static", "https://assets.mixkit.co/active_storage/sfx/1457/1457-preview.mp3"],
  ["sfx/data/glitch-virtual-quick.mp3", "Virtual quick glitch", "https://assets.mixkit.co/active_storage/sfx/2946/2946-preview.mp3"],
  ["sfx/data/break-glitch-digital.mp3", "Digital glitch break", "https://assets.mixkit.co/active_storage/sfx/2951/2951-preview.mp3"],
  ["sfx/data/data-scan.mp3", "Data scaner", "https://assets.mixkit.co/active_storage/sfx/2847/2847-preview.mp3"],
  ["sfx/transition/whoosh-fast.mp3", "Fast whoosh transition", "https://assets.mixkit.co/active_storage/sfx/1490/1490-preview.mp3"],
  ["sfx/transition/air-woosh-quick.mp3", "Quick air woosh", "https://assets.mixkit.co/active_storage/sfx/2605/2605-preview.mp3"],
  ["sfx/transition/swoosh-quick.mp3", "Fast small sweep transition", "https://assets.mixkit.co/active_storage/sfx/166/166-preview.mp3"],
  ["sfx/transition/transition-snap.mp3", "Fast transitions swoosh", "https://assets.mixkit.co/active_storage/sfx/3115/3115-preview.mp3"],
  ["sfx/ui/switch-click-quick.mp3", "Quick switch click", "https://assets.mixkit.co/active_storage/sfx/2582/2582-preview.mp3"],
  ["sfx/ui/chime-crystal.mp3", "Crystal chime", "https://assets.mixkit.co/active_storage/sfx/3108/3108-preview.mp3"],
  ["sfx/light/sparkle-touch.mp3", "Magic sparkle touch", "https://assets.mixkit.co/active_storage/sfx/3083/3083-preview.mp3"],
  ["sfx/light/shimmer-sparkle-sweep.mp3", "Sweeping sparkle presentation intro", "https://assets.mixkit.co/active_storage/sfx/2633/2633-preview.mp3"],
  ["sfx/film/tape-rewind-fast.mp3", "Fast tape rewind cinematic transition", "https://assets.mixkit.co/active_storage/sfx/1092/1092-preview.mp3"],
  ["sfx/impact/bass-hit-short.mp3", "Short bass hit", "https://assets.mixkit.co/active_storage/sfx/2299/2299-preview.mp3"],
  ["sfx/impact/impact-cine-big.mp3", "Big cinematic impact", "https://assets.mixkit.co/active_storage/sfx/788/788-preview.mp3"],
  ["sfx/impact/hit-fast-exciting.mp3", "Exciting fast hit", "https://assets.mixkit.co/active_storage/sfx/2180/2180-preview.mp3"],
  ["sfx/impact/hit-weak.mp3", "Weak hit impact", "https://assets.mixkit.co/active_storage/sfx/2148/2148-preview.mp3"],
  ["sfx/text/typewriter-digital.mp3", "Digital typewriter", "https://assets.mixkit.co/active_storage/sfx/1363/1363-preview.mp3"],
  ["sfx/camera/zoom-swipe-fast.mp3", "Fast swipe zoom", "https://assets.mixkit.co/active_storage/sfx/2627/2627-preview.mp3"],
];

const OUT = "capture/public/audio";
mkdirSync(OUT, { recursive: true });
for (const [p] of AUDIO) copyFileSync(join(SK, "assets/audio", p), join(OUT, p.split("/").pop()));
writeFileSync("capture/public/audio/credits.json", JSON.stringify(AUDIO.map(([p, title, url, lic]) => ({ file: p.split("/").pop(), title, url, licence: lic ?? "Mixkit Sound Effects Free License" })), null, 1));
console.log(`copied ${AUDIO.length} files to ${OUT}`);
