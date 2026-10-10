import { AbsoluteFill } from "remotion";
import { linearTiming, TransitionSeries } from "@remotion/transitions";
import { fade } from "@remotion/transitions/fade";
import { HealV, ReportsV, VHEAL_DUR, VREPORTS_DUR } from "./scenes/FootageV";
import { CTA, Hook } from "./scenes/Text";
import { T } from "./theme";

// Video B: the condensed teaser - hook, self-healing, reports, CTA.
export const SCENES_V = [
  { id: "hook", dur: 105 },
  { id: "self-healing", dur: VHEAL_DUR },
  { id: "reports", dur: VREPORTS_DUR },
  { id: "cta", dur: 165 },
] as const;

const SEAM = 12;
export const V_DURATION = SCENES_V.reduce((a, s) => a + s.dur, 0) - SEAM * (SCENES_V.length - 1);

export const sceneStartsV = () => {
  let t = 0;
  return SCENES_V.map((s) => {
    const start = t;
    t += s.dur - SEAM;
    return { id: s.id, start, mid: start + Math.round(s.dur / 2) };
  });
};

export const Vertical: React.FC<{ byline: string }> = ({ byline }) => {
  const body: Record<string, React.ReactNode> = { hook: <Hook />, "self-healing": <HealV />, reports: <ReportsV />, cta: <CTA byline={byline} /> };
  return (
    <AbsoluteFill style={{ backgroundColor: T.stage }}>
      <TransitionSeries>
        {SCENES_V.flatMap((s, i) => {
          const seq = (
            <TransitionSeries.Sequence key={s.id} name={s.id} durationInFrames={s.dur}>
              {body[s.id]}
            </TransitionSeries.Sequence>
          );
          return i === 0 ? [seq] : [<TransitionSeries.Transition key={`t-${s.id}`} presentation={fade()} timing={linearTiming({ durationInFrames: SEAM })} />, seq];
        })}
      </TransitionSeries>
    </AbsoluteFill>
  );
};
