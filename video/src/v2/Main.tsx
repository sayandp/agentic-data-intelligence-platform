import React from "react";
import { AbsoluteFill, Easing, interpolate, Sequence, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { Audio } from "@remotion/media";
import beat from "../../analysis/beat_house-vibez.json";
import { A, b, Flash, GrainVignette, Leak } from "./kit";
import { scenesH, scenesV, type Scene } from "./scenes";

// The beat grid is the measured one (analysis/beat_house-vibez.json:
// 122.00 BPM, least-squares fit, residual within 16 ms). Playing the track
// at 120/122 makes every beat land on exactly 15 frames, so scene cuts on
// b(n) ARE the music's beats. `musicBeat` = the source beat at frame 0.
const RATE = 120 / beat.bpm;
const srcSecondOfBeat = (k: number) => beat.t0 + k * beat.T;

type Seam = "flash" | "leak" | "whip" | "cut";

/** shot-transitions E (whip-pan, brake): the outgoing scene throws ~1.5
 *  screens left in its last 6f with a horizontal-only smear; the incoming one
 *  arrives from the right with a fast start and an ease-out tail. */
const Whip: React.FC<{ len: number; enter: boolean; exit: boolean; children: React.ReactNode }> = ({ len, enter, exit, children }) => {
  const f = useCurrentFrame();
  const { width } = useVideoConfig();
  let x = 0;
  let blur = 0;
  if (enter) {
    const t = interpolate(f, [0, 14], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.out(Easing.poly(5)) });
    x += width * 0.9 * (1 - t);
    blur += interpolate(f, [0, 8], [40, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  }
  if (exit) {
    const t = interpolate(f, [len - 6, len], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.in(Easing.cubic) });
    x -= width * 1.5 * t;
    blur += t * 44;
  }
  const id = `whip${Math.round(len)}${enter ? "i" : ""}${exit ? "o" : ""}`;
  return (
    <AbsoluteFill style={{ transform: x ? `translateX(${x}px)` : undefined, filter: blur > 0.5 ? `url(#${id})` : undefined }}>
      {blur > 0.5 && (
        <svg width={0} height={0} style={{ position: "absolute" }}>
          <filter id={id} x="-20%" width="140%">
            <feGaussianBlur stdDeviation={`${blur} 0`} />
          </filter>
        </svg>
      )}
      {children}
    </AbsoluteFill>
  );
};

/** audio=false: silent (stills); music=false: SFX only, same timeline (shotcraft A8). */
export type V2Props = { byline: string; gain: number; audio?: boolean; music?: boolean };

const Cut: React.FC<{ scenes: Scene[]; musicBeat: number; gain: number; audio: boolean; music: boolean; seams: Record<string, Seam> }> = ({ scenes, musicBeat, gain, audio, music, seams }) => {
  const { durationInFrames } = useVideoConfig();
  let t = 0;
  const placed = scenes.map((s) => {
    const from = t;
    t += b(s.beats);
    return { ...s, from, len: b(s.beats) };
  });
  return (
    <AbsoluteFill style={{ background: A.ground }}>
      {placed.map((s, i) => (
        <Sequence key={s.id} name={s.id} from={s.from} durationInFrames={s.len}>
          <Whip len={s.len} enter={seams[s.id] === "whip"} exit={seams[placed[i + 1]?.id] === "whip"}>
            <s.C />
          </Whip>
        </Sequence>
      ))}
      {/* seams on the downbeat of each new scene */}
      {placed.slice(1).map((s) =>
        seams[s.id] === "flash" ? <Flash key={`f-${s.id}`} at={s.from} peak={0.7} /> : seams[s.id] === "leak" ? <Leak key={`l-${s.id}`} at={s.from - 8} dur={18} /> : null,
      )}
      <GrainVignette />
      {/* music under SFX, fade in 10f, fade out over the last 2 beats */}
      {audio && music && <Audio
        src={staticFile("audio/house-vibez.mp3")}
        trimBefore={srcSecondOfBeat(musicBeat) * 30}
        playbackRate={RATE}
        volume={(f) => gain * 0.5 * interpolate(f, [0, 10, durationInFrames - b(2), durationInFrames - 1], [0, 1, 1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" })}
      />}
      {audio && placed.flatMap((s) =>
        s.sfx.map((x, i) => (
          <Sequence key={`${s.id}-sfx-${i}`} name={`sfx ${x.src}`} from={s.from + x.at} durationInFrames={x.dur} layout="none">
            <Audio src={staticFile(`audio/${x.src}.mp3`)} volume={x.vol * gain} />
          </Sequence>
        )),
      )}
    </AbsoluteFill>
  );
};

export const H2_SCENES = (byline: string) => scenesH(byline);
export const V2_SCENES = (byline: string) => scenesV(byline);
export const durationOf = (s: Scene[]) => s.reduce((a, x) => a + b(x.beats), 0);

export const Horizontal2: React.FC<V2Props> = ({ byline, gain, audio = true, music = true }) => (
  <Cut scenes={H2_SCENES(byline)} musicBeat={16} gain={gain} audio={audio} music={music} seams={{ problem: "cut", how: "leak", hero: "flash", human: "cut", ask: "leak", report: "whip", trust: "leak", domains: "flash", end: "leak" }} />
);
export const Vertical2: React.FC<V2Props> = ({ byline, gain, audio = true, music = true }) => (
  <Cut scenes={V2_SCENES(byline)} musicBeat={56} gain={gain} audio={audio} music={music} seams={{ hero: "flash", ask: "leak", stats: "flash", end: "leak" }} />
);
