import { AbsoluteFill, Easing, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import { CLAIMS, type Claim } from "../claims";
import { Caption } from "../components/Caption";
import { EASE_OUT, FONT_DATA, FONT_UI, MOVE, T } from "../theme";

// Type entrance in the trust preset: blur-slide up, 21f, decelerating,
// no overshoot; lines staggered so the eye reads them in order.
export const useEnter = (delay = 0, dur = MOVE) => {
  const frame = useCurrentFrame();
  return interpolate(frame, [delay, delay + dur], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.bezier(...EASE_OUT) });
};

export const Rise: React.FC<{ delay?: number; children: React.ReactNode; style?: React.CSSProperties }> = ({ delay = 0, children, style }) => {
  const t = useEnter(delay);
  return <div style={{ opacity: t, filter: `blur(${(1 - t) * 6}px)`, transform: `translateY(${(1 - t) * 26}px)`, ...style }}>{children}</div>;
};

const useV = () => {
  const { width, height } = useVideoConfig();
  return { vertical: height > width, width, height };
};

const Stage: React.FC<{ children: React.ReactNode; center?: boolean }> = ({ children, center }) => {
  const { vertical } = useV();
  return (
    <AbsoluteFill
      style={{
        backgroundColor: T.stage,
        fontFamily: FONT_UI,
        color: T.ink,
        padding: vertical ? "0 80px" : "0 140px",
        justifyContent: "center",
        alignItems: center ? "center" : "flex-start",
        textAlign: center ? "center" : "left",
      }}
    >
      {children}
    </AbsoluteFill>
  );
};

export const Hook: React.FC = () => {
  const { vertical } = useV();
  const size = vertical ? 112 : 124;
  return (
    <Stage>
      <div style={{ fontSize: size, fontWeight: 650, lineHeight: 1.04, letterSpacing: -2.5 }}>
        <Rise>Data that fixes itself</Rise>
        <Rise delay={14} style={{ color: T.accent }}>— and proves it.</Rise>
      </div>
      <Rise delay={34} style={{ marginTop: vertical ? 56 : 44, fontSize: vertical ? 46 : 40, color: T.inkMuted, fontWeight: 500 }}>
        Agentic Data Intelligence Platform
      </Rise>
    </Stage>
  );
};

// Two failures the demo data actually contains, set in the data face.
const Specimen: React.FC<{ before: string; after: string; delay: number }> = ({ before, after, delay }) => (
  <Rise delay={delay} style={{ display: "inline-flex", alignItems: "center", gap: 22, fontFamily: FONT_DATA, fontSize: 44, padding: "16px 26px", border: `1px solid ${T.line}`, borderRadius: 6, background: T.raised }}>
    <span style={{ color: T.inkMuted }}>{before}</span>
    <span style={{ color: T.inkFaint }}>→</span>
    <span style={{ color: T.negative }}>{after}</span>
  </Rise>
);

export const Problem: React.FC = () => {
  const { vertical } = useV();
  return (
    <Stage>
      <div style={{ fontSize: vertical ? 84 : 88, fontWeight: 650, lineHeight: 1.1, letterSpacing: -1.5 }}>
        <Rise>Pipelines break silently.</Rise>
        <Rise delay={40} style={{ color: T.inkMuted }}>
          Analysts spend hours cleaning by hand.
        </Rise>
      </div>
      <div style={{ display: "flex", flexDirection: vertical ? "column" : "row", gap: 28, marginTop: 64 }}>
        <Specimen before="city" after="town" delay={70} />
        <Specimen before="80.90" after={'"$80.90"'} delay={80} />
      </div>
    </Stage>
  );
};

// The storyboard's key line, as a full-frame statement over dimmed footage.
export const KeyLine: React.FC<{ at: number }> = ({ at }) => {
  const frame = useCurrentFrame();
  const { vertical } = useV();
  const bg = interpolate(frame, [at, at + 12], [0, 0.94], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  if (frame < at) return null;
  const size = vertical ? 92 : 96;
  return (
    <AbsoluteFill style={{ fontFamily: FONT_UI }}>
      <AbsoluteFill style={{ backgroundColor: T.stage, opacity: bg }} />
      <AbsoluteFill style={{ justifyContent: "center", padding: vertical ? "0 80px" : "0 160px", color: T.ink, fontSize: size, fontWeight: 650, lineHeight: 1.1, letterSpacing: -1.5 }}>
        <Rise delay={at + 6}>
          The model chooses <span style={{ color: T.accent }}>which</span> allowlisted fix.
        </Rise>
        <Rise delay={at + 26} style={{ marginTop: 18 }}>
          The code decides <span style={{ color: T.accent }}>how</span>.
        </Rise>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

// Stat tiles (dataviz: a headline number is a tile, not a chart). Numbers
// in the data face, labels in text tokens - never a series colour - and the
// provenance under each, so every figure carries its source on screen.
const Tile: React.FC<{ c: Claim; delay: number; big?: number }> = ({ c, delay, big = 112 }) => (
  <Rise delay={delay} style={{ border: `1px solid ${T.line}`, borderRadius: 6, background: T.raised, padding: "30px 34px", display: "flex", flexDirection: "column", gap: 10 }}>
    <div style={{ fontFamily: FONT_DATA, fontSize: big, lineHeight: 1, color: T.ink, letterSpacing: -2 }}>{c.value}</div>
    <div style={{ fontSize: 38, lineHeight: 1.2, color: T.ink, fontWeight: 500 }}>{c.label}</div>
    <div style={{ fontSize: 32, color: T.inkFaint }}>{c.kind}</div>
  </Rise>
);

export const Stats: React.FC = () => {
  const { vertical } = useV();
  const tiles = [CLAIMS.accuracy, CLAIMS.wrongFix, CLAIMS.reverts, CLAIMS.egress];
  return (
    <Stage>
      <Rise style={{ fontSize: vertical ? 64 : 56, fontWeight: 650, marginBottom: 40, letterSpacing: -0.5 }}>
        Measured, not claimed.
      </Rise>
      <div style={{ display: "grid", gridTemplateColumns: vertical ? "1fr" : "1fr 1fr", gap: 24, width: "100%", maxWidth: vertical ? undefined : 1640 }}>
        {tiles.map((c, i) => (
          <Tile key={c.id} c={c} delay={10 + i * 10} big={vertical ? 96 : 104} />
        ))}
      </div>
      <Rise delay={60} style={{ marginTop: 30, fontSize: 32, color: T.inkFaint, fontFamily: FONT_DATA }}>
        source: docs/EVIDENCE.md §4
      </Rise>
      <Caption
        lines={[
          { from: 0, text: "Measured on the project's own evaluation runs — every figure is from docs/EVIDENCE.md." },
          { from: 110, text: "And with no model configured, reports fall back to a labelled template instead of failing." },
        ]}
      />
    </Stage>
  );
};

export const Limits: React.FC = () => {
  const { vertical } = useV();
  return (
    <Stage>
      <Rise style={{ fontSize: 34, fontWeight: 600, color: T.caution, marginBottom: 24 }}>Honest limits</Rise>
      <Rise delay={8} style={{ fontSize: vertical ? 84 : 84, fontWeight: 650, lineHeight: 1.1, letterSpacing: -1.2 }}>
        No scheduler yet — runs start on ingest.
      </Rise>
      <Rise delay={24} style={{ marginTop: 28, fontSize: vertical ? 46 : 44, color: T.inkMuted, lineHeight: 1.3 }}>
        No authentication. PPTX is the only export.
      </Rise>
    </Stage>
  );
};

export const CTA: React.FC<{ byline: string }> = ({ byline }) => {
  const { vertical } = useV();
  return (
    <Stage>
      <Rise style={{ fontSize: vertical ? 96 : 92, fontWeight: 650, lineHeight: 1.05, letterSpacing: -1.5 }}>
        {vertical ? (
          <>
            See the code,
            <br />
            the evidence,
            <br />
            and the limits.
          </>
        ) : (
          <>
            See the code, the evidence,
            <br />
            and the limits.
          </>
        )}
      </Rise>
      <Rise delay={16} style={{ marginTop: 48, fontFamily: FONT_DATA, fontSize: vertical ? 40 : 46, color: T.accent, wordBreak: "break-all" }}>
        github.com/sayandp/
        {vertical ? <br /> : null}
        agentic-data-intelligence-platform
      </Rise>
      <Rise delay={28} style={{ marginTop: 28, fontSize: vertical ? 46 : 44, color: T.inkMuted }}>
        {byline}
      </Rise>
    </Stage>
  );
};
