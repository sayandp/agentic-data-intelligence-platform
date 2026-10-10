// v2 motion kit. Grid: 120 BPM = 15 frames per beat at 30 fps; every cut,
// slam and SFX is placed with b(n). Palette: the dashboard's AURORA theme
// (DESIGN.md - the opt-in EXPERIENCE skin that exists "to look striking in a
// live demo"), so the fancy layer is still the product's own visual language.
import React, { useId } from "react";
import { AbsoluteFill, Easing, Img, interpolate, random, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { shot, src, type Box } from "../shots";

export const BEAT = 15;
export const b = (n: number) => Math.round(n * BEAT);

export const A = {
  ground: "#0A0E1C",
  teal: "#07313A",
  indigo: "#141A4D",
  violet: "#2E1A54",
  glass: "rgba(11, 16, 32, 0.72)",
  glassHi: "rgba(20, 28, 52, 0.82)",
  line: "rgba(150, 200, 255, 0.16)",
  lineStrong: "rgba(160, 210, 255, 0.34)",
  ink: "#EAF1FF",
  muted: "#B9C6E6",
  faint: "#93A3C9",
  cyan: "#5EE0F5",
  magenta: "#FF86DD",
  green: "#5EF0A8",
  red: "#FF8A9C",
  amber: "#FFC46B",
} as const;

export const SANS = "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif";
export const MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";
export const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;
export const OUT = Easing.bezier(0.16, 1, 0.3, 1); // snappy decelerate
export const INOUT = Easing.bezier(0.7, 0, 0.3, 1);

export const useV = () => {
  const { width, height } = useVideoConfig();
  return { vertical: height > width, width, height };
};

// ------------------------------------------------------------- atmosphere
/** Aurora ground: three slow drifting washes (parallax layer 0). */
export const Ground: React.FC = () => {
  const f = useCurrentFrame();
  const { width, height } = useVideoConfig();
  const d = (s: number, p: number) => Math.sin(f / s + p) * 0.08;
  return (
    <AbsoluteFill style={{ background: A.ground }}>
      <AbsoluteFill style={{ background: `radial-gradient(ellipse ${width * 0.7}px ${height * 0.6}px at ${(0.2 + d(90, 0)) * 100}% ${(0.25 + d(70, 1)) * 100}%, ${A.teal} 0%, transparent 70%)` }} />
      <AbsoluteFill style={{ background: `radial-gradient(ellipse ${width * 0.6}px ${height * 0.7}px at ${(0.85 + d(80, 2)) * 100}% ${(0.3 + d(95, 3)) * 100}%, ${A.indigo} 0%, transparent 70%)` }} />
      <AbsoluteFill style={{ background: `radial-gradient(ellipse ${width * 0.7}px ${height * 0.5}px at ${(0.55 + d(100, 4)) * 100}% ${(0.95 + d(85, 5)) * 100}%, ${A.violet} 0%, transparent 70%)` }} />
    </AbsoluteFill>
  );
};

/** Film grain (seeded per 2 frames, deterministic) + vignette, over everything. */
export const GrainVignette: React.FC = () => {
  const f = useCurrentFrame();
  const id = `grain-${useId().replace(/[^a-zA-Z0-9]/g, "")}`;
  const seed = Math.floor(f / 2) % 97;
  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      <svg width="100%" height="100%" style={{ position: "absolute", inset: 0, opacity: 0.09, mixBlendMode: "overlay" }}>
        <filter id={id}>
          <feTurbulence type="fractalNoise" baseFrequency="0.85" numOctaves={2} seed={seed} stitchTiles="stitch" />
          <feColorMatrix type="saturate" values="0" />
        </filter>
        <rect width="100%" height="100%" filter={`url(#${id})`} />
      </svg>
      <AbsoluteFill style={{ background: "radial-gradient(ellipse at center, transparent 55%, rgba(0,0,0,0.55) 100%)" }} />
    </AbsoluteFill>
  );
};

/** shot-transitions A: white flash only over a hard cut (5f each side). */
export const Flash: React.FC<{ at: number; peak?: number; color?: string }> = ({ at, peak = 0.85, color = "#fff" }) => {
  const f = useCurrentFrame();
  const o = interpolate(f, [at - 4, at, at + 6], [0, peak, 0], clamp);
  return o > 0.001 ? <AbsoluteFill style={{ background: color, opacity: o, pointerEvents: "none" }} /> : null;
};

/** Warm light leak across a seam (drawn, not a stock clip): two soft blobs
 *  sweep in and burn out over `dur` frames, screen-blended. */
export const Leak: React.FC<{ at: number; dur?: number; hue?: "warm" | "cool" }> = ({ at, dur = 16, hue = "warm" }) => {
  const f = useCurrentFrame();
  const p = interpolate(f, [at, at + dur], [0, 1], clamp);
  if (p <= 0 || p >= 1) return null;
  const o = Math.sin(Math.PI * p);
  const c1 = hue === "warm" ? "255,170,90" : "94,224,245";
  const c2 = hue === "warm" ? "255,96,140" : "255,134,221";
  return (
    <AbsoluteFill style={{ mixBlendMode: "screen", opacity: o, pointerEvents: "none" }}>
      <AbsoluteFill style={{ background: `radial-gradient(ellipse 60% 80% at ${-10 + p * 120}% 40%, rgba(${c1},0.85), transparent 60%)` }} />
      <AbsoluteFill style={{ background: `radial-gradient(ellipse 40% 60% at ${110 - p * 90}% 70%, rgba(${c2},0.6), transparent 60%)` }} />
    </AbsoluteFill>
  );
};

// ------------------------------------------------------------ typography
export type Word = { t: string; at: number; accent?: string };

/** Kinetic words: each slams in on its frame (scale 1.35->1, blur 10->0, 7f,
 *  snappy), optional accent colour. Never more than 6 words on screen. */
export const Kinetic: React.FC<{
  words: Word[];
  size?: number;
  out?: number; // frame the whole line leaves (5f)
  align?: "center" | "left";
  y?: number | string; // css top
  weight?: number;
}> = ({ words, size, out, align = "center", y = "auto", weight = 800 }) => {
  const f = useCurrentFrame();
  const { vertical } = useV();
  if (words.length > 6) throw new Error(`Kinetic: ${words.length} words > 6`);
  const s = size ?? (vertical ? 104 : 92);
  const leave = out === undefined ? 1 : interpolate(f, [out, out + 5], [1, 0], clamp);
  if (f < words[0].at || leave <= 0) return null;
  return (
    <div
      style={{
        position: "absolute",
        left: vertical ? 70 : 120,
        right: vertical ? 70 : 120,
        top: y,
        bottom: y === "auto" ? (vertical ? 300 : 130) : undefined,
        display: "flex",
        flexWrap: "wrap",
        justifyContent: align === "center" ? "center" : "flex-start",
        columnGap: s * 0.28,
        rowGap: s * 0.05,
        fontFamily: SANS,
        fontWeight: weight,
        fontSize: s,
        lineHeight: 1.04,
        letterSpacing: -s * 0.03,
        color: A.ink,
        opacity: leave,
        textShadow: "0 6px 40px rgba(0,0,0,0.65)",
      }}
    >
      {words.map((w, i) => {
        const t = interpolate(f, [w.at, w.at + 7], [0, 1], { ...clamp, easing: OUT });
        if (f < w.at) return null;
        return (
          <span key={i} style={{ display: "inline-block", color: w.accent ?? A.ink, opacity: t, scale: `${1.35 - 0.35 * t}`, filter: `blur(${(1 - t) * 10}px)`, translate: `0 ${(1 - t) * 18}px` }}>
            {w.t}
          </span>
        );
      })}
    </div>
  );
};

/** Split a phrase into words that land on consecutive beats-fractions. */
export const words = (text: string, at: number, step = 4, accents: Record<string, string> = {}): Word[] =>
  text.split(" ").map((t, i) => ({ t, at: at + i * step, accent: accents[t.replace(/[.,]/g, "")] }));

/** Count-up for an EVIDENCE.md figure (claims.ts only). */
export const CountUp: React.FC<{ to: number; at: number; dur?: number; style?: React.CSSProperties; suffix?: string }> = ({ to, at, dur = 18, style, suffix = "" }) => {
  const f = useCurrentFrame();
  const v = interpolate(f, [at, at + dur], [0, to], { ...clamp, easing: Easing.out(Easing.cubic) });
  return <span style={style}>{Math.round(v).toLocaleString("en-US")}{suffix}</span>;
};

// ---------------------------------------------------------- page camera
/** e: easing INTO this key - "out" (default, decelerate) or "in" (crash-zoom-punch: accelerate, 6f) */
export type Key = { f: number; cx: number; cy: number; z: number; e?: "in" | "out"; cut?: boolean };
const lerp = (a: number, c: number, t: number) => a + (c - a) * t;
export const camAt = (keys: Key[], f: number, ease = OUT) => {
  let a = keys[0];
  let c = keys[keys.length - 1];
  for (let i = 0; i < keys.length - 1; i++) {
    if (f >= keys[i].f && f <= keys[i + 1].f) {
      a = keys[i];
      c = keys[i + 1];
      break;
    }
  }
  if (f < keys[0].f) c = a;
  const t = a.f === c.f ? 1 : interpolate(f, [a.f, c.f], [0, 1], { ...clamp, easing: c.e === "in" ? Easing.in(Easing.cubic) : ease });
  return { cx: lerp(a.cx, c.cx, t), cy: lerp(a.cy, c.cy, t), z: lerp(a.z, c.z, t) };
};

/** A real screenshot seen through a w x h window. Punch-ins get a zoom-blur
 *  trail (two ghost layers at the previous zoom) only while the zoom is fast. */
export const Page: React.FC<{ name: string; keys: Key[]; w: number; h: number; children?: React.ReactNode; filter?: string }> = ({ name, keys, w, h, children, filter }) => {
  const f = useCurrentFrame();
  const s = shot(name);
  const now = camAt(keys, f);
  const prev = camAt(keys, f - 2);
  // a hard cut is a jump, not a fast zoom: no trail for 2 frames after one
  const justCut = keys.some((k) => k.cut && f >= k.f && f < k.f + 3);
  const speed = justCut ? 0 : Math.abs(now.z - prev.z) / Math.max(0.01, now.z);
  const layer = (c: { cx: number; cy: number; z: number }, o: number, k: string) => (
    <div key={k} style={{ position: "absolute", width: s.w, height: s.h, transformOrigin: "0 0", transform: `translate(${w / 2 - c.cx * c.z}px, ${h / 2 - c.cy * c.z}px) scale(${c.z})`, opacity: o }}>
      <Img src={staticFile(src(name))} style={{ position: "absolute", width: s.w, height: s.h }} />
      {k === "main" ? children : null}
    </div>
  );
  return (
    <div style={{ position: "absolute", width: w, height: h, overflow: "hidden", background: "#F7F8FA", filter }}>
      {layer(now, 1, "main")}
      {/* zoom-blur trail: the two previous camera positions, translucent, ON
          TOP of the sharp frame - visible only while the zoom is fast */}
      {speed > 0.02 && layer(camAt(keys, f - 1), 0.45, "g1")}
      {speed > 0.02 && layer(prev, 0.3, "g2")}
    </div>
  );
};

/** 2.5D browser window around real footage: perspective tilt, slow orbit,
 *  soft shadow. Chrome is generic (no third-party marks). */
export const Device: React.FC<{
  w: number;
  h: number;
  url: string;
  orbit?: [number, number]; // rotateY from -> to over the shot
  tilt?: number; // rotateX
  dur: number;
  children: React.ReactNode;
  enter?: number; // frames of rise-in
}> = ({ w, h, url, orbit = [-10, -4], tilt = 6, dur, children, enter = 9 }) => {
  const f = useCurrentFrame();
  const ry = interpolate(f, [0, dur], orbit, clamp);
  const t = interpolate(f, [0, enter], [0, 1], { ...clamp, easing: OUT });
  const bar = 46;
  return (
    <AbsoluteFill style={{ perspective: 2200, alignItems: "center", justifyContent: "center" }}>
      <div
        style={{
          width: w,
          height: h + bar,
          transform: `translateY(${(1 - t) * 80}px) rotateX(${tilt}deg) rotateY(${ry}deg) scale(${0.92 + 0.08 * t})`,
          opacity: t,
          borderRadius: 14,
          overflow: "hidden",
          border: `1px solid ${A.lineStrong}`,
          boxShadow: "0 60px 140px rgba(0,0,0,0.6), 0 0 0 1px rgba(255,255,255,0.04), 0 0 80px rgba(94,224,245,0.10)",
          background: "#0B1020",
        }}
      >
        <div style={{ height: bar, display: "flex", alignItems: "center", gap: 10, padding: "0 18px", background: "rgba(14,20,40,0.96)", borderBottom: `1px solid ${A.line}` }}>
          {["#FF8A9C", "#FFC46B", "#5EF0A8"].map((c) => (
            <span key={c} style={{ width: 13, height: 13, borderRadius: 7, background: c, opacity: 0.8 }} />
          ))}
          <div style={{ marginLeft: 18, flex: 1, height: 28, borderRadius: 8, background: "rgba(255,255,255,0.06)", color: A.muted, fontFamily: MONO, fontSize: 17, display: "flex", alignItems: "center", padding: "0 14px" }}>{url}</div>
        </div>
        <div style={{ position: "relative", width: w, height: h }}>{children}</div>
      </div>
    </AbsoluteFill>
  );
};

// --------------------------------------------------------------- effects
/** RGB-split glitch over a page-space box (inside <Page> children): two
 *  channel-isolated copies of the real pixels jitter apart, slices tear,
 *  red wash flickers. Deterministic (seeded by frame). */
export const Glitch: React.FC<{ name: string; b: Box; from: number; to: number; amount?: number }> = ({ name, b: bx, from, to, amount = 1 }) => {
  const f = useCurrentFrame();
  const id = useId().replace(/[^a-zA-Z0-9]/g, "");
  if (f < from || f > to) return null;
  const s = shot(name);
  const r = (k: string) => random(`${id}-${k}-${Math.floor(f / 2)}`);
  const dx = (4 + r("dx") * 8) * amount;
  const on = r("on") > 0.25;
  const img = (filterId: string, x: number, op: number) => (
    <div style={{ position: "absolute", inset: 0, transform: `translateX(${x}px)`, mixBlendMode: "screen", opacity: op, filter: `url(#${filterId})` }}>
      <Img src={staticFile(src(name))} style={{ position: "absolute", left: -bx.x, top: -bx.y, width: s.w, height: s.h }} />
    </div>
  );
  return (
    <div style={{ position: "absolute", left: bx.x, top: bx.y, width: bx.w, height: bx.h, overflow: "hidden" }}>
      <svg width={0} height={0} style={{ position: "absolute" }}>
        <filter id={`r${id}`}><feColorMatrix type="matrix" values="1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0" /></filter>
        <filter id={`c${id}`}><feColorMatrix type="matrix" values="0 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 1 0" /></filter>
      </svg>
      <div style={{ position: "absolute", inset: 0, background: "#000" }} />
      {img(`r${id}`, dx, 1)}
      {img(`c${id}`, -dx, 1)}
      {[0, 1, 2].map((k) => (
        <div key={k} style={{ position: "absolute", left: 0, right: 0, top: `${r(`y${k}`) * 90}%`, height: `${4 + r(`h${k}`) * 10}%`, background: "rgba(255,40,80,0.35)", transform: `translateX(${(r(`x${k}`) - 0.5) * 30 * amount}px)` }} />
      ))}
      <div style={{ position: "absolute", inset: 0, background: "rgba(255,40,80,0.18)", opacity: on ? 1 : 0.3 }} />
    </div>
  );
};

/** Red -> green wipe across a box with a check mark that draws itself. */
export const HealWipe: React.FC<{ b: Box; at: number; dur?: number; check?: boolean; radius?: number }> = ({ b: bx, at, dur = 9, check = true, radius = 4 }) => {
  const f = useCurrentFrame();
  const p = interpolate(f, [at, at + dur], [0, 1], { ...clamp, easing: INOUT });
  const edge = p >= 1 ? 0 : 0.95;
  const fade = interpolate(f, [at + dur + 18, at + dur + 34], [1, 0.35], clamp);
  if (f < at - 6) return null;
  const c = interpolate(f, [at + dur - 2, at + dur + 8], [0, 1], { ...clamp, easing: OUT });
  const s = Math.min(bx.h * 0.9, 40);
  return (
    <div style={{ position: "absolute", left: bx.x, top: bx.y, width: bx.w, height: bx.h, borderRadius: radius, overflow: "visible", pointerEvents: "none" }}>
      <div style={{ position: "absolute", inset: 0, borderRadius: radius, background: `linear-gradient(90deg, rgba(94,240,168,0.40) 0%, rgba(94,240,168,0.40) ${p * 100 - 6}%, rgba(255,255,255,${edge}) ${p * 100}%, rgba(255,90,120,0.32) ${p * 100 + 6}%, rgba(255,90,120,0.32) 100%)`, opacity: fade, mixBlendMode: "multiply" }} />
      <div style={{ position: "absolute", inset: -2, borderRadius: radius + 2, border: `2px solid ${p >= 1 ? "#1FAE6A" : "rgba(220,40,70,0.8)"}`, opacity: fade }} />
      {check && c > 0 && (
        <svg width={s} height={s} viewBox="0 0 40 40" style={{ position: "absolute", right: -s * 1.2, top: (bx.h - s) / 2 }}>
          <circle cx={20} cy={20} r={19} fill="#1FAE6A" opacity={c} />
          <path d="M11 21 L18 28 L30 13" fill="none" stroke="#fff" strokeWidth={4} strokeLinecap="round" strokeLinejoin="round" strokeDasharray={30} strokeDashoffset={30 * (1 - c)} />
        </svg>
      )}
    </div>
  );
};

/** Freeze-and-circle marker (speed-ramp-freeze card: 8f stroke, rough). */
export const Circle: React.FC<{ b: Box; at: number; out?: number; color: string; stroke?: number; pad?: number }> = ({ b: bx, at, out, color, stroke = 5, pad = 14 }) => {
  const f = useCurrentFrame();
  const id = `c-${useId().replace(/[^a-zA-Z0-9]/g, "")}`;
  const draw = interpolate(f, [at, at + 8], [0, 1], clamp);
  const fade = out === undefined ? 1 : interpolate(f, [out, out + 8], [1, 0], clamp);
  if (draw <= 0 || fade <= 0) return null;
  const rx = bx.w / 2 + pad * 2;
  const ry = bx.h / 2 + pad;
  const m = 30;
  const C = Math.PI * (3 * (rx + ry) - Math.sqrt((3 * rx + ry) * (rx + 3 * ry)));
  return (
    <svg style={{ position: "absolute", left: bx.x + bx.w / 2 - rx - m, top: bx.y + bx.h / 2 - ry - m, overflow: "visible", opacity: fade }} width={2 * (rx + m)} height={2 * (ry + m)}>
      <filter id={id}>
        <feTurbulence type="fractalNoise" baseFrequency="0.02" numOctaves={2} seed={7} result="n" />
        <feDisplacementMap in="SourceGraphic" in2="n" scale={7} />
      </filter>
      <ellipse cx={rx + m} cy={ry + m} rx={rx} ry={ry} fill="none" stroke={color} strokeWidth={stroke} strokeLinecap="round" strokeDasharray={C} strokeDashoffset={C * (1 - draw)} transform={`rotate(-3 ${rx + m} ${ry + m})`} filter={`url(#${id})`} />
    </svg>
  );
};

/** Pointer with click ripple (page- or screen-space). */
export const Pointer: React.FC<{ from: { x: number; y: number }; to: { x: number; y: number }; move: [number, number]; click?: number; scale?: number }> = ({ from, to, move, click, scale = 1 }) => {
  const f = useCurrentFrame();
  const t = interpolate(f, move, [0, 1], { ...clamp, easing: Easing.bezier(0.45, 0, 0.15, 1) });
  const x = from.x + (to.x - from.x) * t;
  const y = from.y + (to.y - from.y) * t;
  const press = click === undefined ? 0 : interpolate(f, [click, click + 3, click + 8], [0, 1, 0], clamp);
  return (
    <div style={{ position: "absolute", left: x, top: y, pointerEvents: "none" }}>
      {click !== undefined &&
        [0, 6].map((d) => {
          const p = interpolate(f, [click + d, click + d + 18], [0, 1], clamp);
          return f >= click + d && p < 1 ? (
            <div key={d} style={{ position: "absolute", left: -30 * scale, top: -30 * scale, width: 60 * scale, height: 60 * scale, borderRadius: "50%", border: `${3 * scale}px solid ${A.cyan}`, transform: `scale(${0.3 + p * 1.4})`, opacity: 1 - p }} />
          ) : null;
        })}
      <svg width={26 * scale} height={34 * scale} viewBox="0 0 22 30" style={{ transform: `scale(${1 - press * 0.15})`, transformOrigin: "0 0", filter: "drop-shadow(0 4px 8px rgba(0,0,0,0.45))" }}>
        <path d="M1 1 L1 24 L7 18.5 L11 28 L15 26.3 L11 17 L19 17 Z" fill="#0A0E1C" stroke="#FFFFFF" strokeWidth={1.6} strokeLinejoin="round" />
      </svg>
    </div>
  );
};

/** VHS rewind: scanlines, tracking bands, chroma fringe and an OSD - wraps
 *  content that the caller is already playing backwards. */
export const VHS: React.FC<{ children: React.ReactNode; strength?: number }> = ({ children, strength = 1 }) => {
  const f = useCurrentFrame();
  const { width } = useVideoConfig();
  const id = useId().replace(/[^a-zA-Z0-9]/g, "");
  const band = (f * 37) % 120;
  const jitter = (random(`vj${Math.floor(f / 2)}`) - 0.5) * 14 * strength;
  return (
    <AbsoluteFill>
      <svg width={0} height={0} style={{ position: "absolute" }}>
        <filter id={`vhs${id}`}>
          <feColorMatrix in="SourceGraphic" type="matrix" values="1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0" result="r" />
          <feOffset in="r" dx={6 * strength} result="ro" />
          <feColorMatrix in="SourceGraphic" type="matrix" values="0 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 1 0" result="gb" />
          <feBlend in="ro" in2="gb" mode="screen" />
        </filter>
      </svg>
      <AbsoluteFill style={{ filter: `url(#vhs${id}) saturate(1.3) contrast(1.1)`, transform: `translateX(${jitter}px)` }}>{children}</AbsoluteFill>
      <AbsoluteFill style={{ background: "repeating-linear-gradient(0deg, rgba(0,0,0,0.22) 0px, rgba(0,0,0,0.22) 2px, transparent 2px, transparent 5px)", pointerEvents: "none" }} />
      <div style={{ position: "absolute", left: 0, right: 0, top: `${band - 10}%`, height: "7%", background: "linear-gradient(180deg, transparent, rgba(255,255,255,0.18), transparent)", filter: "blur(2px)" }} />
      <div style={{ position: "absolute", left: width > 1200 ? 70 : 50, top: 60, fontFamily: MONO, fontSize: 52, color: "#fff", textShadow: "3px 0 #ff3b5c, -3px 0 #3bd5ff", letterSpacing: 4, opacity: Math.floor(f / 6) % 2 ? 1 : 0.65 }}>◀◀ REWIND</div>
    </AbsoluteFill>
  );
};

/** Pixelate-to-token: a cell's real value mosaics, then resolves into the
 *  redactor's real token. */
export const Mosaic: React.FC<{ value: string; token: string; at: number; style?: React.CSSProperties }> = ({ value, token, at, style }) => {
  const f = useCurrentFrame();
  const p = interpolate(f, [at, at + 14], [0, 1], clamp);
  const showToken = p >= 0.6;
  const px = p < 0.6 ? interpolate(p, [0, 0.6], [0, 9]) : interpolate(p, [0.6, 1], [9, 0]);
  return (
    <span style={{ position: "relative", display: "inline-block", ...style }}>
      <span style={{ filter: px > 0.2 ? `blur(${px * 0.7}px) contrast(${1 + px * 0.2})` : undefined, color: showToken ? A.magenta : undefined, fontWeight: showToken ? 700 : undefined }}>{showToken ? token : value}</span>
      {px > 0.5 && (
        <span style={{ position: "absolute", inset: "-2px -4px", background: `repeating-conic-gradient(rgba(255,134,221,0.55) 0 25%, rgba(94,224,245,0.45) 0 50%) 0 0 / ${px * 3}px ${px * 3}px`, opacity: Math.min(1, px / 9), borderRadius: 3 }} />
      )}
    </span>
  );
};

/** Glass panel (Aurora surface). */
export const Glass: React.FC<{ style?: React.CSSProperties; children?: React.ReactNode; glow?: string }> = ({ style, children, glow }) => (
  <div
    style={{
      background: A.glass,
      border: `1px solid ${glow ?? A.line}`,
      borderRadius: 16,
      boxShadow: glow ? `0 0 40px ${glow}55, inset 0 0 30px ${glow}22` : "0 30px 80px rgba(0,0,0,0.45)",
      backdropFilter: "blur(14px)",
      ...style,
    }}
  >
    {children}
  </div>
);

/** Spotlight: dims everything outside a page-space box so the one element
 *  the caption talks about is what the eye lands on (inside <Page>). */
export const Spotlight: React.FC<{ b: Box; at: number; out?: number; dim?: number; pageW?: number; pageH?: number }> = ({ b: bx, at, out, dim = 0.55, pageW = 4000, pageH = 6000 }) => {
  const f = useCurrentFrame();
  if (out !== undefined && f >= out) return null; // ends on its shot's cut
  const o = interpolate(f, [at, at + 6], [0, dim], clamp);
  if (o <= 0) return null;
  const pad = 10;
  const x0 = bx.x - pad, y0 = bx.y - pad, x1 = bx.x + bx.w + pad, y1 = bx.y + bx.h + pad;
  const r = (l: number, t: number, w: number, h: number, k: string) => <div key={k} style={{ position: "absolute", left: l, top: t, width: w, height: h, background: "#0A0E1C", opacity: o }} />;
  return (
    <>
      {r(-2000, -2000, pageW + 4000, y0 + 2000, "t")}
      {r(-2000, y1, pageW + 4000, pageH, "b")}
      {r(-2000, y0, x0 + 2000, y1 - y0, "l")}
      {r(x1, y0, pageW, y1 - y0, "r")}
      <div style={{ position: "absolute", left: x0, top: y0, width: x1 - x0, height: y1 - y0, borderRadius: 8, boxShadow: `0 0 0 2px ${A.cyan}, 0 0 30px ${A.cyan}88`, opacity: o / dim }} />
    </>
  );
};

/** odometer-digit-roll: every digit is a reel that spins and lands on its
 *  value, left to right, with a slight overshoot (one value, no count-up
 *  through invented intermediates - a reel shows all ten digits anyway). */
export const Odometer: React.FC<{ value: string; at: number; size: number; color?: string }> = ({ value, at, size, color = A.ink }) => {
  const f = useCurrentFrame();
  let di = 0;
  return (
    <span style={{ display: "inline-flex", fontFamily: MONO, fontSize: size, lineHeight: 1, color, letterSpacing: -size * 0.04 }}>
      {[...value].map((ch, i) => {
        if (!/[0-9]/.test(ch)) return <span key={i}>{ch}</span>;
        const d = Number(ch);
        const start = at + di * 4;
        di++;
        const p = interpolate(f, [start, start + 16], [0, 1], { ...clamp, easing: Easing.bezier(0.2, 0.9, 0.3, 1.08) });
        const pos = (10 + d) * p; // one full turn, then land on d
        const blur = interpolate(f, [start, start + 8, start + 16], [0, 6, 0], clamp);
        return (
          <span key={i} style={{ display: "inline-block", height: size, overflow: "hidden", position: "relative", width: "0.62em" }}>
            <span style={{ position: "absolute", left: 0, top: -pos * size, filter: `blur(${blur}px)`, display: "flex", flexDirection: "column" }}>
              {Array.from({ length: 21 }).map((_, k) => (
                <span key={k} style={{ height: size, lineHeight: `${size}px` }}>{k % 10}</span>
              ))}
            </span>
          </span>
        );
      })}
    </span>
  );
};

/** theme-switch-moves A (theme-sweep): a diagonal edge sweeps across and the
 *  new skin is revealed behind it, in place. */
export const Sweep: React.FC<{ at: number; dur?: number; children: React.ReactNode }> = ({ at, dur = 10, children }) => {
  const f = useCurrentFrame();
  const p = interpolate(f, [at, at + dur], [0, 1], { ...clamp, easing: INOUT });
  if (p <= 0) return null;
  const x = -30 + p * 160; // percent; the edge leans 30%
  return (
    <AbsoluteFill style={{ clipPath: `polygon(0 0, ${x}% 0, ${x - 30}% 100%, 0 100%)` }}>
      {children}
      {p < 1 && <div style={{ position: "absolute", top: 0, bottom: 0, left: `${x - 15}%`, width: 6, background: A.cyan, boxShadow: `0 0 30px ${A.cyan}`, transform: "skewX(-17deg)" }} />}
    </AbsoluteFill>
  );
};
