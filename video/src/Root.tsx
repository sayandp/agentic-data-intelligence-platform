import { Composition } from "remotion";
import { Horizontal, H_DURATION } from "./Horizontal";
import { Vertical, V_DURATION } from "./Vertical";
import { FPS } from "./theme";
import { durationOf, Horizontal2, H2_SCENES, Vertical2, V2_SCENES } from "./v2/Main";

// `byline` is a prop so the CTA name can change without touching code:
//   npx remotion render Horizontal out/x.mp4 --props=props.json
const defaultProps = { byline: "by @sayandp" };
const v2Props = { byline: "Sayand", gain: 1 };

export const RemotionRoot: React.FC = () => (
  <>
    <Composition id="Horizontal" component={Horizontal} width={1920} height={1080} fps={FPS} durationInFrames={H_DURATION} defaultProps={defaultProps} />
    <Composition id="Vertical" component={Vertical} width={1080} height={1920} fps={FPS} durationInFrames={V_DURATION} defaultProps={defaultProps} />
    {/* v2 re-cut: 120 BPM grid, music + SFX. gain = loudness trim (see scripts/loudness.py). */}
    <Composition id="Horizontal2" component={Horizontal2} width={1920} height={1080} fps={FPS} durationInFrames={durationOf(H2_SCENES(""))} defaultProps={v2Props} />
    <Composition id="Vertical2" component={Vertical2} width={1080} height={1920} fps={FPS} durationInFrames={durationOf(V2_SCENES(""))} defaultProps={v2Props} />
  </>
);
