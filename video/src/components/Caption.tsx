import { Easing, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import { EASE_OUT, FONT_DATA, FONT_UI, T } from "../theme";

// Always-on English captions: most viewers watch muted. One band, bottom of
// frame, solid stage ink (no glass), text at >= 56px effective height
// (aesthetic-rules Q11). `backticks` render as the dashboard's mono data face.
// `step` drives the self-healing rail; an empty `text` hides the band (used
// while a full-frame statement is on screen).
export type Line = { from: number; text: string; step?: number };

const renderText = (text: string) =>
  text.split(/(`[^`]+`)/g).map((part, i) =>
    part.startsWith("`") ? (
      <span key={i} style={{ fontFamily: FONT_DATA, color: T.accent, fontSize: "0.88em" }}>
        {part.slice(1, -1)}
      </span>
    ) : (
      <span key={i}>{part}</span>
    ),
  );

export const STEPS = ["Detect", "Diagnose", "Act", "Verify", "Escalate"] as const;

export const Caption: React.FC<{
  lines: Line[];
  fontSize?: number;
}> = ({ lines, fontSize }) => {
  const frame = useCurrentFrame();
  const { width, height } = useVideoConfig();
  const vertical = height > width;
  const size = fontSize ?? (vertical ? 58 : 56);
  const current = [...lines].reverse().find((l) => frame >= l.from);
  if (!current || current.text === "") return null;
  const idx = lines.indexOf(current);
  const prev = lines[idx - 1];
  // Each line eases in over 8f; the band itself never flickers between lines.
  const tIn = interpolate(frame, [current.from, current.from + 8], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(...EASE_OUT),
  });
  const bandIn = idx === 0 || !prev || prev.text === "" ? tIn : 1;
  const padX = vertical ? 64 : 120;
  return (
    <div
      style={{
        position: "absolute",
        left: 0,
        right: 0,
        bottom: 0,
        padding: `${vertical ? 40 : 30}px ${padX}px ${vertical ? 120 : 44}px`,
        background: T.stage,
        borderTop: `1px solid ${T.line}`,
        opacity: bandIn,
        fontFamily: FONT_UI,
      }}
    >
      {current.step !== undefined && <StepRail active={current.step} vertical={vertical} />}
      <div
        style={{
          color: T.ink,
          fontSize: size,
          lineHeight: 1.22,
          fontWeight: 500,
          letterSpacing: -0.2,
          opacity: tIn,
          transform: `translateY(${(1 - tIn) * 10}px)`,
          minHeight: size * 1.22 * (vertical ? 3 : 2),
        }}
      >
        {renderText(current.text)}
      </div>
    </div>
  );
};

// A pipeline position indicator, not a numbered section label: the five
// stages in order, the current one in the accent, passed ones in ink.
const StepRail: React.FC<{ active: number; vertical: boolean }> = ({ active, vertical }) => (
  <div style={{ display: "flex", flexWrap: "wrap", gap: vertical ? 14 : 22, alignItems: "center", marginBottom: vertical ? 22 : 14, fontSize: vertical ? 34 : 34, fontWeight: 600 }}>
    {STEPS.map((s, i) => (
      <div key={s} style={{ display: "flex", alignItems: "center", gap: vertical ? 14 : 22 }}>
        <span style={{ color: i === active ? T.accent : i < active ? T.inkMuted : T.lineStrong }}>
          {i === active && <span style={{ display: "inline-block", width: 12, height: 12, borderRadius: 6, background: T.accent, marginRight: 12, transform: "translateY(-4px)" }} />}
          {s}
        </span>
        {i < STEPS.length - 1 && <span style={{ color: T.lineStrong }}>→</span>}
      </div>
    ))}
  </div>
);
