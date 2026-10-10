import { AbsoluteFill, useCurrentFrame, interpolate } from "remotion";
import { linearTiming, TransitionSeries } from "@remotion/transitions";
import { Cam } from "../components/Cam";
import { Caption } from "../components/Caption";
import { Ring } from "../components/Ring";
import { focusSwap, whip } from "../components/transitions";
import { CLAIMS } from "../claims";
import { FONT_DATA, FONT_UI, T } from "../theme";
import { KeyLine, Rise } from "./Text";

// Vertical 1080x1920. The dashboard is not responsive: at a 390px viewport it
// keeps its sidebar and lays the page out ~790 CSS px wide. These shots frame
// the content column of those 3x captures (x 310-737 on m-approvals,
// x 283-736 on m-reports) at ~2.3x, under the 3x capture scale. The two Audit
// rows read better from the desktop 2x captures, framed tight.
const TS = 16;
const TW = 12;
const swap = () => linearTiming({ durationInFrames: TS });
const whipT = () => linearTiming({ durationInFrames: TW });
const Z = 2.3;

const A1 = 120;
const A2 = 150;
const A3 = 175;
const CARD = A1 + A2 + A3;
const VERIFY = 150;
const KEPT = 120;
const DECIDE = 110;
const ACK = 95;
const PROOF = 120;
export const VHEAL_DUR = CARD + VERIFY + KEPT + DECIDE + ACK + PROOF - (TW + 4 * TS);

const ACTIONS = ["rename_column", "safe_type_cast", "strip_whitespace", "normalize_case", "escalate"];

const EnumStripV: React.FC<{ at: number; until: number }> = ({ at, until }) => {
  const frame = useCurrentFrame();
  if (frame < at || frame > until) return null;
  const pick = interpolate(frame, [at + 36, at + 46], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <div style={{ position: "absolute", left: 64, right: 64, bottom: 500, fontFamily: FONT_UI }}>
      <Rise delay={at} style={{ background: T.stage, border: `1px solid ${T.line}`, borderRadius: 6, padding: "24px 26px" }}>
        <div style={{ fontSize: 36, color: T.inkMuted, marginBottom: 18 }}>FixAction — a closed enum of {CLAIMS.actions.value}</div>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
          {ACTIONS.map((a, i) => {
            const on = a === "safe_type_cast" && pick > 0.5;
            return (
              <Rise key={a} delay={at + 8 + i * 4} style={{ fontFamily: FONT_DATA, fontSize: 36, padding: "10px 16px", borderRadius: 4, border: `1px solid ${on ? T.accent : T.line}`, color: on ? T.accent : T.inkMuted, background: T.raised }}>
                {a}
              </Rise>
            );
          })}
        </div>
      </Rise>
    </div>
  );
};

const Proof: React.FC = () => (
  <AbsoluteFill style={{ backgroundColor: T.stage, fontFamily: FONT_UI, color: T.ink, padding: "0 72px", justifyContent: "center" }}>
    {[CLAIMS.wrongFix, CLAIMS.accuracy].map((c, i) => (
      <Rise key={c.id} delay={6 + i * 12} style={{ border: `1px solid ${T.line}`, borderRadius: 6, background: T.raised, padding: "34px 36px", marginBottom: 26 }}>
        <div style={{ fontFamily: FONT_DATA, fontSize: 120, lineHeight: 1 }}>{c.value}</div>
        <div style={{ fontSize: 46, fontWeight: 500, marginTop: 12 }}>{c.label}</div>
        <div style={{ fontSize: 34, color: T.inkFaint, marginTop: 6 }}>{c.kind} · docs/EVIDENCE.md §4</div>
      </Rise>
    ))}
  </AbsoluteFill>
);

export const HealV: React.FC = () => {
  const t1 = CARD - TW;
  const t2 = t1 + VERIFY - TS;
  const t3 = t2 + KEPT - TS;
  const t4 = t3 + DECIDE - TS;
  const t5 = t4 + ACK - TS;
  return (
    <AbsoluteFill>
      <TransitionSeries>
        <TransitionSeries.Sequence durationInFrames={CARD}>
          <Cam
            name="m-approvals"
            keys={[
              { f: 0, cx: 523, cy: 720, zoom: 2.1 },
              { f: 30, cx: 523, cy: 760, zoom: Z },
              { f: A1 + 10, cx: 523, cy: 800, zoom: Z },
              { f: A1 + A2 + 6, cx: 523, cy: 845, zoom: Z },
            ]}
          >
            <Ring b={{ x: 325, y: 709, w: 360, h: 20 }} at={40} out={A1 - 4} color={T.teal} pad={8} />
            <Ring b={{ x: 325, y: 800, w: 300, h: 22 }} at={A1 + 70} out={A1 + A2 - 4} color={T.teal} pad={8} />
            <Ring b={{ x: 325, y: 826, w: 395, h: 30 }} at={A1 + A2 + 22} out={A1 + A2 + 70} color={T.teal} pad={8} />
          </Cam>
          <EnumStripV at={A1 + 20} until={A1 + A2 + 2} />
          <KeyLine at={A1 + A2 + 74} />
        </TransitionSeries.Sequence>
        <TransitionSeries.Transition presentation={whip()} timing={whipT()} />
        <TransitionSeries.Sequence durationInFrames={VERIFY}>
          <Cam name="audit-reverted" keys={[{ f: 0, cx: 1040, cy: 1030, zoom: 1.7 }, { f: 30, cx: 1075, cy: 1050, zoom: 1.9 }]}>
            <Ring b={{ x: 844, y: 1029, w: 176, h: 22 }} at={38} color={T.negativeOnLight} stroke={5} pad={10} />
            <Ring b={{ x: 1043, y: 1062, w: 250, h: 24 }} at={54} color={T.negativeOnLight} stroke={5} pad={10} />
          </Cam>
        </TransitionSeries.Sequence>
        <TransitionSeries.Transition presentation={focusSwap()} timing={swap()} />
        <TransitionSeries.Sequence durationInFrames={KEPT}>
          <Cam name="audit-kept" keys={[{ f: 0, cx: 1030, cy: 1540, zoom: 1.7 }, { f: 30, cx: 1060, cy: 1555, zoom: 1.9 }]}>
            <Ring b={{ x: 820, y: 1505, w: 400, h: 100 }} at={36} color={T.teal} pad={10} />
          </Cam>
        </TransitionSeries.Sequence>
        <TransitionSeries.Transition presentation={focusSwap()} timing={swap()} />
        <TransitionSeries.Sequence durationInFrames={DECIDE}>
          <Cam name="m-approvals" keys={[{ f: 0, cx: 523, cy: 900, zoom: Z }, { f: 30, cx: 523, cy: 960, zoom: Z }]}>
            <Ring b={{ x: 322, y: 914, w: 224, h: 120 }} at={34} color={T.teal} pad={10} />
          </Cam>
        </TransitionSeries.Sequence>
        <TransitionSeries.Transition presentation={focusSwap()} timing={swap()} />
        <TransitionSeries.Sequence durationInFrames={ACK}>
          <Cam name="m-approvals" keys={[{ f: 0, cx: 523, cy: 1720, zoom: Z }, { f: 30, cx: 523, cy: 1780, zoom: Z }]}>
            <Ring b={{ x: 609, y: 1875, w: 116, h: 32 }} at={30} color={T.teal} pad={8} />
          </Cam>
        </TransitionSeries.Sequence>
        <TransitionSeries.Transition presentation={focusSwap()} timing={swap()} />
        <TransitionSeries.Sequence durationInFrames={PROOF}>
          <Proof />
        </TransitionSeries.Sequence>
      </TransitionSeries>
      <Caption
        lines={[
          { from: 0, step: 0, text: "A rule sees `price` arrive as text — `$80.90`. No model involved." },
          { from: A1, step: 1, text: "The model diagnoses inside a closed schema: one of five actions." },
          { from: A1 + A2, step: 2, text: "Rules gate the pick: matrix, allowlist, risk, confidence." },
          { from: A1 + A2 + 74, text: "" },
          { from: t1, step: 3, text: "Re-validated: the cast fails, so it is reverted. The data stays as it arrived." },
          { from: t2, step: 3, text: "A fix that passes is kept: run #647 renamed `town` back to `city`." },
          { from: t3, step: 4, text: "Anything else goes to a person, with four decisions." },
          { from: t4, step: 4, text: "Connector warnings are acknowledged. Every decision is recorded." },
          { from: t5, text: "Measured on the evaluation runs, not claimed." },
        ]}
      />
    </AbsoluteFill>
  );
};

const R = { crane: 160, summary: 105, charts: 105, pareto: 95 };
export const VREPORTS_DUR = R.crane + R.summary + R.charts + R.pareto - (2 * TS + TW);

export const ReportsV: React.FC = () => {
  const s1 = R.crane - TS;
  const s2 = s1 + R.summary - TS;
  const s3 = s2 + R.charts - TW;
  return (
    <AbsoluteFill>
      <TransitionSeries>
        <TransitionSeries.Sequence durationInFrames={R.crane}>
          {/* Crane rise from one grounded claim link to the whole narrative. */}
          <Cam name="m-reports" keys={[{ f: 0, cx: 600, cy: 1394, zoom: 2.9 }, { f: 20, cx: 600, cy: 1394, zoom: 2.9 }, { f: 120, cx: 510, cy: 1130, zoom: 2.25 }]}>
            <Ring b={{ x: 600, y: 1384, w: 84, h: 20 }} at={2} out={18} color={T.teal} pad={6} />
          </Cam>
        </TransitionSeries.Sequence>
        <TransitionSeries.Transition presentation={focusSwap()} timing={swap()} />
        <TransitionSeries.Sequence durationInFrames={R.summary}>
          <Cam name="m-reports" keys={[{ f: 0, cx: 510, cy: 420, zoom: 2.25 }, { f: R.summary, cx: 510, cy: 440, zoom: 2.35 }]} />
        </TransitionSeries.Sequence>
        <TransitionSeries.Transition presentation={focusSwap()} timing={swap()} />
        <TransitionSeries.Sequence durationInFrames={R.charts}>
          <Cam name="m-reports" keys={[{ f: 0, cx: 510, cy: 1900, zoom: 2.25 }, { f: R.charts, cx: 510, cy: 3300, zoom: 2.25 }]} />
        </TransitionSeries.Sequence>
        <TransitionSeries.Transition presentation={whip()} timing={whipT()} />
        <TransitionSeries.Sequence durationInFrames={R.pareto}>
          <Cam name="m-analytics" keys={[{ f: 0, cx: 523, cy: 2250, zoom: Z }, { f: R.pareto, cx: 523, cy: 2300, zoom: Z }]} />
        </TransitionSeries.Sequence>
      </TransitionSeries>
      <Caption
        lines={[
          { from: 0, text: "Reports are grounded: every claim links to a real finding." },
          { from: 80, text: "Stage 2 never sees the raw findings — so it cannot invent numbers." },
          { from: s1, text: "A Session Summary sits at the top of every run." },
          { from: s2, text: "Charts are chosen by deterministic statistics." },
          { from: s3, text: "Named analytics, computed without a model." },
        ]}
      />
    </AbsoluteFill>
  );
};
