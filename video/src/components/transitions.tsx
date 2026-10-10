import { useId } from "react";
import { AbsoluteFill, Easing } from "remotion";
import type { TransitionPresentation, TransitionPresentationComponentProps } from "@remotion/transitions";

// shotcraft shot-transitions, seams C and E.

type None = Record<string, unknown>;

// C - focus handoff: the outgoing shot slides out of the focal plane
// (blur 0->8px, drifts 60px) while the incoming one racks in from 8px,
// starting 2f later - a same-frame start reads as the whole screen going soft.
const FocusSwap: React.FC<TransitionPresentationComponentProps<None>> = ({ children, presentationDirection, presentationProgress, presentationDurationInFrames }) => {
  const D = presentationDurationInFrames;
  if (presentationDirection === "exiting") {
    const p = Easing.bezier(0.45, 0, 0.3, 1)(presentationProgress);
    return <AbsoluteFill style={{ opacity: 1 - p, filter: `blur(${p * 8}px)`, transform: `translateX(${-p * 60}px)` }}>{children}</AbsoluteFill>;
  }
  const lag = 2 / D;
  const raw = Math.min(1, Math.max(0, (presentationProgress - lag) / (1 - lag)));
  const p = Easing.bezier(0.3, 0, 0.2, 1)(raw);
  return <AbsoluteFill style={{ opacity: p, filter: `blur(${(1 - p) * 8}px)`, transform: `translateX(${(1 - p) * 50}px)` }}>{children}</AbsoluteFill>;
};

export const focusSwap = (): TransitionPresentation<None> => ({ component: FocusSwap, props: {} });

// E - whip pan: both shots sit on one horizontal line; the camera throws
// ~1.5 screens on a steep ease-in-out, smeared by a HORIZONTAL-only blur
// (an SVG stdDeviation "x 0") proportional to the camera's speed.
const Whip: React.FC<TransitionPresentationComponentProps<None>> = ({ children, presentationDirection, presentationProgress }) => {
  const id = `whip-${useId().replace(/[^a-zA-Z0-9]/g, "")}`;
  const e = Easing.bezier(0.7, 0, 0.3, 1)(presentationProgress);
  const speed = Math.sin(Math.PI * presentationProgress); // 0 at both ends
  const travel = 1.5;
  const x = presentationDirection === "exiting" ? -e * travel * 100 : (1 - e) * travel * 100;
  return (
    <AbsoluteFill style={{ transform: `translateX(${x}%)`, filter: speed > 0.02 ? `url(#${id})` : undefined }}>
      <svg width={0} height={0} style={{ position: "absolute" }}>
        <filter id={id} x="-20%" y="0" width="140%" height="100%">
          <feGaussianBlur stdDeviation={`${speed * 46} 0`} />
        </filter>
      </svg>
      {children}
    </AbsoluteFill>
  );
};

export const whip = (): TransitionPresentation<None> => ({ component: Whip, props: {} });
