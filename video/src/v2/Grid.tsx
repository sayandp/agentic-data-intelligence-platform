import React from "react";
import { random, useCurrentFrame } from "remotion";
import data from "../../capture/public/v2-data.json";
import { A, MONO, SANS } from "./kit";

// The real rows of the seeded orders file (scripts/v2_data.py), drawn as a
// data table - this is the FILE, not a dashboard screen, and is labelled so.
export type Row = Record<string, string | number | null>;
export const DATA = data as unknown as { clean: Row[]; renamed: Row[]; dollar: Row[]; redacted: Row[] };

export type Cell = { text?: string; color?: string; bg?: string; glitch?: number; node?: React.ReactNode };
export type Col = { key: string; label: string; w: number };

export const glitchStyle = (f: number, amount: number, seed: string): React.CSSProperties => {
  if (!amount) return {};
  const r = (k: string) => random(`${seed}-${k}-${Math.floor(f / 2)}`);
  const dx = (2 + r("d") * 5) * amount;
  return {
    textShadow: `${dx}px 0 rgba(255,40,90,0.95), ${-dx}px 0 rgba(40,220,255,0.9)`,
    transform: `translateX(${(r("x") - 0.5) * 8 * amount}px)`,
    opacity: r("o") > 0.12 ? 1 : 0.35,
  };
};

export const Grid: React.FC<{
  rows: Row[];
  cols: Col[];
  label?: string;
  font?: number;
  rowH?: number;
  cell?: (ri: number, key: string, f: number) => Cell;
  head?: (key: string, f: number) => Cell;
  style?: React.CSSProperties;
}> = ({ rows, cols, label, font = 26, rowH = 58, cell, head, style }) => {
  const f = useCurrentFrame();
  const width = cols.reduce((a, c) => a + c.w, 0);
  const render = (c: Cell, fallback: string, seed: string, isHead: boolean) => (
    <div
      style={{
        height: "100%",
        display: "flex",
        alignItems: "center",
        padding: "0 18px",
        background: c.bg,
        color: c.color ?? (isHead ? A.cyan : A.ink),
        whiteSpace: "nowrap",
        overflow: "hidden",
        ...glitchStyle(f, c.glitch ?? 0, seed),
      }}
    >
      {c.node ?? c.text ?? fallback}
    </div>
  );
  return (
    <div style={{ width, fontFamily: MONO, fontSize: font, ...style }}>
      {label && <div style={{ fontFamily: SANS, fontSize: font * 0.82, color: A.faint, marginBottom: 12, letterSpacing: 0.3 }}>{label}</div>}
      <div style={{ border: `1px solid ${A.lineStrong}`, borderRadius: 14, overflow: "hidden", background: A.glass, boxShadow: "0 40px 100px rgba(0,0,0,0.5)" }}>
        <div style={{ display: "flex", height: rowH, background: "rgba(94,224,245,0.06)", borderBottom: `1px solid ${A.lineStrong}`, fontWeight: 700 }}>
          {cols.map((c) => (
            <div key={c.key} style={{ width: c.w }}>{render(head ? head(c.key, f) : {}, c.label, `h-${c.key}`, true)}</div>
          ))}
        </div>
        {rows.map((r, ri) => (
          <div key={ri} style={{ display: "flex", height: rowH, borderBottom: ri < rows.length - 1 ? `1px solid ${A.line}` : undefined }}>
            {cols.map((c) => (
              <div key={c.key} style={{ width: c.w }}>{render(cell ? cell(ri, c.key, f) : {}, String(r[c.key] ?? ""), `c-${ri}-${c.key}`, false)}</div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
};
