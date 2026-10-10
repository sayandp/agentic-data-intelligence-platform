// EVERY figure the video states in its own words lives here, with the
// docs/EVIDENCE.md section it comes from. scripts/figures-table.mjs renders
// this list into video/README.md, so the table cannot drift from the cut.
//
// Numbers that appear INSIDE filmed dashboard frames (run numbers, counts on
// a page) are not claims of the video: they are whatever the filmed runs
// returned, and are listed separately in video/README.md.
export type Claim = { id: string; value: string; label: string; source: string; kind: string; n?: number };

export const CLAIMS = {
  agents: { id: "agents", value: "Nine", label: "agents (v1 only; v2 dropped the count: §1.1 predates the Agriculture and Session Summary agents)", source: "EVIDENCE.md §1.1 (Agents table, nine rows incl. the connector layer)", kind: "code inventory" },
  actions: { id: "actions", value: "5", label: "actions in the closed FixAction enum", source: "EVIDENCE.md §3.1 (Closed FixAction enum: five members)", kind: "code inventory" },
  accuracy: { id: "accuracy", value: "11/12", label: "diagnoses correct", source: "EVIDENCE.md §4 (Diagnosis accuracy, scripts/part6_evaluation.py)", kind: "live model" },
  wrongFix: { id: "wrongFix", value: "0", label: "wrongly auto-fixed, at all 10 thresholds", source: "EVIDENCE.md §4 (Wrongly auto-fixed across sweep, part6_wrongly_auto_fixed_replay.py)", kind: "cache replay, 0 live calls" },
  reverts: { id: "reverts", value: "2 of 6", label: "auto-applies reverted by their post-condition", source: "EVIDENCE.md §4 (Post-condition reverts)", kind: "cache replay" },
  egress: { id: "egress", value: "0", label: "data values stored in the egress trail", source: "EVIDENCE.md §4 (Egress trail leak scan)", kind: "tests + live E2E" },
  tests: { id: "tests", value: "1075", n: 1075, label: "backend tests passed (full run, 2026-10-10)", source: "EVIDENCE.md §1.5 (Backend full-suite pass, re-run 2026-10-10 at 091484e: 1075 passed, 0 failed)", kind: "test suite" },
  contrast: { id: "contrast", value: "0", label: "contrast failures; 910 text nodes measured in each of 3 themes", source: "EVIDENCE.md §4 (Contrast audit)", kind: "real browser" },
} satisfies Record<string, Claim>;

// Statements (no number) the video makes, with where they are backed.
export const STATEMENTS = [
  ["The model chooses WHICH allowlisted fix; the code decides HOW.", "EVIDENCE.md §1.1 (Diagnosis row) + §3.1 (build_fix_spec: parameters from validation events, never the diagnosis)"],
  ["Stage 2 never sees the raw findings, so it cannot invent numbers.", "EVIDENCE.md §3.1 (generate_prose takes only grounded claims)"],
  ["A language model is called only where language is needed: diagnosis, query, modeling intent, narrative.", "EVIDENCE.md §1.1 (Uses an LLM? column); Session Summary prose: §5.5"],
  ["No model configured? Reports fall back to a labelled template.", "EVIDENCE.md §5.3 (Degrade gracefully)"],
  ["No scheduler — runs start on ingest.", "EVIDENCE.md §6.12"],
  ["A new domain = roles + rules + a view.", "EVIDENCE.md §5.2 and §5.6 (pack-local surface: rule pack, router, page; roles in analytics/roles.py)"],
  ["PII is masked before an outbound model call; the egress trail records shape only.", "EVIDENCE.md §1.3 (egress_events: shape only) + §4 (PII detection tiers, egress leak scan)"],
] as const;
