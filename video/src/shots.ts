// Geometry of the captured footage (scripts/capture.mjs -> layout.json).
// Every box is in the page's own CSS px, so overlays line up with pixels
// that were actually rendered by the dashboard.
import layoutJson from "../capture/public/layout.json";

export type Box = { x: number; y: number; w: number; h: number };
type Shot = { w: number; h: number; boxes: Record<string, Box> };
const layout = layoutJson as unknown as Record<string, Shot>;

export const shot = (name: string): Shot => {
  const s = layout[name];
  if (!s) throw new Error(`no captured shot "${name}" - run scripts/capture.mjs`);
  return s;
};

export const box = (name: string, key: string): Box => {
  const b = shot(name).boxes[key];
  if (!b) throw new Error(`shot "${name}" has no box "${key}"`);
  return b;
};

export const mid = (b: Box) => ({ x: b.x + b.w / 2, y: b.y + b.h / 2 });

export const src = (name: string) => `shots/${name}.png`;
