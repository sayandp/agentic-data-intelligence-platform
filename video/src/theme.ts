// The dashboard's own tokens (DESIGN.md / frontend/src/index.css), so the
// video reads as the same instrument. The stage is the sidebar's ink-navy;
// on it, the Dark theme's accent and status steps (validated there against
// a dark surface) carry meaning. Nothing here is a new hue.
export const T = {
  stage: "#141922", // nav-bg
  stageDeep: "#0B0E13", // dark nav-bg
  raised: "#171C24", // dark surface-raised
  sunken: "#1E242E",
  line: "#2B333F", // dark border
  lineStrong: "#414B5A",
  ink: "#E8ECF2", // dark ink
  inkMuted: "#B3BCC9",
  inkFaint: "#8D97A5",
  accent: "#3FC3BD", // dark accent (signal teal, dark step)
  teal: "#0B6E6E", // signal teal (light surfaces)
  positive: "#56D68A",
  negative: "#FF8A8A",
  negativeOnLight: "#B91C1C",
  caution: "#E9B455",
  paper: "#F7F8FA",
} as const;

export const FONT_UI = "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif";
export const FONT_DATA = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";

export const FPS = 30;

// Motion voice: "professional trust" preset (shotcraft pipeline §1) -
// ~21f moves, decelerating, no overshoot. One voice for entrances,
// transitions and holds.
export const MOVE = 21;
export const EASE_OUT: [number, number, number, number] = [0, 0, 0.2, 1];
export const EASE_CAM: [number, number, number, number] = [0.33, 0, 0.15, 1]; // PageCam default
