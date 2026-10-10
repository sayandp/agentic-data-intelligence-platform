import { AbsoluteFill } from "remotion";
import { linearTiming, TransitionSeries } from "@remotion/transitions";
import { fade } from "@remotion/transitions/fade";
import { ARCH_DUR, Architecture } from "./scenes/Architecture";
import { Domains, DOMAINS_DUR, Egress, EGRESS_DUR, Heal, HEAL_DUR, Ingest, INGEST_DUR, Insights, INSIGHTS_DUR, Themes, THEMES_DUR } from "./scenes/FootageH";
import { CTA, Hook, Limits, Problem, Stats } from "./scenes/Text";
import { focusSwap } from "./components/transitions";
import { T } from "./theme";

// The single timeline for video A. Scene order follows the storyboard:
// hook, problem, architecture, ingest, self-healing (hero), insights,
// domains, trust (egress, measured results, themes), limits, CTA.
export const SCENES_H = [
  { id: "hook", dur: 120 },
  { id: "problem", dur: 180 },
  { id: "architecture", dur: ARCH_DUR },
  { id: "ingest", dur: INGEST_DUR },
  { id: "self-healing", dur: HEAL_DUR },
  { id: "insights", dur: INSIGHTS_DUR },
  { id: "domains", dur: DOMAINS_DUR },
  { id: "trust-egress", dur: EGRESS_DUR },
  { id: "trust-measured", dur: 210 },
  { id: "trust-themes", dur: THEMES_DUR },
  { id: "limits", dur: 150 },
  { id: "cta", dur: 165 },
] as const;

const SEAM = 12;
export const H_DURATION = SCENES_H.reduce((a, s) => a + s.dur, 0) - SEAM * (SCENES_H.length - 1);

/** Start frame of each scene in the finished cut (for frame grabs / QA). */
export const sceneStarts = () => {
  let t = 0;
  return SCENES_H.map((s) => {
    const start = t;
    t += s.dur - SEAM;
    return { id: s.id, start, mid: start + Math.round(s.dur / 2) };
  });
};

export type HProps = { byline: string };

export const Horizontal: React.FC<HProps> = ({ byline }) => {
  const body: Record<string, React.ReactNode> = {
    hook: <Hook />,
    problem: <Problem />,
    architecture: <Architecture />,
    ingest: <Ingest />,
    "self-healing": <Heal />,
    insights: <Insights />,
    domains: <Domains />,
    "trust-egress": <Egress />,
    "trust-measured": <Stats />,
    "trust-themes": <Themes />,
    limits: <Limits />,
    cta: <CTA byline={byline} />,
  };
  // Text -> text and text <-> footage seams are a plain 12f fade; footage ->
  // footage seams rack focus (shot-transitions C).
  const footage = new Set(["ingest", "self-healing", "insights", "domains", "trust-egress", "trust-themes"]);
  return (
    <AbsoluteFill style={{ backgroundColor: T.stage }}>
      <TransitionSeries>
        {SCENES_H.flatMap((s, i) => {
          const seq = (
            <TransitionSeries.Sequence key={s.id} name={s.id} durationInFrames={s.dur}>
              {body[s.id]}
            </TransitionSeries.Sequence>
          );
          if (i === 0) return [seq];
          const prev = SCENES_H[i - 1].id;
          const pres = (footage.has(prev) && footage.has(s.id) ? focusSwap() : fade()) as ReturnType<typeof focusSwap>;
          return [<TransitionSeries.Transition key={`t-${s.id}`} presentation={pres} timing={linearTiming({ durationInFrames: SEAM })} />, seq];
        })}
      </TransitionSeries>
    </AbsoluteFill>
  );
};
