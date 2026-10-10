import { Easing, interpolate, useCurrentFrame } from "remotion";
import { EASE_CAM } from "../theme";

// A pointer marking a click that really happened during capture (the
// before/after frames on either side are the dashboard's own states).
// Page-space coordinates, so it rides the camera with the button.
export const Cursor: React.FC<{
  from: { x: number; y: number };
  to: { x: number; y: number };
  move: [number, number]; // frames
  click?: number;
  scale?: number;
}> = ({ from, to, move, click, scale = 1 }) => {
  const frame = useCurrentFrame();
  const t = interpolate(frame, move, [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.bezier(...EASE_CAM) });
  const x = from.x + (to.x - from.x) * t;
  const y = from.y + (to.y - from.y) * t;
  const press = click === undefined ? 0 : interpolate(frame, [click, click + 3, click + 8], [0, 1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const pulse = click === undefined ? 0 : interpolate(frame, [click, click + 14], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <div style={{ position: "absolute", left: x, top: y, pointerEvents: "none" }}>
      {click !== undefined && frame >= click && pulse < 1 && (
        <div
          style={{
            position: "absolute",
            left: -22 * scale,
            top: -22 * scale,
            width: 44 * scale,
            height: 44 * scale,
            borderRadius: "50%",
            border: `${2 * scale}px solid #0B6E6E`,
            transform: `scale(${0.4 + pulse})`,
            opacity: 1 - pulse,
          }}
        />
      )}
      <svg width={22 * scale} height={30 * scale} viewBox="0 0 22 30" style={{ transform: `scale(${1 - press * 0.12})`, transformOrigin: "0 0" }}>
        <path d="M1 1 L1 24 L7 18.5 L11 28 L15 26.3 L11 17 L19 17 Z" fill="#12161C" stroke="#FFFFFF" strokeWidth={1.6} strokeLinejoin="round" />
      </svg>
    </div>
  );
};
