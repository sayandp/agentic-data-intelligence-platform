# Evidence dossier

Source material for the project report. **This is not the report.** Every
entry carries provenance: a file path, a commit hash, a test name, or the
script that produced the number. Anything that could not be sourced is
marked `UNVERIFIED` rather than stated.

Compiled at commit `cf27f66` (2026-08-16). Counts were produced by the
commands shown, on that commit.

> **Read section 6 before quoting anything.** Several figures are weaker
> than they look, and some things you may have been assuming turn out to
> be wrong. Those are called out in-place.
>
> **One of this dossier's own claims has since been retracted** — see the
> summary list at the end, item 2. The three-connector equivalence test
> does exist; the earlier entry saying it did not was my error.

---

## 1. System inventory

### 1.1 Agents

Eight agents write an `AgentTrace` row. Verified by
`grep -rho 'agent_name="[a-z_]*"' backend/app/`, which additionally returns
four non-agent trace writers (`gate`, `correlation`, `resolve`,
`await_human`, `baseline_profiler`) — those are pipeline stages that record
decisions, not agents.

| Agent | Module | Uses an LLM? | What decides its output |
| --- | --- | --- | --- |
| Ingestion / connector | `app/connectors/` | No | Deterministic |
| Data-Quality (validation) | `app/validation/` | No | Rule engine |
| Diagnosis | `app/diagnosis/agent.py` | **Yes** | Model chooses WHICH allowlisted action; parameters are derived deterministically |
| Exploration | `app/exploration/` | No | Closed-form statistics |
| Narrative | `app/narrative/agent.py` | **Yes** | Two-stage; falls back to a deterministic template |
| Query | `app/query/agent.py` | **Yes** | Model writes code; validated and sandboxed before execution |
| Modeling | `app/modeling/agent.py` | **Yes** | Model classifies intent only; model selection is deterministic |
| Business Analytics | `app/analytics/` | No | Seven named analyses |
| Marketing | `app/marketing/` | No | Rule pack, `34a98e2` |

**Four of nine use an LLM** (diagnosis, narrative, query, modeling intent).
Verified by `grep -rln "llm_client" backend/app/` — the other hits are
`app/llm/*` (the clients themselves), `*/dependency.py` (FastAPI wiring) and
`*/models.py` (response schemas), not decision paths.

The Marketing Agent is the eighth *agent* in the sense the report uses
(persists findings, writes a trace, runs as a graph node) — the count of
nine rows above includes the connector layer, which is not usually counted.
**Be precise about which count you quote.**

### 1.2 Endpoints

26 routes across 13 routers. Verified by
`grep -o '@router\.\(get\|post\|delete\)' backend/app/routers/*.py`:
16 GET, 9 POST, 1 DELETE.

```
GET    /runs                          POST /sources
GET    /sources                       POST /sources/upload
GET    /sources/{id}                  POST /sources/{id}/baseline
GET    /sources/{id}/runs             POST /ingest/{source_id}
GET    /ingest/{run_id}/status        POST /ask
GET    /findings/{run_id}             POST /predict
GET    /reports/{run_id}              POST /approvals/{item_id}/resolve
GET    /audit/{run_id}                POST /analytics/{run_id}/confirmed-roles
GET    /runs/{run_id}/audit-trail     POST /export/{run_id}/pptx
GET    /approvals/pending             DELETE /analytics/{run_id}/confirmed-roles/{role}
GET    /models/{run_id}
GET    /analytics/{run_id}
GET    /marketing/{run_id}
GET    /export/{run_id}/pptx
GET    /export/{run_id}/pptx/status
GET    /export/{run_id}/pptx/contents
```

### 1.3 Database tables

13 tables. Verified by `grep __tablename__ backend/app/models.py`:

`agent_traces`, `baselines`, `business_analyses`, `confirmed_column_roles`,
`data_sources`, `egress_events`, `exploration_findings`,
`marketing_analyses`, `model_runs`, `query_runs`, `reports`, `runs`,
`validation_events`.

`egress_events` is one row per outbound model call, recorded as shape only
(README §"The egress trail"). `confirmed_column_roles` also gained `decision`
and `confirmed_by` for the privacy decision flow, and now holds two kinds of
row: semantic-role confirmations, and `pii:`-prefixed privacy decisions that
every semantic-role reader filters out explicitly.

### 1.4 Dashboard views

9 routes plus a redirect, from `frontend/src/App.tsx`:
`/sources`, `/approvals`, `/reports`, `/audit`, `/ask`, `/predict`,
`/analytics`, `/marketing`, `/export` (`/` redirects to `/sources`).

### 1.5 Test counts

```
cd backend  && .venv/Scripts/python.exe -m pytest --collect-only -q
   -> 873 tests collected            (63 test files)
      (789 when this document was written; +84 from the privacy layer
       and the egress audit trail)

cd frontend && npx playwright test --list
   -> Total: 144 tests in 12 files   (48 specs x 3 themes)
      (111 when this document was written; +33 from marketing-upload,
       privacy and egress-audit, each x3 themes)
```

The E2E total is 135 because `playwright.config.ts` defines three projects
(default / dark / aurora) and every spec runs once per theme.

**Full-suite pass, most recent run:** 874 backend passed (4m38s); 144 E2E
passed (16m24s) across all three themes, as one batch.

Caveat on how that number is obtained: the backend suite cannot be run
concurrently with itself. `tests/conftest.py` hard-codes one SQLite path and
its autouse fixture calls `drop_all`, so two pytest processes delete each
other's tables and produce ~40 fixture errors and ~10 failures spread across
unrelated files. That looks exactly like a product regression and is not one -
it happened twice while assembling this document. Run the suite once at a
time. (README §Known issues.)

---

## 2. The silent-fallback catalogue

The pattern: *a fallback that cannot fail converts an error into silently
wrong output.* Your estimate was "roughly eleven including tooling."

**Verified count: 13** — 8 product, 5 tooling. Two more than you have been
assuming. Listed with provenance below; where a commit is not named, the
mechanism is documented in the README rather than in a discrete commit, and
that is stated.

### 2.1 Product (8)

| # | Mechanism | How found | Fix | Commit |
| --- | --- | --- | --- | --- |
| P1 | `except: pass` around chart rendering meant a failed chart produced an empty panel, indistinguishable from "no chart applies" | Blank dashboard investigation | Failure surfaced; charts capped and sampled | `7080d23` |
| P2 | 43MB report payloads — full scatter data serialised into `chart_refs`, browser silently stalled | Same investigation | `MAX_SCATTER_POINTS=5000`, deterministic stride sampling, sampling stated in the title | `7080d23` |
| P3 | 37s status poll: `_serialize_run_response` rebuilt the whole repaired frame to compute metadata | Same investigation | Cached on `Run.contract_metadata` | `7080d23`, invalidation added `8b9ef3e` |
| P4 | Encoding detection sampled only the first N bytes, so a latin-1 character later in the file decoded wrongly and silently | Ingestion failure investigation | Sample scope widened | `1b27482` |
| P5 | Any-negative rule rejected a monetary column over 5 rows in 1,067,371, taking four analyses down with no statement of why | Online Retail II run | Configurable negative fraction (5% default), count recorded on the detection | `1b27482` |
| P6 | Monetary role could not distinguish a unit price from a line total; Pareto silently ranked by unit price | Band A read as `Manual` / `AMAZON FEE` | `app/analytics/value_basis.py` resolves once, reported with every figure | `d038c35` |
| P7 | `explore_node` marks a run `completed` before `narrate_node` writes the report, so the UI showed "completed" and "no report" together | Screenshot of a contradictory Reports page | Race stated as a wait, not a contradiction | `c9b8089` |
| P8 | `--chart-1..6` **were the four status colours**, so a data series could render in the semantic vocabulary reserved for verdicts | Chart-colour work | Three separate palettes; status structurally unreachable from the chart layer | `cf3a8d4` |

### 2.2 Tooling (5)

| # | Mechanism | How found | Fix | Commit |
| --- | --- | --- | --- | --- |
| T1 | Root `tsconfig.json` is solution-style (`"files": []` + references), so `tsc --noEmit` type-checked **nothing** and exited 0. Two real type errors shipped behind it | Investigating a blank page after a revert | `npm run typecheck` = `tsc -b --force`; warning comment in the root tsconfig | `8b9ef3e` |
| T2 | `e2e/` was type-checked by **nothing** — `tsconfig.app.json` includes only `src`. A spec referencing an undefined helper compiled clean | Adding `requireRun` to the marketing spec | `tsconfig.e2e.json` added to project references; surfaced 5 pre-existing type errors, all fixed | `cf27f66` |
| T3 | Contrast audit measured a **loading shell** — it sampled before data rendered, so it was auditing an empty page and reporting a pass | Contrast-audit hardening | Node-count floor + cross-theme identity assertion | Documented in README; no discrete commit isolates it |
| T4 | Palette CVD probe compared colours that were never adjacent, so a pair 5.8 apart under deuteranopia passed | Palette selection | Combinatorial search over designed candidate pools with each theme's accent pinned | `cf3a8d4` |
| T5 | `marketing.spec.ts` called `test.skip` when its fixture was absent — green over zero assertions on a fresh database | Reviewing the spec after writing it | Explicit failure naming the missing fixture; `seed-demo.ps1` seeds both cases | `cf27f66` |

### 2.3 Near-misses worth mentioning

Not bugs that shipped, but the same shape, caught before commit:

- **Plotly binary serialisation.** numpy arrays serialise as
  `{dtype, bdata}`, so `len(x)` returns 2 regardless of point count. A test
  asserting a sampling cap would have passed on any input. Fixed with a
  `_point_count()` decoder before the test was committed.
- **`pd.NA` in a float64 column.** `_safe_divide` originally used `pd.NA`,
  which propagates as object dtype and makes `float()` raise. Caught by
  `test_an_adset_with_zero_conversions_has_no_cpa_and_says_so`.

---

## 3. The design principle and its instances

*"Prefer a structure that cannot hold an invalid value over a check that
must catch it."*

### 3.1 Implemented (verified in code)

| Instance | Location | What is structurally impossible |
| --- | --- | --- |
| Closed `FixAction` enum | `app/diagnosis/models.py:29-35` | An LLM cannot name an action outside the five members; the response schema will not parse |
| Global allowlist | `app/actions.py:20-23` | Only 4 of the 5 actions are auto-applicable; `ESCALATE` is not in `ALLOWLIST_ACTIONS` |
| Applicability matrix derived from the firing rule | `app/gate.py:33-44` | `permitted_actions()` returns `set()` by default — "everything not listed here permits nothing." A dropped column cannot become auto-fixable by a model asserting it should be |
| Executor parameters from validation events | `app/gate.py:46-60` (`build_fix_spec`) | Fix parameters come from `ValidationEvent` data, never `diagnosis.suggested_fix.parameters`. Module docstring in `app/actions.py:1-10` states this |
| Secret-incapable config schema | `app/connectors/credentials.py` | `connection_config` stores an env var *name*; there is no field a secret value could occupy, and `GET /sources` returns the config verbatim |
| `TimeSeriesSplit` has no shuffle option | `app/modeling/automl.py:301` | Temporal leakage is not a check that can be forgotten — the splitter sklearn provides cannot shuffle |
| Stage 2 never receives the findings | `app/narrative/agent.py:222` | `generate_prose(self, claims)` takes only `list[GroundedClaim]`. Prose cannot cite a finding that was not already grounded, because it never sees one |
| Marketing severity declared as data | `app/marketing/findings.py` (`SEVERITY_OF`) | A rule cannot emit itself at a severity the escalation path was not built for |
| `ThresholdBreachPayload.finding_type` is a 6-member `Literal` | `app/marketing/findings.py` | `ACCOUNT_TOTALS` cannot be emitted through the breach shape |
| Chart colour roles have no status member | `app/analytics/chart_specs.py` | The role vocabulary is `accent` / `neutral` / `categorical` / sequential scale. There is no role that maps to a status token, so a chart cannot assert a verdict in colour |

### 3.2 Considered and NOT applied, with the reason

- **Marketing thresholds are configuration, not types.** A `float` field can
  hold a nonsensical threshold (negative CPA multiple). Making them
  constrained types was rejected: the values are operator-tunable and the
  finding reports the threshold it used, so a wrong value is visible in the
  output rather than silently applied. *Structure would have cost the
  legibility the feature is for.*
- **`MarketingConfig.required_roles` is a tuple, not a closed set of valid
  combinations.** An operator can configure a role set that qualifies
  nothing. Rejected for the same reason — and the refusal names what was
  missing, so a mis-configuration is self-evident.
- **The value basis is resolved once but not type-enforced.** Nothing stops
  a future analysis reading the monetary column directly instead of via
  `value_basis`. This is convention, not structure. **A genuine gap in the
  principle's application** — worth saying so in the report.

---

## 4. Measured results, with provenance

| Figure | Value | Produced by | Live or fake? |
| --- | --- | --- | --- |
| Diagnosis accuracy | **11/12 correct** | `scripts/part6_evaluation.py` | **Live** Gemini |
| Wrongly auto-fixed (that run) | **0** | `scripts/part6_evaluation.py` | Live |
| Wrongly auto-fixed across sweep | **0 at all 10 thresholds** | `scripts/part6_wrongly_auto_fixed_replay.py` | **Cache replay**, `PoisonPillClient` — 0 cache misses, 0 live calls |
| Post-condition reverts | **2 of 6 auto-apply attempts** reverted (`auto_fix_reverted_by_verification`) | `scripts/part6_wrongly_auto_fixed_replay.py` | Cache replay |
| Live confidence distribution | `0.95, 0.95, 0.95, 0.95, 0.95, 0.90, 0.95, 0.95, 0.98, 0.95, 0.95` — 11 values, 0.08-wide band | `scripts/part6_evaluation.py` | **Live** |
| Threshold sweep shape | Flat `t=0.5`→`0.9`, cliff at `t=0.95` where 9 diagnoses at exactly 0.95 fail `confidence <= threshold` (`app/gate.py:100-103` at `cf27f66`; the README cites line 95, which is stale) | Same run | Live |
| Online Retail II rows | **1,067,371** | README §Phase 7.5; negatives were 5 rows = 0.0005% | Real dataset |
| Band A before value-basis fix | led by `Manual`, `AMAZON FEE` | README §"What value means" | Real dataset |
| Band A after `Price × Quantity` | led by `REGENCY CAKESTAND 3 TIER` | Same | Real dataset |
| Contrast audit | **0 failures; 910 text nodes measured identically in each of 3 themes** | `frontend/scripts/contrast-audit.mjs` | Real browser |
| Three-connector equivalence | Identical validation outcome across file / SQL / API; stable over 5 repeats | `tests/test_three_connector_equivalence.py` | Real CSV, real SQLite, real loopback HTTP server; fake diagnosis client |
| Redaction cost to diagnosis | **none** — 3/3 failure groups produced the same cause and the same confidence with redaction on and off | `backend/scripts/privacy_diagnosis_cost.py` | **Live** Gemini, `source llm` (not cache) |
| Redaction cost to narrative | **material** — 5 claims / 574 chars clear vs 4 claims / 275 chars redacted (−52%); the Band A value-concentration finding is lost entirely | `backend/scripts/privacy_narrative_cost.py` | **Live** Gemini, `source llm`, analytics findings included |
| PII detection tiers | 5 high-confidence kinds auto-classified; 3 low-confidence kinds never auto-classified | `backend/app/privacy/detectors.py`, `tests/test_privacy.py` (61 tests) | Deterministic, no LLM |
| Egress trail leak scan | **0 data values and 0 redaction tokens** in any stored record or any /audit response, across a run exercising all four outbound paths | `tests/test_egress_audit.py::test_no_record_anywhere_contains_a_data_value`, `frontend/e2e/egress-audit.spec.ts` | Fake clients (backend), live Gemini (E2E) |
| Corruption harness | 7 injector kinds: rename, dtype, nulls, drop, distribution shift, whitespace/case, truncate | `backend/tests/corruption/suite.py` | Deterministic, seeded |

### Figures I could NOT verify — do not quote without checking

- **`UNVERIFIED`: a per-corruption-kind detection rate.** The corruption
  suite defines 7 injectors and `golden_scenarios.py` defines 8 scenarios,
  but I found no script that reports a detection *rate* per kind. The
  11/12 figure is diagnosis correctness on 12 correlated groups, which is
  **not** the same measurement. If you have been quoting a "detection rate",
  it needs re-deriving or dropping.
- **~~`UNVERIFIED`: three-connector equivalence~~ — RETRACTED. This entry
  was WRONG.** An earlier revision of this dossier stated no such test
  existed. It does, and has since the initial commit `3a8a3b3`:
  `backend/tests/test_three_connector_equivalence.py`. The error was mine —
  I searched the README for a *description* of the result instead of
  searching the test suite for the test. Corrected at commit `5513055`
  (see §4.1 for what the test now demonstrates).

### Stale figures

- **The 30.8s `GET /ingest/{run_id}/status` measurement is historical.**
  It was fixed by the `contract_metadata` cache (`7080d23`, invalidation
  `8b9ef3e`). The README table is now explicitly marked FIXED as of
  `cf27f66`. Quote it as "was", never as current behaviour.

---

## 5. Architectural claims and their evidence

### 5.1 Source-agnosticism

- **Evidence, architectural:** one `BaseConnector` contract with three
  implementations (`file`, `sql`, `api`); `DataContract` is what every
  downstream agent consumes; `repaired_contract_for_run` is the single
  rebuild path.
- **Evidence, empirical:** `backend/tests/test_three_connector_equivalence.py`
  — two tests:
  - `test_three_connectors_produce_equivalent_validation_events`
  - `test_the_equivalence_is_stable_across_repeated_runs`

  One dataset (60 rows: int, categorical, float, datetime), one corruption
  (`rename_column`, `city` → `town`), one seed (42), loaded through **real**
  sources: a CSV on disk, a SQLite table via `SQLConnector`, and a real
  loopback HTTP server via `APIConnector` — not a mocked transport.

  **Asserts sameness:** the sorted set of `(rule family, column, risk level,
  action taken)` validation events is identical across all three; all three
  reach the same terminal run state; all three converge on the same
  `column_types`, including resolving `ordered_at` to `datetime64` — which
  the file and API paths reach by parse-rate inference over text and the SQL
  path from the column's declared type. Stable across 5 repeated runs, each
  in a fresh directory.

  **Asserts difference:** `source_type` differs; the file connector reports
  `detected_encoding` and `encoding_confidence`, SQL reports a non-empty
  `declared_schema`, API reports `pages_fetched >= 1` — and each of those
  markers is asserted **absent** from the other two. Without that half, three
  connectors silently degrading to the same empty contract would pass.

  **Falsified:** disabling datetime normalisation in `SQLConnector` makes it
  fail with `connector(s) ['sql'] did not reach the same terminal state as
  the others. all three: {'file': 'completed', 'sql': 'awaiting_approval',
  'api': 'completed'}`. Restoring returns it to green.

- **Does NOT demonstrate:** equivalence for *all* corruption kinds, or for
  sources beyond these three. It covers **one corruption kind (rename)
  through three connectors**, not the harness's seven kinds through all
  sources. It also does not demonstrate equivalence under a SQL dialect
  other than SQLite — `SQLConnector`'s docstring notes SQLite stands in for
  Postgres/MySQL/MSSQL, and that substitution is untested.

- **Two things the falsification exposed, worth reporting honestly:**
  1. Before this work the fixture had **no datetime column**, so sabotaging
     SQL's declared-schema coercion changed nothing and the test passed. It
     was blind to a whole branch of the contract. The column was added.
  2. Even with the column, disabling only the *declared-schema* coercion
     still passes: parse-rate inference reaches the same dtype anyway. The
     declared-schema path is a **fast path, not the only path** — a useful
     fact about the connector that the test made visible.

### 5.2 A new domain fits as configuration plus a rule pack

- **Evidence:** the Marketing Agent (`34a98e2`). Enumerated file-change
  report shows `app/gate.py`, `app/actions.py`, `app/validation/*`,
  `app/resolution.py`, `app/state_machine.py` and every existing agent's
  logic were **not modified**. Orchestration changed by exactly one call in
  `explore_node`.
- **Does NOT demonstrate:** that *no* domain would require deeper change.
  Marketing needed one genuinely shared modification — the semantic role
  detector — plus a new shared parser (`app/numeric_text.py`) because
  detection runs before transformation. A domain whose data needed different
  *repair* would hit the validation engine, which this did not test.

### 5.3 Degrade gracefully

- **Evidence:** narrative falls back to a deterministic template when the
  LLM fails, and the mode is labelled on the report and in the deck
  (`test_no_llm_mode.py`, `test_narrative_fallback_diagnostics.py`).
  Analytics and marketing are wrapped so a failure cannot fail an ingest
  that already produced findings. One rule raising cannot sink the rest
  (`app/marketing/engine.py`).
- **Does NOT demonstrate:** graceful degradation of the *data* path. A
  connector failure fails the run — correctly, but that is refusal, not
  degradation.

### 5.4 Auditability

- **Evidence:** every agent writes an `AgentTrace` with input summary,
  output summary and edge taken; `GET /audit/{run_id}` reconstructs the
  decision path; every finding carries an evidence block with its
  parameters; every marketing finding states `compared_against`.
- **Does NOT demonstrate:** that the audit trail is *sufficient to
  reproduce* a run. Reproduction depends on the source file being unchanged,
  and `source_immutable = False` for the API connector — an API source can
  return different data between fetches and the trail would not show it.

---

## 6. Limitations, honestly

1. **The container path has never been run.** `docker-compose.yml`,
   `backend/Dockerfile` and `frontend/Dockerfile` exist and are committed.
   Nothing in this session or any recorded run has built or started them.
   `docker` is not installed on the development machine (verified: `docker`
   is not a recognised command). Treat the container path as **written but
   unexercised**.

2. **The full E2E suite has not been re-run at `cf27f66`.** It passed 111/111
   at `34a98e2`. Since then the marketing specs changed and `tsconfig.e2e.json`
   was added; the marketing specs were re-run standalone (12/12) and the
   type-check is clean, but the other 99 tests have not been re-run on the
   current commit.

3. **A known E2E flake.** `demo-full-charts.spec.ts` failed once in a full
   run and passed on immediate re-run, with no retained error context. Cause
   not diagnosed. It is not known to be deterministic.

4. **Analytics chart derivation history.** These charts were derived in the
   browser and persisted nowhere until `f6f4c7f`. The port's fidelity is
   backed by a golden capture taken from the live frontend immediately
   before deletion (`backend/tests/fixtures/analytics_charts_golden.json`)
   and a 0-difference re-render across three themes. **But the baseline was
   captured post-hoc by the same process that did the port** — it is not an
   independent oracle.

5. **Formatted-text numbers.** The repair path does not recover numbers from
   currency symbols, thousands separators or percentage strings. Mitigated
   only for the marketing path via `app/numeric_text.py`. Generic CSVs with
   formatted numeric columns still reach exploration, profiling and the
   baseline as text. Documented in README §Known issues.

6. **Derived metrics have no stored baseline.** `app/profiling.py` profiles
   raw columns at ingest. CPA, ROAS and CTR are derived *after* profiling,
   so no baseline entry for them exists or could without changing the
   profiling path. Marketing's CPA and CTR rules therefore compare against
   the run's own prior window, and say so in `compared_against`. **The
   "a CPA spike is a distribution shift the platform already detects" framing
   is not what the code does.**

7. **The applicability matrix has never caught a wrong call.** README
   line ~1395: in every sample collected, the model's own suggested action
   and risk level already agreed with the matrix. Its protective value is
   structural and guaranteed, **not empirically demonstrated by a save.**
   This is the single most important caveat in the dossier and should not be
   smoothed over.

8. **Out-of-scope analysis families.** Predictive CLV, causal inference,
   attribution modelling and uplift are deliberately absent. Documented in
   the README as excluded by design rather than stubbed. The Historical CLV
   analysis is labelled HISTORICAL for exactly this reason.

9. **No live Meta API integration.** The access check (§Part 0 of that task)
   found network egress works but there is no ad account, app, or `ads_read`
   token. The Marketing Agent consumes uploaded CSV exports only.

10. **Marketing rules fire on synthetic fixtures.** Every rule has a test
    that fires it, but on fixtures constructed to fire it. No real campaign
    export with a genuine CPA spike has been analysed.

11. **The value basis is convention, not structure** (§3.2).

---

## 7. Timeline

From `git log --format="%h|%ad|%s" --date=short`. The repository's initial
commit is a squashed baseline; phases 1–7 predate it and are documented in
the README rather than in individual commits.

| Date | Commit | Work |
| --- | --- | --- |
| 2026-08-03 | `3a8a3b3` | Initial commit — platform baseline (pre-Impeccable) |
| 2026-08-04 | `f4f729e` | Dashboard redesign, run picker, LLM failure diagnosability |
| 2026-08-04 | `b232646` | Theme infrastructure: tokens, `data-theme` swap, Default + Dark |
| 2026-08-04 | `56b017b` | Aurora theme + per-theme verification |
| 2026-08-04 | `292a9fa` | DESIGN.md drift fix, sidecar refresh |
| 2026-08-04 | `c258fd9` | Unanswerable Predict escalation made actionable |
| 2026-08-07 | `43720b6` | Business Analytics: capability detection + ABC/Pareto |
| 2026-08-08 | `2813c87` | Business Analytics: analyses 2–7, integration, UI |
| 2026-08-08 | `801eb79` | Pareto concentration flag, confirmed-role UI |
| 2026-08-09 | `1b27482` | **Remediation:** sample-scoped encoding; monetary negative rule |
| 2026-08-09 | `d038c35` | **Remediation:** line-total derivation (unit price vs total) |
| 2026-08-09 | `7080d23` | **Remediation:** blank dashboard, 43MB payloads, 37s poll |
| 2026-08-09 | `c9b8089` | **Remediation:** completed-vs-no-report race |
| 2026-08-11 | `cf3a8d4` | Chart colour: three palettes, status structurally unreachable |
| 2026-08-11 | `b7a2df3` | PPTX export: a renderer over persisted artifacts |
| 2026-08-11 | `435395c` | Deck retrievability; per-bar categorical colour |
| 2026-08-13 | `f3832db` | Deck covers the whole run; Export gets its own destination |
| 2026-08-13 | `f6f4c7f` | Analytics chart derivation moved to the backend |
| 2026-08-14 | `38fbbc1` | Semantic roles shared across every agent |
| 2026-08-15 | `8b9ef3e` | Pre-demo hardening: offline charts, real type-check, seed script |
| 2026-08-15 | `644e84c` | Typography ramp promoted to named utilities |
| 2026-08-15 | `7401ce0` | Ramp comment reworded |
| 2026-08-15 | `a0e2c70` | Cohort heatmap colour domain fitted to the data |
| 2026-08-16 | `34a98e2` | **Marketing Agent** — the eighth agent |
| 2026-08-16 | `cf27f66` | Marketing specs fail instead of skipping; `e2e/` type-checked |

**On the remediation phase.** Four consecutive commits on 2026-08-09
(`1b27482`, `d038c35`, `7080d23`, `c9b8089`) are all fixes to defects found
by running the system on a real 1.07M-row dataset rather than on fixtures.
This is the methodologically interesting part of the timeline: every one of
those defects was a silent-fallback failure that unit tests passed through,
and they were found by looking at outputs — a blank page, a 43-second wait,
a Band A list that read as bookkeeping entries. It belongs in the
methodology chapter as evidence about *how* defects surface, not as an
embarrassment to be minimised.

---

## Things that contradict what you may be assuming

1. **The silent-fallback count is 13, not ~11** (§2).
2. ~~There is no three-connector equivalence result in this repo.~~
   **RETRACTED — this was my error, not yours.** The test exists and has
   since the initial commit (§5.1). I searched the README instead of the
   test suite. It has since been strengthened and falsified.
3. **There is no per-corruption-kind detection rate** that I could find;
   11/12 is diagnosis correctness, a different measurement (§4).
4. **The 30.8s status-poll figure is fixed and historical** (§4).
5. **The applicability matrix has never actually caught a wrong model call**
   (§6.7).
6. **"A CPA spike is a distribution shift the platform already detects" is
   not what the code does** — derived metrics have no baseline (§6.6).
7. **The container path has never been run** (§6.1).
