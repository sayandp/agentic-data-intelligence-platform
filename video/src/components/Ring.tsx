import { useId } from "react";
import { interpolate, useCurrentFrame } from "remotion";
import type { Box } from "../shots";

// Freeze-and-circle (shotcraft speed-ramp-freeze, FreezeAnnotateReal):
// the camera holds still, a marker ellipse draws itself in 8f with a slight
// hand-drawn wobble (feTurbulence), then fades. Drawn in PAGE space, so it
// sits on the exact element the dashboard rendered.
export const Ring: React.FC<{
  b: Box;
  at: number; // frame the stroke starts drawing
  out?: number; // frame it starts fading (8f)
  color: string;
  pad?: number;
  stroke?: number;
}> = ({ b, at, out, color, pad = 14, stroke = 4 }) => {
  const frame = useCurrentFrame();
  const id = `rough-${useId().replace(/[^a-zA-Z0-9]/g, "")}`;
  const draw = interpolate(frame, [at, at + 8], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const fade = out === undefined ? 1 : interpolate(frame, [out, out + 8], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  if (draw <= 0 || fade <= 0) return null;
  const rx = b.w / 2 + pad * 2.2;
  const ry = b.h / 2 + pad;
  const cx = b.x + b.w / 2;
  const cy = b.y + b.h / 2;
  // Ramanujan's perimeter, so the dash covers the ellipse exactly once.
  const C = Math.PI * (3 * (rx + ry) - Math.sqrt((3 * rx + ry) * (rx + 3 * ry)));
  const m = 40;
  return (
    <svg
      style={{ position: "absolute", left: cx - rx - m, top: cy - ry - m, overflow: "visible", opacity: fade, pointerEvents: "none" }}
      width={2 * (rx + m)}
      height={2 * (ry + m)}
    >
      <defs>
        <filter id={id}>
          <feTurbulence type="fractalNoise" baseFrequency="0.02" numOctaves={2} seed={7} result="n" />
          <feDisplacementMap in="SourceGraphic" in2="n" scale={5} />
        </filter>
      </defs>
      <ellipse
        cx={rx + m}
        cy={ry + m}
        rx={rx}
        ry={ry}
        fill="none"
        stroke={color}
        strokeWidth={stroke}
        strokeLinecap="round"
        strokeDasharray={C}
        strokeDashoffset={C * (1 - draw)}
        transform={`rotate(-2 ${rx + m} ${ry + m})`}
        filter={`url(#${id})`}
      />
    </svg>
  );
};
