import { AbsoluteFill, Easing, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import { Caption } from "../components/Caption";
import { EASE_OUT, FONT_DATA, FONT_UI, T } from "../theme";
import { Rise } from "./Text";

// Hand-built (no page shows this), in the dashboard's own vocabulary:
// flat bordered surfaces, one accent, status dot + label.
// Solid chips are the nine agents of EVIDENCE.md §1.1; dashed items are
// stages/packs that are not counted as agents there. "LLM" marks exactly the
// rows §1.1 marks "Uses an LLM? Yes" (narrative includes its Session Summary
// stage-2 prose, §5.5).
type Item = { name: string; llm?: string; dashed?: boolean };
const PLANES: { name: string; items: Item[] }[] = [
  { name: "Ingestion", items: [{ name: "Connectors", llm: undefined }, { name: "file · SQL · API", dashed: true }] },
  { name: "Reliability", items: [{ name: "Data-Quality" }, { name: "Diagnosis", llm: "LLM" }, { name: "Gate · rules", dashed: true }] },
  { name: "Intelligence", items: [{ name: "Exploration" }, { name: "Business Analytics" }, { name: "Query", llm: "LLM" }, { name: "Modeling", llm: "LLM · intent" }] },
  { name: "Delivery", items: [{ name: "Narrative + Summary", llm: "LLM" }, { name: "Marketing" }, { name: "Agriculture pack", dashed: true }, { name: "PPTX export", dashed: true }] },
];

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;

export const ARCH_DUR = 360;

export const Architecture: React.FC = () => {
  const frame = useCurrentFrame();
  const { width, height } = useVideoConfig();
  const vertical = height > width;
  const LLM_AT = 200;
  const llmOn = interpolate(frame, [LLM_AT, LLM_AT + 14], [0, 1], { ...clamp, easing: Easing.bezier(...EASE_OUT) });
  const bar = interpolate(frame, [70, 120], [0, 1], { ...clamp, easing: Easing.bezier(...EASE_OUT) });
  // One run travelling the graph: a token along the orchestrator bar.
  const run = interpolate(frame, [125, 195], [0, 1], { ...clamp, easing: Easing.bezier(0.45, 0, 0.55, 1) });
  const colGap = vertical ? 20 : 28;
  return (
    <AbsoluteFill style={{ backgroundColor: T.stage, fontFamily: FONT_UI, color: T.ink }}>
      <div style={{ position: "absolute", left: vertical ? 64 : 120, right: vertical ? 64 : 120, top: vertical ? 200 : 90 }}>
        <Rise style={{ fontSize: vertical ? 64 : 54, fontWeight: 650, letterSpacing: -0.6, marginBottom: vertical ? 44 : 34 }}>
          Nine agents. Four planes. One graph.
        </Rise>
        <div style={{ display: "grid", gridTemplateColumns: vertical ? "1fr 1fr" : "repeat(4, 1fr)", gap: colGap }}>
          {PLANES.map((p, pi) => (
            <Rise key={p.name} delay={14 + pi * 8} style={{ border: `1px solid ${T.line}`, borderRadius: 6, background: T.raised, padding: "20px 20px 22px" }}>
              <div style={{ fontSize: 34, fontWeight: 600, color: T.inkMuted, marginBottom: 16 }}>{p.name}</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                {p.items.map((it, ii) => (
                  <Chip key={it.name} it={it} delay={30 + pi * 8 + ii * 5} llmOn={llmOn} vertical={vertical} />
                ))}
              </div>
            </Rise>
          ))}
        </div>
        {/* Orchestrator: drawn left to right, then a run token crosses it. */}
        <div style={{ position: "relative", marginTop: 26, height: 84, border: `1px solid ${T.lineStrong}`, borderRadius: 6, background: T.sunken, clipPath: `inset(0 ${(1 - bar) * 100}% 0 0)` }}>
          <div style={{ position: "absolute", left: 24, top: 0, bottom: 0, display: "flex", alignItems: "center", gap: 18, fontSize: 36, fontWeight: 600 }}>
            <span>Orchestrator</span>
            <span style={{ color: T.inkMuted, fontWeight: 500, fontSize: 32 }}>LangGraph state graph</span>
          </div>
          {run > 0 && run < 1 && (
            <div style={{ position: "absolute", top: 34, left: `calc(${run * 100}% - 8px)`, width: 16, height: 16, borderRadius: 8, background: T.accent }} />
          )}
        </div>
      </div>
      <Caption
        lines={[
          { from: 0, text: "One LangGraph orchestrator routes every run through four planes." },
          { from: LLM_AT, text: "A model is called only where language is needed — and its output is validated before anything trusts it." },
        ]}
      />
    </AbsoluteFill>
  );
};

const Chip: React.FC<{ it: Item; delay: number; llmOn: number; vertical: boolean }> = ({ it, delay, llmOn, vertical }) => (
  <Rise
    delay={delay}
    style={{
      display: "flex",
      justifyContent: "space-between",
      alignItems: "center",
      gap: 10,
      padding: "12px 16px",
      borderRadius: 4,
      border: `1px ${it.dashed ? "dashed" : "solid"} ${it.llm && llmOn > 0.5 ? T.accent : T.line}`,
      background: it.dashed ? "transparent" : T.stage,
      color: it.dashed ? T.inkFaint : T.ink,
      fontSize: vertical ? 34 : 33,
      fontWeight: it.dashed ? 400 : 500,
      minHeight: 58,
    }}
  >
    <span>{it.name}</span>
    {it.llm && (
      <span style={{ display: "inline-flex", alignItems: "center", gap: 8, fontSize: 30, fontFamily: FONT_DATA, color: T.accent, opacity: llmOn, whiteSpace: "nowrap" }}>
        <span style={{ width: 9, height: 9, borderRadius: 5, background: T.accent }} />
        {it.llm}
      </span>
    )}
  </Rise>
);
