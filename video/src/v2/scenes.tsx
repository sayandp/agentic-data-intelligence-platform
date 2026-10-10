import React from "react";
import { AbsoluteFill, Easing, Img, interpolate, Sequence, staticFile, useCurrentFrame } from "remotion";
import { evolvePath, getLength, getPointAtLength } from "@remotion/paths";
import { CLAIMS } from "../claims";
import { DATA, Grid, glitchStyle, type Col } from "./Grid";
import { A, b, Circle, clamp, Device, Glass, Glitch, Ground, HealWipe, INOUT, Kinetic, Leak, MONO, Mosaic, Odometer, OUT, Page, Pointer, SANS, Spotlight, Sweep, useV, VHS, words, type Key } from "./kit";

// Every scene: its length in beats, its component (local frames) and its
// SFX cues (local frames); the composition offsets both. Footage
// coordinates are page-space CSS px measured on the captures.
// Shots inside a scene change by HARD CUT on a beat (two camera keys one
// frame apart) or by a 6f ease-in crash zoom (crash-zoom-punch card).

export type Sfx = { at: number; src: string; vol: number; dur?: number };
export type Scene = { id: string; beats: number; C: React.FC; sfx: Sfx[] };

const useDev = () => {
  const { vertical } = useV();
  return vertical ? { w: 980, h: 760, lift: 190 } : { w: 1320, h: 650, lift: 95 };
};

/** A hard cut inside one camera: hold the previous key until f-1, then jump. */
const cutTo = (f: number, c: Omit<Key, "f">): Key[] => [{ f: f - 1, cx: NaN, cy: NaN, z: NaN }, { f, ...c, cut: true }];
/** Flatten keys; a NaN key means "hold the previous key's framing". */
const keys = (...ks: (Key | Key[])[]): Key[] => {
  const flat = ks.flat();
  for (let i = 0; i < flat.length; i++) {
    if (Number.isNaN(flat[i].cx)) flat[i] = { ...flat[i - 1], f: flat[i].f, e: undefined };
  }
  return flat;
};
/** crash-zoom-punch: after a wide hold, a 6f accelerating push. */
const crash = (at: number, c: Omit<Key, "f">): Key => ({ f: at + 6, ...c, e: "in" });

const Stage: React.FC<{ url: string; dur: number; orbit?: [number, number]; tilt?: number; children: React.ReactNode }> = ({ url, dur, orbit, tilt, children }) => {
  const d = useDev();
  return (
    <AbsoluteFill style={{ top: -d.lift, bottom: d.lift }}>
      <Device w={d.w} h={d.h} url={url} dur={dur} orbit={orbit} tilt={tilt}>
        {children}
      </Device>
    </AbsoluteFill>
  );
};

const PV: React.FC<{ name: string; keys: Key[]; filter?: string; children?: React.ReactNode }> = ({ name, keys: k, filter, children }) => {
  const d = useDev();
  return (
    <Page name={name} keys={k} w={d.w} h={d.h} filter={filter}>
      {children}
    </Page>
  );
};

// Columns of the seeded orders file.
const colsH = (cityLabel: string): Col[] => [
  { key: "order_id", label: "order_id", w: 230 },
  { key: "city", label: cityLabel, w: 200 },
  { key: "category", label: "category", w: 230 },
  { key: "price", label: "price", w: 170 },
  { key: "customer_email", label: "customer_email", w: 400 },
];
const colsV = (cityLabel: string): Col[] => [
  { key: "order_id", label: "order_id", w: 330 },
  { key: "city", label: cityLabel, w: 280 },
  { key: "price", label: "price", w: 260 },
];
const renamedRows = DATA.renamed.map((r) => ({ ...r, city: r.town }));

// ------------------------------------------------------------------ HOOK
const Hook: React.FC = () => {
  const f = useCurrentFrame();
  const { vertical } = useV();
  const cols = vertical ? colsV("town") : colsH("town");
  const healAt = b(5);
  const healed = (ri: number) => f >= healAt + ri * 2 + 3;
  const push = interpolate(f, [b(6), b(6) + 10], [0, 1], { ...clamp, easing: OUT });
  return (
    <AbsoluteFill>
      <Ground />
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", scale: `${1 - push * 0.12}`, filter: `blur(${push * 7}px)`, opacity: 1 - push * 0.55 }}>
        <Grid
          rows={renamedRows}
          cols={cols}
          font={vertical ? 40 : 31}
          rowH={vertical ? 88 : 68}
          label="orders.csv · run #647, as ingested"
          head={(k) =>
            k === "city"
              ? f >= healAt + 4
                ? { text: "city", color: A.green, bg: "rgba(94,240,168,0.16)" }
                : { text: "town", color: A.red, bg: "rgba(255,90,120,0.22)", glitch: f < b(4) ? 1.4 : 0.5 }
              : {}
          }
          cell={(ri, k) => (k === "city" ? (healed(ri) ? { bg: "rgba(94,240,168,0.12)", color: A.green } : { bg: "rgba(255,90,120,0.14)", glitch: f < b(4) && (ri + Math.floor(f / 3)) % 3 === 0 ? 1 : 0 }) : {})}
        />
        <div style={{ position: "absolute", top: vertical ? 470 : 130, right: vertical ? 80 : 210, opacity: interpolate(f, [b(4), b(4) + 5], [0, 1], clamp), scale: `${interpolate(f, [b(4), b(4) + 7], [1.3, 1], { ...clamp, easing: OUT })}` }}>
          <Glass glow={A.magenta} style={{ padding: "16px 22px", fontFamily: MONO, fontSize: vertical ? 34 : 30, color: A.ink }}>
            <span style={{ color: A.magenta }}>● diagnosis</span>&nbsp; rename_column · 0.95
          </Glass>
        </div>
      </AbsoluteFill>
      <Kinetic words={words("Broken data arrives.", 4, 4, { Broken: A.red })} out={b(4)} y={vertical ? 240 : 50} size={vertical ? 84 : 70} />
      <Kinetic words={words("Data that fixes itself.", b(6), 3, { fixes: A.cyan, itself: A.cyan })} size={vertical ? 132 : 128} y={vertical ? 760 : 400} />
    </AbsoluteFill>
  );
};

// --------------------------------------------------------------- PROBLEM
const Problem: React.FC = () => {
  const f = useCurrentFrame();
  const { vertical } = useV();
  return (
    <AbsoluteFill>
      <Ground />
      {f < b(4) ? (
        <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", top: vertical ? -200 : -90 }}>
          <Grid
            rows={DATA.dollar}
            cols={vertical ? colsV("city") : colsH("city")}
            font={vertical ? 36 : 28}
            rowH={vertical ? 80 : 62}
            label="orders.csv · run #648, as ingested"
            head={(k) => (k === "price" ? { color: A.red, glitch: 0.8 } : {})}
            cell={(ri, k) => (k === "price" ? { color: A.red, bg: "rgba(255,90,120,0.12)", glitch: (ri + Math.floor(f / 4)) % 2 ? 1 : 0.3 } : {})}
          />
        </AbsoluteFill>
      ) : null}
      <Sequence from={b(4)}>
        <Stage url="localhost:5173/approvals" dur={b(4)}>
          <PV name="approvals" keys={keys({ f: 0, cx: 760, cy: 600, z: 1.0 }, { f: 8, cx: 760, cy: 600, z: 1.0 }, crash(8, { cx: 560, cy: 536, z: vertical ? 2.4 : 2.3 }))}>
            <Glitch name="approvals" b={{ x: 326, y: 526, w: 540, h: 22 }} from={16} to={b(4)} />
            <Spotlight b={{ x: 326, y: 526, w: 540, h: 22 }} at={16} />
          </PV>
        </Stage>
      </Sequence>
      <Kinetic words={words("Pipelines break silently.", 2, 5, { silently: A.red })} out={b(8) - 5} />
    </AbsoluteFill>
  );
};

// ------------------------------------------------------------------- HOW
type Node = { name: string; plane: number; llm?: string; dashed?: boolean };
const NODES: Node[] = [
  { name: "Connectors", plane: 0 },
  { name: "file · SQL · API", plane: 0, dashed: true },
  { name: "Data-Quality", plane: 1 },
  { name: "Diagnosis", plane: 1, llm: "LLM" },
  { name: "Gate · rules", plane: 1, dashed: true },
  { name: "Exploration", plane: 2 },
  { name: "Analytics", plane: 2 },
  { name: "Query", plane: 2, llm: "LLM" },
  { name: "Modeling", plane: 2, llm: "intent" },
  { name: "Narrative", plane: 3, llm: "LLM" },
  { name: "Marketing", plane: 3 },
  { name: "Agriculture", plane: 3, dashed: true },
];
const PLANES = ["Ingestion", "Reliability", "Intelligence", "Delivery"];
// The nine agents of EVIDENCE.md §1.1, in pipeline order.
const FLOW = ["Connectors", "Data-Quality", "Diagnosis", "Exploration", "Analytics", "Query", "Modeling", "Narrative", "Marketing"];

const How: React.FC = () => {
  const f = useCurrentFrame();
  const { vertical, width, height } = useV();
  const pw = vertical ? 440 : 400;
  const ph = vertical ? 560 : 540;
  const gap = vertical ? 40 : 30;
  const x0 = vertical ? (width - 2 * pw - gap) / 2 : (width - 4 * pw - 3 * gap) / 2;
  const y0 = vertical ? 260 : 150;
  const planeXY = (i: number) => (vertical ? { x: x0 + (i % 2) * (pw + gap), y: y0 + Math.floor(i / 2) * (ph + gap) } : { x: x0 + i * (pw + gap), y: y0 });
  const nodeH = 76;
  const pos: Record<string, { x: number; y: number }> = {};
  PLANES.forEach((_, pi) => {
    NODES.filter((n) => n.plane === pi).forEach((n, k) => {
      const p = planeXY(pi);
      pos[n.name] = { x: p.x + pw / 2, y: p.y + 104 + k * (nodeH + 26) + nodeH / 2 };
    });
  });
  const d = FLOW.map((n, i) => `${i ? "L" : "M"} ${pos[n].x} ${pos[n].y}`).join(" ");
  const len = getLength(d);
  const draw = interpolate(f, [b(1), b(4.5)], [0, 1], { ...clamp, easing: INOUT });
  const llm = interpolate(f, [b(15) + 6, b(15) + 14], [0, 1], clamp);
  const pulse = 0.6 + 0.4 * Math.sin(f / 4);
  // diagram camera: wide -> crash onto planes 0-1 -> cut to 2-3 -> pull back out
  const pairCenter = (a: number, c: number) => {
    const p = planeXY(a);
    const q = planeXY(c);
    return { x: (p.x + q.x + pw) / 2, y: (p.y + q.y + ph) / 2 };
  };
  const wide = { x: width / 2, y: y0 + (vertical ? ph + gap / 2 : ph / 2), z: 1 };
  const zPair = vertical ? 1.05 : 1.95;
  const A01 = { ...pairCenter(0, 1), z: zPair };
  const A23 = { ...pairCenter(2, 3), z: zPair };
  const lerp3 = (p: { x: number; y: number; z: number }, q: typeof p, t: number) => ({ x: p.x + (q.x - p.x) * t, y: p.y + (q.y - p.y) * t, z: p.z + (q.z - p.z) * t });
  const cam =
    f < b(5)
      ? wide
      : f < b(10)
        ? lerp3(wide, A01, interpolate(f, [b(5), b(5) + 6], [0, 1], { ...clamp, easing: Easing.in(Easing.cubic) }))
        : f < b(15)
          ? A23
          : lerp3(A23, wide, interpolate(f, [b(15), b(15) + 8], [0, 1], { ...clamp, easing: OUT }));
  const fy = height * (vertical ? 0.42 : 0.44);
  return (
    <AbsoluteFill>
      <Ground />
      <AbsoluteFill style={{ transformOrigin: "0 0", transform: `translate(${width / 2 - cam.x * cam.z}px, ${fy - cam.y * cam.z}px) scale(${cam.z})` }}>
        {PLANES.map((p, i) => {
          const xy = planeXY(i);
          const t = interpolate(f, [i * 3, i * 3 + 10], [0, 1], { ...clamp, easing: OUT });
          return (
            <div key={p} style={{ position: "absolute", left: xy.x, top: xy.y + (1 - t) * 60, width: pw, height: ph, opacity: t }}>
              <Glass style={{ width: "100%", height: "100%" }}>
                <div style={{ fontFamily: SANS, fontSize: 30, fontWeight: 700, color: A.muted, padding: "26px 28px", letterSpacing: 1 }}>{p.toUpperCase()}</div>
              </Glass>
            </div>
          );
        })}
        <svg width={width} height={height * 1.4} style={{ position: "absolute", inset: 0, overflow: "visible" }}>
          <path d={d} fill="none" stroke={A.cyan} strokeOpacity={0.55} strokeWidth={3} strokeDasharray={evolvePath(draw, d).strokeDasharray} strokeDashoffset={evolvePath(draw, d).strokeDashoffset} />
          {draw >= 1 &&
            Array.from({ length: 16 }).map((_, i) => {
              const at = ((((f - b(4.5)) * 9 + i * (len / 16)) % len) + len) % len;
              const p = getPointAtLength(d, at);
              if (!p) return null;
              return <circle key={i} cx={p.x} cy={p.y} r={i % 4 === 0 ? 7 : 4.5} fill={i % 4 === 0 ? "#fff" : A.cyan} opacity={0.9} style={{ filter: `drop-shadow(0 0 8px ${A.cyan})` }} />;
            })}
        </svg>
        {NODES.map((n, k) => {
          const p = pos[n.name];
          const t = interpolate(f, [6 + k * 2, 6 + k * 2 + 8], [0, 1], { ...clamp, easing: OUT });
          const glow = n.llm ? llm : 0;
          return (
            <div
              key={n.name}
              style={{
                position: "absolute",
                left: p.x - pw / 2 + 24,
                top: p.y - nodeH / 2,
                width: pw - 48,
                height: nodeH,
                borderRadius: 12,
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "0 20px",
                fontFamily: SANS,
                fontSize: 32,
                fontWeight: n.dashed ? 500 : 700,
                color: n.dashed ? A.faint : A.ink,
                background: n.dashed ? "transparent" : A.glassHi,
                border: `${glow > 0.5 ? 2 : 1}px ${n.dashed ? "dashed" : "solid"} ${glow > 0.5 ? A.magenta : A.line}`,
                boxShadow: glow > 0 ? `0 0 ${30 * pulse * glow}px ${A.magenta}` : undefined,
                opacity: t,
                scale: `${0.85 + 0.15 * t}`,
              }}
            >
              <span>{n.name}</span>
              {n.llm && <span style={{ fontFamily: MONO, fontSize: 26, color: A.magenta, opacity: llm }}>{n.llm}</span>}
            </div>
          );
        })}
      </AbsoluteFill>
      {/* scrim so the caption reads over the zoomed diagram */}
      <AbsoluteFill style={{ background: "linear-gradient(180deg, transparent 62%, rgba(10,14,28,0.92) 86%)", pointerEvents: "none" }} />
      <Kinetic words={words(`${CLAIMS.agents.value} agents.`, b(1), 4, { Nine: A.cyan })} out={b(5) - 4} />
      <Kinetic words={words("Rules guard every run.", b(5) + 6, 3, { Rules: A.cyan })} out={b(10) - 4} />
      <Kinetic words={words("One graph, end to end.", b(10), 3, { graph: A.cyan })} out={b(15) - 4} />
      <Kinetic words={words("LLMs only where needed.", b(15) + 4, 3, { LLMs: A.magenta })} />
    </AbsoluteFill>
  );
};

// ------------------------------------------------------------------ HERO
const DiagCard: React.FC = () => {
  const f = useCurrentFrame();
  const { vertical } = useV();
  const t = interpolate(f, [0, 8], [0, 1], { ...clamp, easing: OUT });
  const pick = f >= b(1.5);
  const acts = ["rename_column", "safe_type_cast", "strip_whitespace", "normalize_case", "escalate"];
  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", top: vertical ? -180 : -80 }}>
      <div style={{ scale: `${1.25 - 0.25 * t}`, opacity: t, filter: `blur(${(1 - t) * 8}px)` }}>
        <Glass glow={A.magenta} style={{ padding: vertical ? "36px 38px" : "34px 44px", width: vertical ? 920 : 1080, fontFamily: MONO, color: A.ink }}>
          <div style={{ fontFamily: SANS, fontSize: 32, color: A.magenta, fontWeight: 700, marginBottom: 18 }}>● diagnosis · run #647 · stored output</div>
          <div style={{ fontSize: vertical ? 44 : 46, lineHeight: 1.45 }}>
            action&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<span style={{ color: A.cyan }}>rename_column</span>
            <br />
            risk&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<span style={{ color: A.green }}>low</span>
            <br />
            confidence <span style={{ color: A.ink }}>0.95</span>
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginTop: 26 }}>
            {acts.map((a, i) => {
              const on = pick && a === "rename_column";
              const ti = interpolate(f, [6 + i * 2, 12 + i * 2], [0, 1], clamp);
              return (
                <span key={a} style={{ fontSize: 30, padding: "8px 14px", borderRadius: 8, border: `1px solid ${on ? A.cyan : A.line}`, color: on ? A.cyan : A.faint, background: on ? "rgba(94,224,245,0.12)" : "transparent", opacity: on ? 1 : ti * (pick ? 0.45 : 1), boxShadow: on ? `0 0 24px ${A.cyan}88` : undefined }}>
                  {a}
                </span>
              );
            })}
          </div>
        </Glass>
      </div>
    </AbsoluteFill>
  );
};

const SplitFix: React.FC = () => {
  const f = useCurrentFrame();
  const { vertical } = useV();
  const cols = vertical ? colsV("city") : colsH("city").filter((c) => c.key !== "customer_email" && c.key !== "category");
  const healAt = b(1);
  const t = interpolate(f, [0, 8], [0, 1], { ...clamp, easing: OUT });
  const close = f >= b(3); // hard cut on the beat to the healed side
  const grid = (after: boolean) => (
    <Grid
      rows={after ? DATA.clean : renamedRows}
      cols={cols}
      font={vertical ? 32 : 28}
      rowH={vertical ? 64 : 62}
      label={after ? "after rename_column · re-validated" : "as ingested · run #647"}
      head={(k) => (k === "city" ? (after ? (f >= healAt + 4 ? { text: "city", color: A.green, bg: "rgba(94,240,168,0.16)" } : { text: "town", color: A.red, bg: "rgba(255,90,120,0.22)" }) : { text: "town", color: A.red, bg: "rgba(255,90,120,0.22)", glitch: 0.6 }) : {})}
      cell={(ri, k) => (k === "city" ? (after && f >= healAt + ri * 2 + 3 ? { color: A.green, bg: "rgba(94,240,168,0.12)" } : { bg: "rgba(255,90,120,0.12)" }) : {})}
    />
  );
  const check = (
    <svg width={72} height={72} viewBox="0 0 40 40" style={{ position: "absolute", right: -28, top: -14, scale: `${interpolate(f, [healAt + 10, healAt + 18], [0.4, 1], { ...clamp, easing: OUT })}` }}>
      <circle cx={20} cy={20} r={19} fill="#1FAE6A" />
      <path d="M11 21 L18 28 L30 13" fill="none" stroke="#fff" strokeWidth={4} strokeLinecap="round" strokeLinejoin="round" strokeDasharray={30} strokeDashoffset={30 * (1 - interpolate(f, [healAt + 12, healAt + 20], [0, 1], clamp))} />
    </svg>
  );
  if (close) {
    return (
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", top: vertical ? -200 : -90 }}>
        <div style={{ position: "relative", scale: vertical ? "1.15" : "1.45" }}>
          {grid(true)}
          {check}
        </div>
      </AbsoluteFill>
    );
  }
  return (
    <AbsoluteFill style={{ flexDirection: vertical ? "column" : "row", alignItems: "center", justifyContent: "center", gap: vertical ? 50 : 60, top: vertical ? -200 : -90 }}>
      <div style={{ opacity: t, translate: `${(1 - t) * -60}px 0` }}>{grid(false)}</div>
      <div style={{ fontSize: 60, color: A.cyan, fontFamily: SANS, rotate: vertical ? "90deg" : "0deg", opacity: t }}>→</div>
      <div style={{ opacity: t, translate: `${(1 - t) * 60}px 0`, position: "relative" }}>
        {grid(true)}
        {f >= healAt + 10 && check}
      </div>
    </AbsoluteFill>
  );
};

// The type cast run #648's diagnosis chose, attempted on `price`: a scan runs
// down the column and stops at the first value that cannot convert.
const CastAttempt: React.FC<{ reverse?: boolean; len: number }> = ({ reverse, len }) => {
  const own = useCurrentFrame();
  const f = reverse ? Math.max(0, len - own) : own; // the rewind plays it back at 1x
  const { vertical } = useV();
  const cols = vertical ? colsV("city") : colsH("city").filter((c) => c.key !== "customer_email");
  const scan = interpolate(f, [4, 26], [0, 1], { ...clamp, easing: Easing.in(Easing.quad) });
  const failAt = 26;
  const failed = f >= failAt;
  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", top: vertical ? -200 : -90 }}>
      <div style={{ position: "relative" }}>
        <Grid
          rows={DATA.dollar}
          cols={cols}
          font={vertical ? 36 : 30}
          rowH={vertical ? 80 : 66}
          label="safe_type_cast(price → float64) · run #648"
          head={(k) => (k === "price" ? { color: A.amber, bg: "rgba(255,196,107,0.12)" } : {})}
          cell={(ri, k) => {
            if (k !== "price") return {};
            const lit = scan * DATA.dollar.length > ri;
            if (failed && ri === 0) return { color: "#fff", bg: "rgba(255,60,90,0.75)", glitch: 1 };
            return lit ? { color: A.amber, bg: "rgba(255,196,107,0.14)" } : {};
          }}
        />
        {failed && (
          <div style={{ position: "absolute", left: "50%", top: -82, translate: "-50% 0", whiteSpace: "nowrap", ...glitchStyle(f, f < failAt + 8 ? 1 : 0, "err") }}>
            <Glass glow={A.red} style={{ padding: "12px 20px", fontFamily: MONO, fontSize: 30, color: A.red }}>could not convert '$80.90'</Glass>
          </div>
        )}
      </div>
    </AbsoluteFill>
  );
};

const HERO = { detect: 4, diagnose: 4, fix: 6, verify: 6, attempt: 4, rewind: 4, caught: 8 };
const heroStarts = (() => {
  let t = 0;
  const o: Record<string, number> = {};
  for (const [k, v] of Object.entries(HERO)) {
    o[k] = t;
    t += v;
  }
  return o;
})();

const Hero: React.FC = () => {
  const S = heroStarts;
  const at = (k: keyof typeof HERO) => b(S[k]);
  const len = (k: keyof typeof HERO) => b(HERO[k]);
  const { vertical } = useV();
  const zc = vertical ? 2.4 : 2.2;
  return (
    <AbsoluteFill>
      <Ground />
      <Sequence from={at("detect")} durationInFrames={len("detect")}>
        <Stage url="localhost:5173/audit?run=647" dur={len("detect")} orbit={[-12, -6]}>
          <PV name="audit-kept" keys={keys({ f: 0, cx: 848, cy: 1350, z: 0.85 }, { f: 10, cx: 848, cy: 1350, z: 0.85 }, crash(10, { cx: 540, cy: 1555, z: zc }))}>
            <Glitch name="audit-kept" b={{ x: 312, y: 1500, w: 430, h: 110 }} from={16} to={len("detect")} />
            <Spotlight b={{ x: 312, y: 1500, w: 430, h: 110 }} at={18} />
          </PV>
        </Stage>
        <Kinetic words={words("Caught by rules. No model.", 12, 4, { rules: A.cyan })} />
      </Sequence>
      <Sequence from={at("diagnose")} durationInFrames={len("diagnose")}>
        <DiagCard />
        <Kinetic words={words(`Model picks 1 of ${CLAIMS.actions.value}.`, 4, 4, { Model: A.magenta })} />
      </Sequence>
      <Sequence from={at("fix")} durationInFrames={len("fix")}>
        <SplitFix />
        <Kinetic words={[...words("Model picks which.", 2, 3, { which: A.magenta }), ...words("Code decides how.", b(3), 3, { how: A.cyan })]} size={vertical ? 92 : 84} />
      </Sequence>
      <Sequence from={at("verify")} durationInFrames={len("verify")}>
        <Stage url="localhost:5173/audit?run=647" dur={len("verify")} orbit={[8, 3]}>
          <PV name="audit-kept" keys={keys({ f: 0, cx: 1000, cy: 1555, z: 1.7 }, cutTo(b(3), { cx: 1075, cy: 1555, z: vertical ? 2.4 : 2.3 }))}>
            <HealWipe b={{ x: 820, y: 1505, w: 410, h: 100 }} at={b(0.6)} check={false} />
            <Circle b={{ x: 828, y: 1520, w: 70, h: 72 }} at={b(1.4)} out={b(3) - 6} color="#1FAE6A" />
            <Spotlight b={{ x: 925, y: 1503, w: 305, h: 104 }} at={b(3)} />
          </PV>
        </Stage>
        <Kinetic words={words("Re-validated. Kept.", 6, 5, { Kept: A.green })} />
      </Sequence>
      <Sequence from={at("attempt")} durationInFrames={len("attempt")}>
        <CastAttempt len={len("attempt")} />
        <Kinetic words={words("Next fix fails.", 24, 4, { fails: A.red })} />
      </Sequence>
      <Sequence from={at("rewind")} durationInFrames={len("rewind")}>
        <VHS>
          <Ground />
          <CastAttempt len={len("attempt")} reverse />
        </VHS>
      </Sequence>
      <Sequence from={at("caught")} durationInFrames={len("caught")}>
        <Stage url="localhost:5173/audit?run=648" dur={len("caught")} orbit={[-6, -11]}>
          <PV name="audit-reverted" keys={keys({ f: 0, cx: 900, cy: 1040, z: vertical ? 1.8 : 1.7 }, cutTo(b(3), { cx: 930, cy: 1040, z: zc + 0.2 }), cutTo(b(5.5), { cx: 1165, cy: 1062, z: zc + 0.2 }))}>
            <Spotlight b={{ x: 309, y: 980, w: 1078, h: 117 }} at={2} out={b(5.5)} dim={0.35} />
            <Circle b={{ x: 844, y: 1029, w: 176, h: 22 }} at={b(0.8)} color="#E11D48" stroke={6} />
            <Spotlight b={{ x: 1043, y: 990, w: 250, h: 95 }} at={b(5.5)} />
            <Circle b={{ x: 1043, y: 1062, w: 250, h: 24 }} at={b(5.8)} color="#E11D48" stroke={6} />
          </PV>
        </Stage>
        <Kinetic words={words("It caught its own mistake.", 6, 4, { caught: A.cyan })} />
      </Sequence>
      <Leak at={b(S.fix) - 6} dur={14} hue="cool" />
    </AbsoluteFill>
  );
};
const heroBeats = Object.values(HERO).reduce((a, c) => a + c, 0);
const heroSfx: Sfx[] = [
  { at: b(heroStarts.detect), src: "whoosh-fast", vol: 0.5 },
  { at: b(heroStarts.detect) + 10, src: "zoom-swipe-fast", vol: 0.55 },
  { at: b(heroStarts.detect) + 16, src: "glitch-static", vol: 0.35 },
  { at: b(heroStarts.diagnose), src: "hit-weak", vol: 0.8 },
  { at: b(heroStarts.diagnose) + b(1.5), src: "switch-click-quick", vol: 0.5 },
  { at: b(heroStarts.fix), src: "transition-snap", vol: 0.6 },
  { at: b(heroStarts.fix) + b(1), src: "chime-crystal", vol: 0.55 },
  { at: b(heroStarts.fix) + b(3), src: "swoosh-quick", vol: 0.4 },
  { at: b(heroStarts.verify), src: "swoosh-quick", vol: 0.6 },
  { at: b(heroStarts.verify) + b(1.4), src: "sparkle-touch", vol: 0.6 },
  { at: b(heroStarts.verify) + b(3), src: "zoom-swipe-fast", vol: 0.4 },
  { at: b(heroStarts.attempt), src: "data-scan", vol: 0.45 },
  { at: b(heroStarts.attempt) + 26, src: "break-glitch-digital", vol: 0.6 },
  { at: b(heroStarts.rewind), src: "tape-rewind-fast", vol: 0.8 },
  { at: b(heroStarts.caught), src: "bass-hit-short", vol: 0.55 },
  { at: b(heroStarts.caught) + b(0.8), src: "hit-weak", vol: 0.7 },
  { at: b(heroStarts.caught) + b(3), src: "transition-snap", vol: 0.4 },
  { at: b(heroStarts.caught) + b(5.5), src: "zoom-swipe-fast", vol: 0.4 },
];

// ----------------------------------------------------------------- HUMAN
// Pointer glides in (b0.3-b1.6), crash to Keep (b1.6), FREEZE b2-b5 (45f,
// speed-ramp-freeze) with the circle, click on b5, ripple finishes, cut b6.5.
const Human: React.FC = () => {
  const f = useCurrentFrame();
  const { vertical } = useV();
  const click = b(5);
  const freeze = f >= b(2) && f < click;
  const cut = b(6.5);
  return (
    <AbsoluteFill>
      <Ground />
      <Sequence durationInFrames={cut}>
        <Stage url="localhost:5173/approvals" dur={cut} orbit={[-8, -4]}>
          <PV
            name="approvals-hover-keep"
            keys={keys({ f: 0, cx: 760, cy: 600, z: vertical ? 1.3 : 1.25 }, { f: b(1.6), cx: 760, cy: 600, z: vertical ? 1.3 : 1.25 }, crash(b(1.6), { cx: 560, cy: 650, z: vertical ? 2.4 : 2.2 }))}
            filter={freeze ? "saturate(0.3) brightness(0.9)" : undefined}
          >
            <Pointer from={{ x: 1000, y: 820 }} to={{ x: 470, y: 672 }} move={[b(0.3), b(1.6)]} click={click} />
            <Circle b={{ x: 416, y: 650, w: 126, h: 34 }} at={b(2.4)} out={click} color={A.amber} stroke={4} pad={8} />
          </PV>
        </Stage>
      </Sequence>
      <Sequence from={cut}>
        <Stage url="localhost:5173/audit?run=648" dur={b(12) - cut} orbit={[4, 8]}>
          {f < b(8) ? (
            <PV name="audit-reverted" keys={[{ f: 0, cx: 900, cy: 1040, z: vertical ? 2.2 : 2.0 }]}>
              <Spotlight b={{ x: 690, y: 1028, w: 330, h: 24 }} at={0} />
            </PV>
          ) : (
            <PV name="audit-resolved" keys={[{ f: b(8) - cut, cx: 1000, cy: 1612, z: vertical ? 1.9 : 2.0 }, { f: b(12) - cut, cx: 1060, cy: 1612, z: vertical ? 2.0 : 2.1 }]}>
              <Spotlight b={{ x: 690, y: 1590, w: 700, h: 44 }} at={b(8) - cut} dim={0.45} />
              <Circle b={{ x: 1298, y: 1590, w: 90, h: 44 }} at={b(8.6) - cut} color="#1FAE6A" stroke={5} pad={8} />
            </PV>
          )}
        </Stage>
      </Sequence>
      {freeze && <AbsoluteFill style={{ boxShadow: `inset 0 0 0 10px ${A.amber}55`, pointerEvents: "none" }} />}
      <Kinetic words={words("A person decides.", 4, 4, { person: A.amber })} out={cut - 4} />
      <Kinetic words={words("Fix rejected. Data kept.", b(8), 4, { kept: A.green })} />
    </AbsoluteFill>
  );
};
const humanSfx: Sfx[] = [
  { at: 0, src: "air-woosh-quick", vol: 0.5 },
  { at: b(1.6), src: "zoom-swipe-fast", vol: 0.5 },
  { at: b(2.4), src: "hit-weak", vol: 0.6 },
  { at: b(5), src: "switch-click-quick", vol: 1.0 },
  { at: b(6.5), src: "swoosh-quick", vol: 0.5 },
  { at: b(8), src: "sparkle-touch", vol: 0.7 },
];

// ------------------------------------------------------------------- ASK
// The Question box from capture_v2.mjs, one real frame per keystroke, shown
// on its own (no compositing). Clip: page x 288-1408, y 220-410.
const TYPE_N = 39;
const TypeFrames: React.FC<{ n: number; z: number; ox: number; oy: number; pointer?: React.ReactNode }> = ({ n, z, ox, oy, pointer }) => {
  const d = useDev();
  return (
    <div style={{ position: "absolute", width: d.w, height: d.h, overflow: "hidden", background: "#F7F8FA" }}>
      <div style={{ position: "absolute", left: d.w / 2 - ox * z, top: d.h / 2 - oy * z, width: 1120 * z, height: 190 * z }}>
        <Img src={staticFile(`shots/ask-type-${String(n).padStart(2, "0")}.png`)} style={{ width: "100%", height: "100%" }} />
        {/* the clip's top edge slices the "Question" label; cover that strip */}
        <div style={{ position: "absolute", left: 0, right: 0, top: 0, height: 30 * z, background: "#F7F8FA" }} />
        <div style={{ position: "absolute", inset: 0, transform: `scale(${z})`, transformOrigin: "0 0" }}>{pointer}</div>
      </div>
    </div>
  );
};

const Ask: React.FC<{ drop: number }> = ({ drop }) => {
  const f = useCurrentFrame();
  const { vertical } = useV();
  const typeEnd = b(7);
  const n = Math.min(TYPE_N, Math.max(0, Math.floor(interpolate(f, [6, typeEnd - 6], [0, TYPE_N], clamp))));
  const resultAt = b(8);
  const lineH = 20.6;
  const top0 = 605 + 18;
  const lines = 24;
  const shown = interpolate(f, [drop, drop + b(4)], [0, lines], clamp);
  const glow = interpolate(f, [resultAt + 4, resultAt + 12], [0, 1], clamp) * (0.7 + 0.3 * Math.sin(f / 3));
  const tight = f >= b(4); // hard cut on b4 to a tighter crop of the same box
  // measured: the typed text sits at clip y ~60, the Ask button at ~125
  const zt = vertical ? (tight ? 2.6 : 2.0) : tight ? 2.7 : 2.2;
  const ox = tight ? 230 : vertical ? 260 : 300;
  return (
    <AbsoluteFill>
      <Ground />
      <Sequence durationInFrames={resultAt}>
        <Stage url="localhost:5173/ask?run=650" dur={resultAt} orbit={[-9, -5]}>
          <TypeFrames n={n} z={zt} ox={ox} oy={tight ? 66 : 85} pointer={f >= typeEnd - 12 ? <Pointer from={{ x: 260, y: 230 }} to={{ x: 46, y: 152 }} move={[typeEnd - 10, typeEnd + 2]} click={typeEnd + 4} scale={0.5} /> : null} />
        </Stage>
      </Sequence>
      <Sequence from={resultAt}>
        <Stage url="localhost:5173/ask?run=650" dur={b(16) - resultAt} orbit={[6, 2]}>
          <PV
            name="ask-result"
            keys={keys(
              { f: 0, cx: vertical ? 600 : 830, cy: 537, z: vertical ? 1.6 : 1.15 },
              { f: drop - resultAt - 1, cx: vertical ? 620 : 830, cy: 537, z: vertical ? 1.7 : 1.22 },
              cutTo(drop - resultAt, { cx: vertical ? 520 : 640, cy: 760, z: vertical ? 2.0 : 1.75 }),
              cutTo(drop - resultAt + b(3), { cx: vertical ? 520 : 640, cy: 1000, z: vertical ? 2.0 : 1.75 }),
            )}
          >
            <div style={{ position: "absolute", left: 309, top: 510, width: 1078, height: 56, borderRadius: 6, boxShadow: `0 0 ${30 * glow}px ${A.cyan}, inset 0 0 ${16 * glow}px ${A.cyan}88` }} />
            {/* the real JSON result, revealed line by line behind a scan edge */}
            <div style={{ position: "absolute", left: 309, top: top0 + shown * lineH, width: 1078, height: 1140 - (top0 + shown * lineH), background: "#12161C" }} />
            {shown > 0 && shown < lines && <div style={{ position: "absolute", left: 309, top: top0 + shown * lineH - 3, width: 1078, height: 4, background: A.cyan, boxShadow: `0 0 18px ${A.cyan}` }} />}
          </PV>
        </Stage>
      </Sequence>
      <Kinetic words={words("Ask anything.", 2, 4, { anything: A.cyan })} out={resultAt - 4} />
      <Kinetic words={words("See the SQL.", resultAt, 4, { SQL: A.cyan })} out={drop - 4} />
      <Kinetic words={words("Get the answer.", drop, 3, { answer: A.green })} />
    </AbsoluteFill>
  );
};
const askSfx = (drop: number): Sfx[] => [
  { at: 0, src: "swoosh-quick", vol: 0.5 },
  { at: 6, src: "typewriter-digital", vol: 0.45 },
  { at: 6 + 55, src: "typewriter-digital", vol: 0.45 },
  { at: b(7) + 4, src: "switch-click-quick", vol: 0.9 },
  { at: b(8), src: "whoosh-fast", vol: 0.5 },
  { at: b(8) + 4, src: "data-scan", vol: 0.4 },
  { at: drop, src: "bass-hit-short", vol: 1.0 },
  { at: drop + b(3), src: "transition-snap", vol: 0.35 },
];

// ---------------------------------------------------------------- REPORT
const NARR = { x: 288, top: 790, lineH: 20.2, lines: 9, w: 520 };
const Report: React.FC = () => {
  const f = useCurrentFrame();
  const { vertical } = useV();
  const themeAt = b(11);
  const rev0 = b(3) + 4;
  const per = 6; // frames per line (document-typewriter-reveal pace)
  const k = Math.floor((f - rev0) / per); // current line
  const frac = interpolate((f - rev0) % per, [0, per], [0, 1], clamp);
  const z1 = vertical ? 1.9 : 1.6;
  const z2 = vertical ? 2.3 : 2.1;
  const z3 = vertical ? 2.5 : 2.4;
  return (
    <AbsoluteFill>
      <Ground />
      <Sequence durationInFrames={themeAt}>
        <Stage url="localhost:5173/reports?run=608" dur={themeAt} orbit={[-7, -3]}>
          <PV name="reports" keys={keys({ f: 0, cx: 640, cy: 240, z: z1 }, cutTo(b(3), { cx: 540, cy: 870, z: z2 }), { f: b(7) - 1, cx: 540, cy: 880, z: z2 }, cutTo(b(7), { cx: 520, cy: 1000, z: z3 }))}>
            <Spotlight b={{ x: 309, y: 318, w: 140, h: 28 }} at={b(0.8)} out={b(3)} dim={0.4} />
            <Circle b={{ x: 309, y: 322, w: 135, h: 20 }} at={b(1)} color={A.magenta} stroke={4} pad={8} />
            {/* the stored narrative written out line by line: every line below
                the current one is hidden as ONE block (no slivers); the current
                line is uncovered left to right behind a caret */}
            {f >= b(3) && k < NARR.lines && (
              <>
                <div style={{ position: "absolute", left: NARR.x - 4, top: NARR.top + (Math.max(0, k) + 1) * NARR.lineH, width: NARR.w + 8, height: (NARR.lines - Math.max(0, k) - 1) * NARR.lineH + 10, background: "#F7F8FA" }} />
                <div style={{ position: "absolute", left: NARR.x + NARR.w * (k < 0 ? 0 : frac), top: NARR.top + Math.max(0, k) * NARR.lineH, width: NARR.w * (1 - (k < 0 ? 0 : frac)) + 4, height: NARR.lineH + 1, background: "#F7F8FA" }} />
                <div style={{ position: "absolute", left: NARR.x + NARR.w * (k < 0 ? 0 : frac), top: NARR.top + Math.max(0, k) * NARR.lineH + 2, width: 2.5, height: NARR.lineH - 4, background: "#0B6E6E" }} />
              </>
            )}
            <Circle b={{ x: 468, y: 1065, w: 90, h: 21 }} at={b(7.6)} color={A.cyan} stroke={4} pad={8} />
          </PV>
        </Stage>
      </Sequence>
      <Sequence from={themeAt}>
        <Stage url="localhost:5173/reports?run=608" dur={b(5)} orbit={[-3, 3]}>
          <PV name="theme-default" keys={[{ f: 0, cx: 720, cy: 300, z: vertical ? 1.2 : 1.3 }]} />
          <Sweep at={b(1.5)}>
            <PV name="theme-dark" keys={[{ f: 0, cx: 720, cy: 300, z: vertical ? 1.2 : 1.3 }]} />
          </Sweep>
          <Sweep at={b(3)}>
            <PV name="theme-aurora" keys={[{ f: 0, cx: 720, cy: 300, z: vertical ? 1.2 : 1.3 }]} />
          </Sweep>
        </Stage>
      </Sequence>
      <Kinetic words={words("The report writes itself.", 4, 4, { itself: A.cyan })} out={b(7) - 4} />
      <Kinetic words={words("Stage 2 never sees raw findings.", b(7), 3, { never: A.cyan })} out={themeAt - 4} size={vertical ? 88 : 80} />
      <Kinetic words={[{ t: "Default.", at: themeAt }, { t: "Dark.", at: themeAt + b(1.5) }, { t: "Aurora.", at: themeAt + b(3), accent: A.magenta }]} />
    </AbsoluteFill>
  );
};
const reportSfx: Sfx[] = [
  { at: 0, src: "whoosh-fast", vol: 0.5 },
  { at: b(1), src: "hit-weak", vol: 0.5 },
  { at: b(3), src: "transition-snap", vol: 0.4 },
  { at: b(3) + 4, src: "typewriter-digital", vol: 0.3 },
  { at: b(7), src: "transition-snap", vol: 0.4 },
  { at: b(11), src: "swoosh-quick", vol: 0.5 },
  { at: b(12.5), src: "air-woosh-quick", vol: 0.5 },
  { at: b(14), src: "air-woosh-quick", vol: 0.5 },
];

// ----------------------------------------------------------------- TRUST
/** One EVIDENCE.md figure per shot: reel number, two-word label, and the
 *  source as a footer that arrives after the number lands. */
const StatShot: React.FC<{ value: string; label: string; source: string }> = ({ value, label, source }) => {
  const f = useCurrentFrame();
  const { vertical } = useV();
  const t = interpolate(f, [0, 8], [0, 1], { ...clamp, easing: OUT });
  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 18, top: vertical ? -120 : -40 }}>
      <div style={{ scale: `${1.12 - 0.12 * t}` }}>
        <Glass glow={A.cyan} style={{ padding: vertical ? "50px 70px" : "44px 90px", textAlign: "center", minWidth: vertical ? 860 : 900 }}>
          <Odometer value={value} at={4} size={vertical ? 230 : 210} />
          <div style={{ fontFamily: SANS, fontSize: vertical ? 70 : 64, fontWeight: 700, color: A.ink, marginTop: 18 }}>{label}</div>
          <div style={{ fontFamily: MONO, fontSize: vertical ? 32 : 30, color: A.faint, marginTop: 16, opacity: interpolate(f, [26, 34], [0, 1], clamp) }}>{source}</div>
        </Glass>
      </div>
    </AbsoluteFill>
  );
};
export const STATS = [
  { value: CLAIMS.accuracy.value, label: "diagnoses correct", source: "live model · EVIDENCE.md §4" },
  { value: CLAIMS.wrongFix.value, label: "wrong auto-fixes", source: "all 10 thresholds · cache replay · §4" },
  { value: CLAIMS.tests.value, label: "tests passed", source: "backend · latest full run · §1.5" },
];
export const StatsRun: React.FC<{ each: number }> = ({ each }) => (
  <>
    {STATS.map((s, i) => (
      <Sequence key={s.label} from={i * each} durationInFrames={i === STATS.length - 1 ? undefined : each}>
        <StatShot {...s} />
      </Sequence>
    ))}
  </>
);

const Trust: React.FC = () => {
  const f = useCurrentFrame();
  const { vertical } = useV();
  const cols: Col[] = vertical
    ? [
        { key: "city", label: "city", w: 300 },
        { key: "customer_email", label: "customer_email", w: 600 },
      ]
    : [
        { key: "order_id", label: "order_id", w: 230 },
        { key: "city", label: "city", w: 260 },
        { key: "price", label: "price", w: 170 },
        { key: "customer_email", label: "customer_email", w: 460 },
      ];
  const statsAt = b(8);
  const close = f >= b(2.5);
  return (
    <AbsoluteFill>
      <Ground />
      {f < b(5) && (
        <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", top: vertical ? -200 : -90 }}>
          <div style={{ scale: close ? "1.45" : "1", translate: close ? "-120px 0" : "0 0" }}>
            <Grid
              rows={DATA.clean}
              cols={cols}
              font={vertical ? 34 : 28}
              rowH={vertical ? 78 : 62}
              label="outbound sample · strict policy · app.privacy.redaction"
              cell={(ri, k) =>
                k === "customer_email" || k === "city" ? { node: <Mosaic value={String(DATA.clean[ri][k])} token={String(DATA.redacted[ri][k])} at={b(0.7) + ri * 3 + (k === "city" ? 0 : 6)} /> } : {}
              }
            />
          </div>
        </AbsoluteFill>
      )}
      <Sequence from={b(5)} durationInFrames={statsAt - b(5)}>
        <Stage url="localhost:5173/audit?run=647" dur={b(3)} orbit={[-6, -2]}>
          <PV name="audit-kept" keys={keys({ f: 0, cx: 848, cy: 1050, z: 1.0 }, { f: 8, cx: 848, cy: 1050, z: 1.0 }, crash(8, { cx: 1040, cy: 1121, z: vertical ? 2.4 : 2.2 }))}>
            <Spotlight b={{ x: 312, y: 1092, w: 1072, h: 60 }} at={14} dim={0.45} />
            <Circle b={{ x: 1071, y: 1103, w: 142, h: 34 }} at={18} color={A.magenta} stroke={5} pad={8} />
          </PV>
        </Stage>
      </Sequence>
      <Sequence from={statsAt}>
        <StatsRun each={Math.round(b(8) / 3)} />
      </Sequence>
      <Kinetic words={words("Redacted before it leaves.", 3, 4, { Redacted: A.magenta })} out={b(5) - 4} />
      <Kinetic words={words("Only shape is logged.", b(5), 4, { shape: A.cyan })} out={statsAt - 4} />
    </AbsoluteFill>
  );
};
const trustSfx: Sfx[] = [
  { at: 0, src: "transition-snap", vol: 0.6 },
  { at: b(0.7), src: "glitch-virtual-quick", vol: 0.35 },
  { at: b(1.5), src: "sparkle-touch", vol: 0.5 },
  { at: b(2.5), src: "transition-snap", vol: 0.35 },
  { at: b(5), src: "whoosh-fast", vol: 0.4 },
  { at: b(5) + 8, src: "zoom-swipe-fast", vol: 0.5 },
  { at: b(8) + 4, src: "hit-fast-exciting", vol: 0.9 },
  { at: b(8) + 40 + 4, src: "hit-fast-exciting", vol: 0.9 },
  { at: b(8) + 80 + 4, src: "hit-fast-exciting", vol: 0.9 },
];

// --------------------------------------------------------------- DOMAINS
const Domains: React.FC = () => {
  const f = useCurrentFrame();
  const half = b(4);
  return (
    <AbsoluteFill>
      <Ground />
      <Sequence durationInFrames={half}>
        <Stage url="localhost:5173/marketing?run=385" dur={half} orbit={[-8, -3]}>
          <PV name="marketing" keys={keys({ f: 0, cx: 848, cy: 500, z: 1.0 }, { f: 8, cx: 848, cy: 500, z: 1.0 }, crash(8, { cx: 640, cy: 430, z: 2.0 }))}>
            <Spotlight b={{ x: 300, y: 345, w: 1100, h: 180 }} at={16} dim={0.35} />
          </PV>
        </Stage>
      </Sequence>
      <Sequence from={half}>
        <Stage url="localhost:5173/agriculture?run=384" dur={half} orbit={[8, 3]}>
          <PV name="agriculture" keys={keys({ f: 0, cx: 848, cy: 480, z: 1.0 }, { f: 8, cx: 848, cy: 480, z: 1.0 }, crash(8, { cx: 660, cy: 420, z: 2.0 }))}>
            <Spotlight b={{ x: 300, y: 380, w: 1100, h: 160 }} at={16} dim={0.35} />
          </PV>
        </Stage>
      </Sequence>
      <Kinetic words={words("Marketing.", 2, 4, { Marketing: A.cyan })} out={half - 4} />
      {f >= half && f < half + b(1.5) && <Kinetic words={words("Agriculture.", half, 4, { Agriculture: A.green })} />}
      <Kinetic words={words("Add a domain as a pack.", half + b(1.5), 3, { pack: A.cyan })} />
    </AbsoluteFill>
  );
};
const domainsSfx: Sfx[] = [
  { at: 0, src: "whoosh-fast", vol: 0.5 },
  { at: 8, src: "zoom-swipe-fast", vol: 0.5 },
  { at: b(4), src: "swoosh-quick", vol: 0.5 },
  { at: b(4) + 8, src: "zoom-swipe-fast", vol: 0.5 },
];

// ------------------------------------------------------------------- END
const End: React.FC<{ byline: string }> = ({ byline }) => {
  const f = useCurrentFrame();
  const { vertical } = useV();
  const url = "github.com/sayandp/agentic-data-intelligence-platform";
  const n = Math.floor(interpolate(f, [b(1), b(3)], [0, url.length], clamp));
  const typed = url.slice(0, n);
  const split = "github.com/sayandp/".length;
  return (
    <AbsoluteFill>
      <Ground />
      <Kinetic words={words("Data that fixes itself.", 0, 3, { fixes: A.cyan, itself: A.cyan })} size={120} y={vertical ? 620 : 300} />
      <div style={{ position: "absolute", top: vertical ? 1000 : 500, left: 0, right: 0, textAlign: "center", fontFamily: MONO, fontSize: vertical ? 40 : 46, color: A.cyan, lineHeight: 1.4 }}>
        {vertical ? (
          <>
            {typed.slice(0, split)}
            <br />
            {typed.slice(split)}
          </>
        ) : (
          typed
        )}
        <span style={{ opacity: n < url.length && Math.floor(f / 5) % 2 ? 1 : 0 }}>▍</span>
      </div>
      <div style={{ position: "absolute", top: vertical ? 1170 : 590, left: 0, right: 0, textAlign: "center", fontFamily: SANS, fontSize: vertical ? 52 : 48, fontWeight: 600, color: A.muted, opacity: interpolate(f, [b(3), b(3) + 8], [0, 1], clamp) }}>{byline}</div>
    </AbsoluteFill>
  );
};
const endSfx: Sfx[] = [
  { at: 0, src: "impact-cine-big", vol: 0.8, dur: 110 },
  { at: 4, src: "shimmer-sparkle-sweep", vol: 0.45 },
  { at: b(1), src: "typewriter-digital", vol: 0.3 },
];

// ------------------------------------------------------------- PLAYLISTS
export const hookSfx: Sfx[] = [
  { at: 0, src: "glitch-static", vol: 0.55 },
  { at: b(2), src: "glitch-virtual-quick", vol: 0.45 },
  { at: b(4), src: "hit-weak", vol: 0.8 },
  { at: b(5), src: "chime-crystal", vol: 0.6 },
  { at: b(6), src: "impact-cine-big", vol: 0.85, dur: 100 },
];

export const scenesH = (byline: string): Scene[] => [
  { id: "hook", beats: 10, C: Hook, sfx: hookSfx },
  { id: "problem", beats: 8, C: Problem, sfx: [{ at: 0, src: "break-glitch-digital", vol: 0.45 }, { at: b(4), src: "whoosh-fast", vol: 0.4 }, { at: b(4) + 8, src: "zoom-swipe-fast", vol: 0.5 }] },
  { id: "how", beats: 20, C: How, sfx: [{ at: 0, src: "whoosh-fast", vol: 0.5 }, { at: b(1), src: "hit-weak", vol: 0.4 }, { at: b(5), src: "zoom-swipe-fast", vol: 0.5 }, { at: b(10), src: "transition-snap", vol: 0.45 }, { at: b(15), src: "air-woosh-quick", vol: 0.45 }, { at: b(15) + 8, src: "sparkle-touch", vol: 0.6 }] },
  { id: "hero", beats: heroBeats, C: Hero, sfx: heroSfx },
  { id: "human", beats: 12, C: Human, sfx: humanSfx },
  { id: "ask", beats: 16, C: () => <Ask drop={b(10)} />, sfx: askSfx(b(10)) },
  { id: "report", beats: 16, C: Report, sfx: reportSfx },
  { id: "trust", beats: 16, C: Trust, sfx: trustSfx },
  { id: "domains", beats: 8, C: Domains, sfx: domainsSfx },
  { id: "end", beats: 8, C: () => <End byline={byline} />, sfx: endSfx },
];

// Vertical teaser: hook -> hero heal + revert -> ask -> stat cards -> end.
const StatsScene: React.FC = () => (
  <AbsoluteFill>
    <Ground />
    <StatsRun each={b(10 / 3)} />
  </AbsoluteFill>
);
export const scenesV = (byline: string): Scene[] => [
  { id: "hook", beats: 10, C: Hook, sfx: hookSfx },
  { id: "hero", beats: heroBeats, C: Hero, sfx: heroSfx },
  { id: "ask", beats: 16, C: () => <Ask drop={b(10)} />, sfx: askSfx(b(10)) },
  { id: "stats", beats: 10, C: StatsScene, sfx: [0, 1, 2].map((i) => ({ at: i * b(10 / 3) + 4, src: "hit-fast-exciting", vol: 0.9 })) },
  { id: "end", beats: 6, C: () => <End byline={byline} />, sfx: endSfx },
];
