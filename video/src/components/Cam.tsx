import { AbsoluteFill, Easing, Img, interpolate, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { shot, src } from "../shots";
import { EASE_CAM, T } from "../theme";

// A camera over a real full-page screenshot - shotcraft's PageCam, flat mode.
// (cx, cy) is the page-space CSS point placed at the frame's focal point;
// zoom = output px per CSS px. Textures are 2x/3x, so zoom stays <= the
// capture scale and text never upsamples (aesthetic-rules Q2).
export type CamKey = { f: number; cx: number; cy: number; zoom: number };

const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

export const camAt = (keys: CamKey[], frame: number, ease = Easing.bezier(...EASE_CAM)) => {
  let a = keys[0];
  let b = keys[keys.length - 1];
  for (let i = 0; i < keys.length - 1; i++) {
    if (frame >= keys[i].f && frame <= keys[i + 1].f) {
      a = keys[i];
      b = keys[i + 1];
      break;
    }
  }
  if (frame < keys[0].f) b = a;
  const t = a.f === b.f ? 1 : interpolate(frame, [a.f, b.f], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: ease });
  return { cx: lerp(a.cx, b.cx, t), cy: lerp(a.cy, b.cy, t), zoom: lerp(a.zoom, b.zoom, t) };
};

export const Cam: React.FC<{
  name: string;
  keys: CamKey[];
  /** screen y the focal point sits at; above centre leaves room for captions */
  focusY?: number;
  children?: React.ReactNode;
  dim?: number;
}> = ({ name, keys, focusY, children, dim = 0 }) => {
  const frame = useCurrentFrame();
  const { width, height } = useVideoConfig();
  const s = shot(name);
  const { cx, cy, zoom } = camAt(keys, frame);
  const fy = focusY ?? height * 0.44;
  return (
    <AbsoluteFill style={{ backgroundColor: T.paper, overflow: "hidden" }}>
      <div
        style={{
          position: "absolute",
          width: s.w,
          height: s.h,
          transformOrigin: "0 0",
          transform: `translate(${width / 2 - cx * zoom}px, ${fy - cy * zoom}px) scale(${zoom})`,
        }}
      >
        <Img src={staticFile(src(name))} style={{ position: "absolute", width: s.w, height: s.h }} />
        {children}
      </div>
      {dim > 0 && <AbsoluteFill style={{ backgroundColor: T.stage, opacity: dim }} />}
    </AbsoluteFill>
  );
};

/** Fit a page-space box's width into `frac` of the frame width. */
export const fitZoom = (boxW: number, frameW: number, frac = 0.86, max = 2) => Math.min(max, (frameW * frac) / boxW);
