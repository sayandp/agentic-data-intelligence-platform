// Prints the "figures on screen -> docs/EVIDENCE.md" table for README.md
// straight from src/claims.ts, so the table cannot drift from the cut.
//   node scripts/figures-table.mjs
import { CLAIMS, STATEMENTS } from "../src/claims.ts";

console.log("| On screen | Meaning | Provenance | Kind |\n| --- | --- | --- | --- |");
for (const c of Object.values(CLAIMS)) console.log(`| **${c.value}** | ${c.label} | ${c.source} | ${c.kind} |`);
console.log("\n| Statement (no figure) | Backed by |\n| --- | --- |");
for (const [s, src] of STATEMENTS) console.log(`| ${s} | ${src} |`);
