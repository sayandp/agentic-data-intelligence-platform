import { Composition } from "remotion";
import { Horizontal, H_DURATION } from "./Horizontal";
import { Vertical, V_DURATION } from "./Vertical";
import { FPS } from "./theme";

// `byline` is a prop so the CTA name can change without touching code:
//   npx remotion render Horizontal out/x.mp4 --props=props.json
const defaultProps = { byline: "by @sayandp" };

export const RemotionRoot: React.FC = () => (
  <>
    <Composition id="Horizontal" component={Horizontal} width={1920} height={1080} fps={FPS} durationInFrames={H_DURATION} defaultProps={defaultProps} />
    <Composition id="Vertical" component={Vertical} width={1080} height={1920} fps={FPS} durationInFrames={V_DURATION} defaultProps={defaultProps} />
  </>
);
