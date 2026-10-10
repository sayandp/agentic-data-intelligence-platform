import { AbsoluteFill, useCurrentFrame, interpolate } from "remotion";
import { linearTiming, TransitionSeries } from "@remotion/transitions";
import { Cam } from "../components/Cam";
import { Caption } from "../components/Caption";
import { Cursor } from "../components/Cursor";
import { Ring } from "../components/Ring";
import { focusSwap, whip } from "../components/transitions";
import { CLAIMS } from "../claims";
import { FONT_DATA, FONT_UI, T } from "../theme";
import { KeyLine, Rise } from "./Text";

// Coordinates are page-space CSS px, measured on the 2x captures
// (capture/public/shots). The content column of every desktop page spans
// x 288-1408, centre 848; full-width framing is zoom 1920/1440.
const CX = 848;
const FULL = 1920 / 1440;
const TS = 16; // focus-swap seam, frames
const TW = 12; // whip seam
const swap = () => linearTiming({ durationInFrames: TS });
const whipT = () => linearTiming({ durationInFrames: TW });

// ------------------------------------------------------------------ ingest
export const INGEST_DUR = 80 + 64 + 110 - 2 * TS;
export const Ingest: React.FC = () => (
  <AbsoluteFill>
    <TransitionSeries>
      <TransitionSeries.Sequence durationInFrames={80}>
        <Cam name="sources-api" keys={[{ f: 0, cx: 760, cy: 430, zoom: 1.6 }, { f: 80, cx: 760, cy: 440, zoom: 1.72 }]} />
      </TransitionSeries.Sequence>
      <TransitionSeries.Transition presentation={focusSwap()} timing={swap()} />
      <TransitionSeries.Sequence durationInFrames={64}>
        <Cam name="sources-sql" keys={[{ f: 0, cx: 760, cy: 440, zoom: 1.72 }, { f: 64, cx: 760, cy: 445, zoom: 1.78 }]} />
      </TransitionSeries.Sequence>
      <TransitionSeries.Transition presentation={focusSwap()} timing={swap()} />
      <TransitionSeries.Sequence durationInFrames={110}>
        {/* The registered-sources table: file, api and sql rows, and the new
            upload ingesting live with its elapsed time. */}
        <Cam name="ingest-2" keys={[{ f: 0, cx: 860, cy: 600, zoom: 1.45 }, { f: 60, cx: 1060, cy: 720, zoom: 1.75 }]}>
          <Ring b={{ x: 1181, y: 772, w: 208, h: 36 }} at={62} color={T.teal} />
        </Cam>
      </TransitionSeries.Sequence>
    </TransitionSeries>
    <Caption
      lines={[
        { from: 0, text: "A run starts when you ingest — from a file, a SQL query or an API endpoint." },
        { from: 128, text: "Ingest progress is live, with the elapsed time on screen." },
      ]}
    />
  </AbsoluteFill>
);

// ------------------------------------------------------------ self-healing
// One continuous camera over run #648's escalation card carries Detect,
// Diagnose and Act; then whip to its Audit row for Verify, swap to the kept
// fix of run #647, and back to the queue for the human decision.
const A1 = 135; // detect
const A2 = 165; // diagnose
const A3 = 220; // act + key line (the key line holds ~2s before the whip)
const CARD = A1 + A2 + A3;
const VERIFY = 170;
const KEPT = 130;
const DECIDE = 130;
const ACK = 120;
export const HEAL_DUR = CARD + VERIFY + KEPT + DECIDE + ACK - (TW + 3 * TS);

const ACTIONS = ["rename_column", "safe_type_cast", "strip_whitespace", "normalize_case", "escalate"];

const EnumStrip: React.FC<{ at: number; until: number }> = ({ at, until }) => {
  const frame = useCurrentFrame();
  if (frame < at || frame > until) return null;
  const pick = interpolate(frame, [at + 36, at + 46], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <div style={{ position: "absolute", left: 120, right: 120, bottom: 300, fontFamily: FONT_UI }}>
      <Rise delay={at} style={{ background: T.stage, border: `1px solid ${T.line}`, borderRadius: 6, padding: "22px 26px" }}>
        <div style={{ fontSize: 32, color: T.inkMuted, marginBottom: 16 }}>
          FixAction — a closed enum of {CLAIMS.actions.value}. Anything else fails to parse.
        </div>
        <div style={{ display: "flex", gap: 14, flexWrap: "wrap" }}>
          {ACTIONS.map((a, i) => {
            const on = a === "safe_type_cast";
            return (
              <Rise key={a} delay={at + 8 + i * 4} style={{ fontFamily: FONT_DATA, fontSize: 34, padding: "10px 18px", borderRadius: 4, border: `1px solid ${on && pick > 0.5 ? T.accent : T.line}`, color: on && pick > 0.5 ? T.accent : T.inkMuted, background: T.raised }}>
                {a}
              </Rise>
            );
          })}
        </div>
      </Rise>
    </div>
  );
};

export const Heal: React.FC = () => {
  const t1 = CARD - TW; // start of verify (after whip overlap)
  const t2 = t1 + VERIFY - TS;
  const t3 = t2 + KEPT - TS;
  const t4 = t3 + DECIDE - TS;
  return (
    <AbsoluteFill>
      <TransitionSeries>
        <TransitionSeries.Sequence durationInFrames={CARD}>
          <Cam
            name="approvals"
            keys={[
              { f: 0, cx: CX, cy: 560, zoom: 1.5 },
              { f: 30, cx: 640, cy: 545, zoom: 1.8 },
              { f: A1 + 10, cx: 760, cy: 590, zoom: 1.8 },
              { f: A1 + A2 + 6, cx: 780, cy: 620, zoom: 1.8 },
            ]}
          >
            <Ring b={{ x: 326, y: 526, w: 520, h: 20 }} at={44} out={A1 - 4} color={T.teal} />
            <Ring b={{ x: 1040, y: 571, w: 250, h: 24 }} at={A1 + 70} out={A1 + A2 - 4} color={T.teal} />
            <Ring b={{ x: 400, y: 600, w: 470, h: 18 }} at={A1 + A2 + 24} out={A1 + A2 + 72} color={T.teal} />
          </Cam>
          <EnumStrip at={A1 + 20} until={A1 + A2 + 2} />
          <KeyLine at={A1 + A2 + 78} />
        </TransitionSeries.Sequence>
        <TransitionSeries.Transition presentation={whip()} timing={whipT()} />
        <TransitionSeries.Sequence durationInFrames={VERIFY}>
          {/* Freeze-and-circle: the camera lands and holds; the ring draws on
              the revert and the post-condition that caused it. */}
          <Cam name="audit-reverted" keys={[{ f: 0, cx: 900, cy: 1000, zoom: 1.5 }, { f: 30, cx: 1000, cy: 1040, zoom: 1.75 }]}>
            <Ring b={{ x: 844, y: 1029, w: 176, h: 22 }} at={40} color={T.negativeOnLight} stroke={5} />
            <Ring b={{ x: 1043, y: 1062, w: 250, h: 24 }} at={58} color={T.negativeOnLight} stroke={5} />
          </Cam>
        </TransitionSeries.Sequence>
        <TransitionSeries.Transition presentation={focusSwap()} timing={swap()} />
        <TransitionSeries.Sequence durationInFrames={KEPT}>
          <Cam name="audit-kept" keys={[{ f: 0, cx: 800, cy: 1520, zoom: 1.55 }, { f: 40, cx: 820, cy: 1545, zoom: 1.7 }]}>
            <Ring b={{ x: 820, y: 1505, w: 400, h: 100 }} at={40} color={T.teal} />
          </Cam>
        </TransitionSeries.Sequence>
        <TransitionSeries.Transition presentation={focusSwap()} timing={swap()} />
        <TransitionSeries.Sequence durationInFrames={DECIDE}>
          {/* Captured while the pointer hovered "Keep data as-is" on #648. */}
          <Cam name="approvals-hover-keep" keys={[{ f: 0, cx: 700, cy: 600, zoom: 1.6 }, { f: 30, cx: 640, cy: 640, zoom: 1.85 }]}>
            <Ring b={{ x: 326, y: 650, w: 619, h: 34 }} at={30} out={92} color={T.teal} />
            <Cursor from={{ x: 760, y: 760 }} to={{ x: 470, y: 670 }} move={[60, 90]} click={98} scale={0.8} />
          </Cam>
        </TransitionSeries.Sequence>
        <TransitionSeries.Transition presentation={focusSwap()} timing={swap()} />
        <TransitionSeries.Sequence durationInFrames={ACK}>
          <AckBeat />
        </TransitionSeries.Sequence>
      </TransitionSeries>
      <Caption
        lines={[
          { from: 0, step: 0, text: "A rule sees `price` arrive as text — `$80.90`. Detection is deterministic: no model involved." },
          { from: A1, step: 1, text: "The model diagnoses inside a closed schema: one of five actions, plus a confidence." },
          { from: A1 + A2, step: 2, text: "Rules gate the pick: permitted by the matrix and the allowlist, risk low, confidence high enough." },
          { from: A1 + A2 + 78, text: "" },
          { from: t1, step: 3, text: "Re-validate after the fix. The cast fails its post-condition, so it is reverted — the data stays as it arrived." },
          { from: t2, step: 3, text: "When a fix passes, it is kept and logged: run #647 renamed `town` back to `city` on its own." },
          { from: t3, step: 4, text: "Anything else goes to a person: approve, keep as-is, accept a new baseline, or discard the run." },
          { from: t4, step: 4, text: "Connector warnings are acknowledged — and every decision is recorded with who made it." },
        ]}
      />
    </AbsoluteFill>
  );
};

// Hover frame (real), cursor click, then the frame captured after the click.
const AckBeat: React.FC = () => {
  const frame = useCurrentFrame();
  const after = frame >= 64;
  const keys = [{ f: 0, cx: CX, cy: 530, zoom: 1.45 }];
  return after ? (
    <Cam name="approvals-after-ack" keys={keys} />
  ) : (
    <Cam name="approvals-hover-ack" keys={keys}>
      <Cursor from={{ x: 1100, y: 660 }} to={{ x: 1300, y: 556 }} move={[14, 44]} click={54} scale={0.8} />
    </Cam>
  );
};

// ----------------------------------------------------------------- insights
const I = { crane: 160, charts: 105, analytics: 95, ask: 150, predict: 95, compare: 85, exportB: 100 };
export const INSIGHTS_DUR = Object.values(I).reduce((a, b) => a + b, 0) - TS - 5 * TW;

export const Insights: React.FC = () => {
  const s1 = I.crane - TS;
  const s2 = s1 + I.charts - TW;
  const s3 = s2 + I.analytics - TW;
  const s4 = s3 + I.ask - TW;
  const s5 = s4 + I.predict - TW;
  const s6 = s5 + I.compare - TW;
  return (
    <AbsoluteFill>
      <TransitionSeries>
        <TransitionSeries.Sequence durationInFrames={I.crane}>
          {/* Crane rise: hold on one grounded claim link, then pull up and
              back until the Summary, caveat and narrative fill the frame. */}
          <Cam name="reports" keys={[{ f: 0, cx: 520, cy: 1060, zoom: 2 }, { f: 20, cx: 520, cy: 1060, zoom: 2 }, { f: 120, cx: CX, cy: 470, zoom: 1.2 }]}>
            <Ring b={{ x: 468, y: 1065, w: 90, h: 21 }} at={2} out={18} color={T.teal} />
          </Cam>
        </TransitionSeries.Sequence>
        <TransitionSeries.Transition presentation={focusSwap()} timing={swap()} />
        <TransitionSeries.Sequence durationInFrames={I.charts}>
          <Cam name="reports" keys={[{ f: 0, cx: CX, cy: 1500, zoom: FULL }, { f: I.charts, cx: CX, cy: 2650, zoom: FULL }]} />
        </TransitionSeries.Sequence>
        <TransitionSeries.Transition presentation={whip()} timing={whipT()} />
        <TransitionSeries.Sequence durationInFrames={I.analytics}>
          <Cam name="analytics" keys={[{ f: 0, cx: CX, cy: 1560, zoom: FULL }, { f: I.analytics, cx: CX, cy: 1640, zoom: 1.4 }]} />
        </TransitionSeries.Sequence>
        <TransitionSeries.Transition presentation={whip()} timing={whipT()} />
        <TransitionSeries.Sequence durationInFrames={I.ask}>
          <AskBeat />
        </TransitionSeries.Sequence>
        <TransitionSeries.Transition presentation={whip()} timing={whipT()} />
        <TransitionSeries.Sequence durationInFrames={I.predict}>
          <Cam name="predict-result" keys={[{ f: 0, cx: CX, cy: 740, zoom: 1.45 }, { f: I.predict, cx: CX, cy: 760, zoom: 1.6 }]} />
        </TransitionSeries.Sequence>
        <TransitionSeries.Transition presentation={whip()} timing={whipT()} />
        <TransitionSeries.Sequence durationInFrames={I.compare}>
          <Cam name="compare" keys={[{ f: 0, cx: CX, cy: 470, zoom: FULL }, { f: I.compare, cx: CX, cy: 500, zoom: 1.45 }]} />
        </TransitionSeries.Sequence>
        <TransitionSeries.Transition presentation={whip()} timing={whipT()} />
        <TransitionSeries.Sequence durationInFrames={I.exportB}>
          <ExportBeat />
        </TransitionSeries.Sequence>
      </TransitionSeries>
      <Caption
        lines={[
          { from: 0, text: "Every run ends with a Session Summary and a grounded narrative." },
          { from: 80, text: "Stage 2 never sees the raw findings — so it cannot invent numbers." },
          { from: s1, text: "Exploration: deterministic statistics choose every chart." },
          { from: s2, text: "Business analytics: named methods. The ones that don't apply say why." },
          { from: s3, text: "Ask in plain English → the generated SQL → the result. Never the answer alone." },
          { from: s4, text: "Modeling: a forecast scored next to its naive baselines." },
          { from: s5, text: "Compare two runs of one source: the auto-fix shows up as a change." },
          { from: s6, text: "Export: one PPTX deck per run — the only export format." },
        ]}
      />
    </AbsoluteFill>
  );
};

const AskBeat: React.FC = () => {
  const frame = useCurrentFrame();
  if (frame < 56) {
    return (
      <Cam name="ask-typed" keys={[{ f: 0, cx: CX, cy: 300, zoom: 1.6 }]}>
        <Cursor from={{ x: 520, y: 450 }} to={{ x: 334, y: 374 }} move={[6, 34]} click={42} scale={0.8} />
      </Cam>
    );
  }
  return <Cam name="ask-result" keys={[{ f: 56, cx: CX, cy: 430, zoom: 1.5 }, { f: 150, cx: CX, cy: 640, zoom: 1.5 }]} />;
};

const ExportBeat: React.FC = () => {
  const frame = useCurrentFrame();
  const keys = [{ f: 0, cx: CX, cy: 640, zoom: FULL }, { f: 40, cx: 700, cy: 720, zoom: 1.6 }];
  return frame < 66 ? (
    <Cam name="export-ready" keys={keys}>
      <Cursor from={{ x: 640, y: 760 }} to={{ x: 360, y: 893 }} move={[24, 52]} click={60} scale={0.8} />
    </Cam>
  ) : (
    <Cam name="export-clicked" keys={keys} />
  );
};

// ------------------------------------------------------------------ domains
export const DOMAINS_DUR = 130 + 130 - TW;
export const Domains: React.FC = () => (
  <AbsoluteFill>
    <TransitionSeries>
      <TransitionSeries.Sequence durationInFrames={130}>
        <Cam name="marketing" keys={[{ f: 0, cx: CX, cy: 560, zoom: FULL }, { f: 130, cx: CX, cy: 520, zoom: 1.45 }]} />
      </TransitionSeries.Sequence>
      <TransitionSeries.Transition presentation={whip()} timing={whipT()} />
      <TransitionSeries.Sequence durationInFrames={130}>
        <Cam name="agriculture" keys={[{ f: 0, cx: CX, cy: 480, zoom: FULL }, { f: 130, cx: CX, cy: 440, zoom: 1.45 }]} />
      </TransitionSeries.Sequence>
    </TransitionSeries>
    <Caption
      lines={[
        { from: 0, text: "Domain packs read the same run: Marketing (a public ad-conversion export)…" },
        { from: 130 - TW, text: "…and Agriculture (public crop data). A new domain = roles + rules + a view." },
      ]}
    />
  </AbsoluteFill>
);

// ------------------------------------------------------------------- trust
export const EGRESS_DUR = 170;
export const Egress: React.FC = () => (
  <AbsoluteFill>
    <Cam name="audit-kept" keys={[{ f: 0, cx: CX, cy: 1050, zoom: FULL }, { f: 36, cx: 900, cy: 1110, zoom: 1.6 }]}>
      <Ring b={{ x: 1071, y: 1103, w: 142, h: 34 }} at={50} color={T.teal} />
    </Cam>
    <Caption
      lines={[
        { from: 0, text: "Personal data is masked before any outbound model call — here, `customer_email`." },
        { from: 90, text: "The egress trail records shape only: which columns, how many values. Never the rows." },
      ]}
    />
  </AbsoluteFill>
);

// Default -> Dark -> Aurora: the same report, re-skinned by one attribute.
export const THEMES_DUR = 105;
export const Themes: React.FC = () => {
  const frame = useCurrentFrame();
  const name = frame < 35 ? "theme-default" : frame < 70 ? "theme-dark" : "theme-aurora";
  const label = frame < 35 ? "Default" : frame < 70 ? "Dark" : "Aurora";
  return (
    <AbsoluteFill>
      <Cam name={name} keys={[{ f: 0, cx: 720, cy: 450, zoom: FULL }]} />
      <div style={{ position: "absolute", top: 40, right: 60, fontFamily: FONT_UI, fontSize: 34, fontWeight: 600, color: T.ink, background: T.stage, border: `1px solid ${T.line}`, borderRadius: 6, padding: "10px 20px" }}>
        {label}
      </div>
      <Caption lines={[{ from: 0, text: `Default, Dark, Aurora — one information architecture. ${CLAIMS.contrast.value} ${CLAIMS.contrast.label}.` }]} />
    </AbsoluteFill>
  );
};

