# Agentic Data Intelligence Platform

A separate frontend (`frontend/`, React + TypeScript + Tailwind, its own
build) and backend (`backend/`, FastAPI) - the platform started as a single
server-rendered app (Phase 1-8) and was split post-release into this
structure; see "Frontend/backend split" near the end of this document for
why and what changed. Everything below the split still describes the
backend's own history phase by phase, since none of that changed - only
where the UI lives did.

## Structure

```
backend/
  app/
    contract.py             canonical DataContract (the only interface connectors expose downstream)
    models.py                SQLAlchemy models: data_sources, runs, validation_events, agent_traces, reports
    db.py                     engine/session setup, init_db()
    connectors/                file/SQL/API connectors
    routers/                    every HTTP endpoint - sources, ingest, approvals, reports, audit, query, predict
    graph/                       the LangGraph ingest orchestrator (Phase 8)
    main.py                       FastAPI app, CORS for the separate frontend, calls init_db() on startup
  tests/                       pytest suite (SQLite-backed)
  scripts/                     standalone evaluation/acceptance scripts
  requirements.txt
  Dockerfile
frontend/
  src/
    api/                       typed fetch client - the ONLY place that talks to the backend
    components/                 shared layout (sidebar nav) + UI primitives (Card, Badge, Button)
    pages/                       SourcesPage, ApprovalsPage, ReportsPage, AuditPage, AskPage
  Dockerfile                   multi-stage: npm build -> nginx serving the static output
  nginx.conf                   SPA fallback (client-side routing needs every path to serve index.html)
docker-compose.yml            db + backend + frontend, three services
```

## Quickstart (local, no Docker)

This is the supported way to run and demo the platform - it's the exact
stack (uvicorn + Vite + SQLite) that's been developed and verified against
throughout this project. No LLM key is required; see "Running without an
LLM" below for what that mode looks like.

**One-time setup:**

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cd ..\frontend
npm install
cd ..
```

**Every time you want to run it:**

```powershell
.\start.ps1
```

This activates the backend venv, starts uvicorn on `http://localhost:8000`
with a local SQLite database, starts the Vite dev server, waits for both
to come up, and prints both URLs when ready - open the dashboard URL it
prints (Vite's default is `http://localhost:5173`, but it prints whichever
port actually got used). It also prints the `[startup]` line so you can
see at a glance whether an LLM provider is active or the platform is
running in its no-LLM mode. `start.ps1` checks first that the venv exists,
dependencies are installed, `node_modules` exists, and ports 8000/5173 are
free, and fails with a one-line copy-pasteable fix for anything missing,
rather than a stack trace.

```powershell
.\stop.ps1
```

Stops both servers cleanly.

```powershell
.\reset.ps1
```

Stops both servers, deletes the local SQLite database and every
cache/artifact directory the app writes at runtime, and returns the
project to a clean demo state - run `.\start.ps1` afterward to bring it
back up from scratch.

```powershell
.\seed-demo.ps1
```

Resets, restarts, and leaves the platform **paused on exactly one
escalation** - so a live demo starts at the interesting part instead of
spending its first few minutes on setup nobody wants to watch.

It ingests a clean CSV to establish the source's provisional baseline,
then rewrites that same file with nulls past the tolerance and re-ingests
it. Both steps go through the SAME source on purpose: validation compares
a run against its source's baseline, so a separately registered file
would simply establish its own and never be checked against anything.

The corruption targets `null_threshold`, which has no auto-fix in
`app/gate.py`'s applicability matrix - the gate cannot quietly repair it,
so it always escalates. The script asserts there is exactly one pending
escalation before declaring itself ready, and exits non-zero if not: a
demo that silently seeds the wrong state is worse than one that refuses
to start.

`-SkipReset` seeds on top of the current database instead of wiping it.

## Run with Docker Compose

An alternative deployment path - Postgres instead of SQLite, both
services containerized. Not required to run or demo the platform; see
"Quickstart" above for the supported local path.

```bash
docker compose up --build
```

This starts Postgres 16, the API on `http://localhost:8000`, and the
frontend on `http://localhost:3000` - open the frontend URL, not the API
one. The backend container mounts `./backend/data` (host) to `/data`
(container), so drop a CSV in `./backend/data/` and reference it as
`/data/your_file.csv` in requests below, or just use the frontend's Upload
button (see "Frontend/backend split" for why a path-based request and a
browser upload both work).

### Running without an LLM

**No LLM is required to run this platform.** `docker compose up` alone,
with no key configured, is a fully supported, first-class mode - not a
degraded one. Ingest works, validation still detects every failure, and
every detected failure escalates to human review instead of being
auto-fixed; exploration still runs and reports still generate, from the
deterministic template rather than an LLM-authored narrative. This is the
same "degrade rather than fail" guarantee the Narrative/Query/Modeling
Agents have always had (Phases 5-7) - the Diagnostic Agent now has it too:
construction failure (no key, no reachable provider) degrades to `None`
the same way, and every validation event that would have been diagnosed
escalates cleanly instead of the request failing. Check the startup log -
the app prints `[startup] LLM provider active: ...` or
`[startup] No LLM configured...` once, at boot, so the operating mode is
visible immediately rather than inferred from behaviour three requests in.

**To configure one:** `cp .env.example .env` and fill in `GEMINI_API_KEY`,
then `docker compose up --build` again - `docker-compose.yml` reads `.env`
for this value automatically via its own `${VAR:-default}` substitution,
no extra step needed. Gemini is the only LLM provider this deployment
runs; `LLM_PROVIDER` accepts no other value (see
`app/llm/factory.py::get_llm_client`).

**Hitting the free tier's quota (429)?** The free tier caps at 20
requests/day, per project, per model - easy to hit during testing. Set
`GEMINI_API_KEYS` instead (comma-separated, see `.env.example`) with keys
from more than one Google Cloud project; `GeminiClient` rotates to the next
key automatically on a 429 and only fails once every configured key is
exhausted. `LLM_PROVIDER=gemini`'s single-key `GEMINI_API_KEY` still works
unchanged if you only have one.

### Register a file source and ingest it

```bash
# copy an Olist CSV into ./data first, e.g. ./data/olist_orders.csv

curl -X POST http://localhost:8000/sources \
  -H "Content-Type: application/json" \
  -d '{"type": "file", "connection_config": {"path": "/data/olist_orders.csv"}}'
# -> {"id": "b3f1..."}

curl -X POST http://localhost:8000/ingest/b3f1...
# -> {"run_id": "...", "status": "completed", "metadata": {"source_type": "file", "source_id": "b3f1...", "ingestion_timestamp": "...", "row_count": 99441, "column_types": {...}}}
```

**Validation is on demand.** That `POST /ingest` is the only thing that starts
a run (a human resolving an escalation resumes a paused one; there is no third
way). The platform does not watch a source, poll it, or re-ingest it on a
cadence - there is no scheduler here. Drift detection is real and works across
repeated ingests of the same source, comparing each against the baseline an
earlier one established, but **you trigger each of those ingests.** Nothing
notices that a file changed on disk.


Check the results directly in Postgres:

```bash
docker compose exec db psql -U postgres -d agentic_platform \
  -c "select id, status, started_at, completed_at from runs;"
docker compose exec db psql -U postgres -d agentic_platform \
  -c "select run_id, agent_name, output_summary from agent_traces;"
```

## Run locally without Docker

Backend:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows / $env:DATABASE_URL=... in PowerShell
pip install -r requirements.txt

# point at a local Postgres, or copy .env.example (project root) to .env and adjust
export DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/agentic_platform

uvicorn app.main:app --reload
```

Frontend, in a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173` (Vite's dev server - `frontend/.env`'s
`VITE_API_BASE_URL=http://localhost:8000` already points it at the backend
above; `app/main.py`'s CORS middleware already allows that origin by
default). Or skip the frontend entirely and use the same curl commands
shown above (against `localhost:8000`), with `connection_config.path`
pointing at a CSV anywhere on your local filesystem. Either way: no LLM key
is required for any of this to work (see "Running without an LLM");
`load_dotenv()` at `app/main.py`'s top picks up a `.env` in the working
directory automatically if one exists.

## Tests

**The suite is safe to run concurrently with itself**, and this is not
incidental. `tests/conftest.py` puts the process id in both store filenames
(`agentic_platform_test_<pid>.db` and its checkpoint counterpart), and a
session-scoped fixture deletes them when the run ends.

It used to hard-code one name for each. Since the autouse fixture calls
`drop_all` before every test, a second pytest process — a `--collect-only`
alongside a full run, a second terminal, a file watcher — deleted the first
one's tables mid-test. What that produced was not a lock error: it was ~40
fixture errors and ~10 failures scattered across unrelated files, in a suite
that passed clean when run alone. It was reported as a product regression
three separate times before the cause was found. A name that cannot collide
needs no check that must catch a collision.

Cleanup closes both connections before unlinking. On Windows an open sqlite
handle holds an OS-level file lock, so the first version of the fixture
silently left every run's checkpoint file behind.


```bash
cd backend
pip install -r requirements.txt
pytest
```

Tests run against a temp-file SQLite database (no Postgres required) via a
`DATABASE_URL` override set in `tests/conftest.py`, and cover:

- `DataContract.metadata()` correctness
- CSV encoding/delimiter auto-detection, including a semicolon-delimited file
  and a latin-1 encoded file
- unsupported file extensions raising a clear error
- both endpoints end-to-end via `TestClient`

### UI verification (real browser, real backend)

Every other check above talks to the API directly - `TestClient`, curl,
PowerShell. None of that can catch "the backend resolved it but the UI never
noticed," a button wired to the wrong handler, or a chart that silently fails
to render. `frontend/e2e/dashboard-flow.spec.ts` (Playwright) drives the
actual dashboard in a real headless Chromium browser against the actual
running local stack - not a mock, not the Streamlit test console below -
through the full human flow: register a source, ingest, confirm a
provisional baseline, force an escalation and resolve it, then check the
Reports and Audit pages, all through real clicks and real page state, never
a manual reload.

```powershell
.\start.ps1              # backend + dashboard must already be running
cd frontend
npm i -D @playwright/test && npx playwright install chromium   # one-time
npm run test:e2e
```

### Manual test console (Streamlit)

`tools/test_console.py` is a Streamlit page for poking the running backend by
hand: register, upload and ingest a source, resolve approvals, inspect reports
and the audit trail, check that the Gemini key is actually live (not just
present in `.env`), and run the backend test suite on demand. It talks to the
same HTTP API the React dashboard uses. It is a debugging aid, not a
replacement for the dashboard, and no automated test depends on it.

```powershell
.\start.ps1              # the backend must already be running
backend\.venv\Scripts\python.exe -m streamlit run tools\test_console.py
```

It opens on http://localhost:8501. Its two dependencies, `streamlit` and
`requests`, are listed in `tools/requirements.txt` and are already present in
`backend\.venv`.

### Type-checking the frontend

```bash
cd frontend
npm run typecheck        # tsc -b --force
npm run verify           # typecheck + lint + e2e
```

Use one of those, **not** `tsc --noEmit`. The root `tsconfig.json` is
solution-style (`"files": []` plus project references), so running `tsc`
against it type-checks *nothing* and exits 0 no matter how broken the code
is - two real type errors reached the working tree behind exactly that
invocation. `tsc -b` follows the references and checks the actual projects.

## What's not here yet (by design)

- SQL and API connectors (`BaseConnector` is ready for them)
- Data-Quality Agent / validation rules — see the `TODO` in `app/routers/ingest.py`
- Any LLM calls or analytics/report generation

## Phase 2 methodology notes

**Validation engine implementation: native pandas/numpy, not Great Expectations.**
GE (latest 0.18.22) has no published wheels for Python 3.14 and pip falls back
to building an old pinned numpy from source, which fails without a C compiler.
This is a Python-version problem, not a GE problem — GE installs cleanly under
3.11/3.12. We evaluated downgrading the interpreter specifically to keep GE,
but decided against it: the rewrite (GE's expectation-suite API instead of raw
pandas/numpy, re-verifying several pandas-3.0-specific behaviours the native
implementation already accounts for — e.g. `.astype(str)` producing a `str`
extension dtype rather than `object`, silently round-tripping back to a numeric
dtype through a CSV write/read) is substantial rework immediately before the
Diagnostic Agent and Action Executor, the highest-risk remaining phases. The
four rule families (schema conformance, null thresholds, PSI/KS distribution
drift, encoding integrity) and the fifth (categorical drift) are implemented
directly against pandas/numpy/scipy in `app/validation/engine.py`. This is a
deliberate substitution, not a silent one.

**Baseline sanity floors are guarded below 10 rows.** "Categorical cardinality
== row count" is trivially true for almost any small sample (3 rows, 3 distinct
city names isn't an identifier signal, just too little data) so that floor is
skipped under `MIN_ROWS_FOR_CARDINALITY_FLOOR` (`app/baseline_sanity.py`) rather
than firing on every small dataset, including most unit-test fixtures.

**A dtype change that's a mechanical consequence of another already-detected
failure on the same column is folded into that failure, not reported as its
own event.** Inserting nulls into an `int64` column forces a `float64` upcast
(NaN has no int representation); scaling/offsetting an `int64` column by a
float factor does the same via numpy type promotion. Neither is an independent
schema problem, so `ValidationEngine._fold_derived_dtype_mismatches` merges the
dtype note into the `null_threshold` or `distribution_drift` event it's
actually caused by. Left unfixed, this both doubled Diagnostic Agent calls
(Part 4) against a free-tier per-minute cap and could produce a spurious
`safe_type_cast` auto-fix sitting next to a correctly-escalated null flood.

**The gate's applicability matrix (`app/gate.py`), not the LLM's self-reported
risk_level/confidence, is what makes auto-fix safe.** A diagnosis can report
`action=safe_type_cast, risk_level=low, confidence=0.99` for a genuinely
dropped column - every field the model controls can look perfect while being
wrong. The matrix is derived entirely from `ValidationEngine`/correlation
output (which rule family actually fired, and whether the missing/unexpected
columns correlated as a rename pair) and is checked *before* risk_level or
confidence are even consulted; failing the matrix escalates regardless of what
the model claims. Four of the eight rule families this system detects
(`null_threshold`, `distribution_drift`, `schema_conformance:row_count_drop`,
both `encoding_*` checks) permit **no** auto-fix at all, for any diagnosis,
under any confidence - a null flood or a distribution shift is a question
about what the data *means*, and no allowlisted mechanical transformation
(rename/cast/strip/case-normalize) answers that. The system doesn't attempt
one. That a majority of detected corruption classes are auto-fix-ineligible by
construction is a stronger accountability claim than a high auto-fix rate
would be.

**Every auto-applied (or human-approved) fix is verified, not assumed.** After
`apply_action`, the specific failed expectation(s) are re-run against the
repaired frame (`app/repair.py::attempt_fix`); only a fix that actually clears
its own check gets committed (`action_taken="auto_fixed"`). One that doesn't -
e.g. `strip_whitespace` suggested for a case-only corruption, which it cannot
resolve - is reverted via the stored reversal record and escalated
(`action_taken="auto_fix_reverted"`), with the post-condition failure recorded
alongside the original diagnosis. This is what makes the reversal records
load-bearing rather than decorative: `test_repair.py` asserts the reverted
frame is byte-identical (`pd.testing.assert_frame_equal`) to the pre-fix
frame, not just "close enough."

**Repaired data propagates as a replayable recipe, not a mutated copy.** A
run's committed fixes are persisted as `Run.fix_chain` - an ordered list of
`{action, spec}` - rather than a second copy of the DataFrame.
`app/repair.py::apply_fix_chain` reconstructs "the repaired frame" on demand
by replaying that chain against a fresh fetch from the source connector. This
is also what a human approval does: fetch fresh, replay the run's
already-committed chain, apply the newly-approved fix on top, verify, commit.
Auditable and reproducible, with no duplicate storage.

**Gate confidence threshold defaults to 0.8, via `GATE_CONFIDENCE_THRESHOLD`.**
Part 6's sweep (0.5–0.95, `scripts/part6_evaluation.py`) confirms zero
wrongly-auto-fixed corruptions at every threshold tested, with the auto-fix
rate degrading above ~0.85 as the fake evaluator's own confidence values fall
below the bar. Diagnosis is threshold-independent, so the sweep runs the LLM
call exactly once per (rule, column, detail) signature and replays the cached
result through `evaluate_gate`/`attempt_fix` for each threshold - re-running
`diagnose_once` a second time makes zero additional calls, which the script
asserts directly rather than just claiming.

**`pandas.read_excel()` does not respect a cell's stored Text format.** A
genuine "numbers stored as text" Excel corruption - a numeric column whose
cells are explicitly Text-typed (`data_type='s'`, `number_format='@'`),
confirmed via raw `openpyxl` to survive a save/reload with that type intact -
still gets silently re-inferred as `float64` by `pd.read_excel()` with default
settings, unless `dtype=str` is passed for that column. `FileConnector` does
not pass it, so as things stand this corruption is currently invisible to the
pipeline: pandas "repairs" it before any validation rule runs, for the same
underlying reason numeric-looking CSV text does (Part 0's earlier finding) -
except here Excel's own cell-level type metadata is right there, unused. We
deliberately did not change `FileConnector` for this (out of scope, touches
every existing Excel ingest path) - `scripts/part6_evaluation.py` demonstrates
`safe_type_cast`'s actual happy path by reading that one file with `dtype=str`
forced at the script level, bypassing `FileConnector`, with the bypass
documented at the call site. The CSV-safe `change_dtype` corruption (`$`-prefixed,
Part 0) is unaffected by any of this and continues to correctly fail the cast
and escalate.

## Design principle: make the bad state unrepresentable, not just checked-for

A single principle recurs across every layer that has to resist either a
malicious or simply-wrong input, and it's worth naming rather than leaving
implicit: **prefer a structure that cannot hold the invalid value over a check
that has to catch it every time.** A check is something you can forget to run,
run in the wrong order, or have quietly bypassed by a new code path; a type
that cannot represent the bad value has no such failure mode. Four instances,
found incrementally rather than planned upfront:

1. **`suggested_fix.action` is a closed `Enum` (`app/diagnosis/models.py`)**,
   not a string. A malformed or injected action name isn't rejected by a
   check - it's rejected by `FixAction("delete_everything")` raising before
   any decision logic even runs. There is no string-matching branch to get
   wrong.
2. **The gate's applicability matrix is derived from which rule fired
   (`app/gate.py::permitted_actions`), never from the diagnosis.** A wrong
   diagnosis (`action=safe_type_cast, risk_level=low, confidence=0.99` for a
   `drop_column`) has nowhere to grant itself permission from - the matrix
   answers "what's possible here" before the diagnosis's risk_level or
   confidence are even read.
3. **Executor parameters come from validation-event data, never from
   `diagnosis.suggested_fix.parameters`** (`app/gate.py::build_fix_spec`). The
   rename target, the cast's dtype, the affected column - all are read off
   the `ValidationFailure` the correlator already produced. The diagnosis
   payload has no channel through which a bad parameter could reach the
   executor, whether the model hallucinated it or a prompt injection tried to
   supply it.
4. **`connection_config`'s schema has no field capable of holding a secret**
   (`app/connectors/credentials.py`, the `*_env` fields on `AuthConfig` /
   `SQLConnectorConfig`). There's no redaction step on `GET /sources` because
   there's nothing to redact - the Pydantic models that produce
   `connection_config` only ever accept an environment variable *name*.

Each of these replaces a check that could fail (validate the string, trust
the risk_level, sanitize the parameter, filter the secret) with a structure
that can't hold the bad value in the first place. None of the four was
designed this way from the start - each came from tracing a specific failure
mode (a prompt-injected action, a drop_column mis-diagnosed as safe, a
malicious `row_limit`, an accidentally-stored token) back to its structural
fix.

## Phase 4: the Exploration Agent

Phase 4 scope is the Exploration Agent only - no Query, Modeling, or
Narrative agent, no orchestrator. It runs `app/exploration/engine.py`
(deterministic - no LLM anywhere in this phase) against a run's REPAIRED
frame once every validation event has resolved, and persists a structured
`ExplorationFindings` object (`app/exploration/findings.py`) to the new
`exploration_findings` table, retrievable via `GET /findings/{run_id}`.

```
app/exploration/
  findings.py            Pydantic output contract - closed enums, discriminated payload unions
  config.py               every threshold/cap, all configurable (mirrors ValidationEngine's pattern)
  columns.py              shared numeric/categorical/datetime column-kind inference
  stats.py                 summary statistics + cardinality_note
  correlation_analysis.py  Pearson/Spearman between numeric column pairs
  outliers.py              IQR-based outlier clusters
  trend.py                  datetime x numeric trend fit + basic seasonality detection
  distribution.py          normality/skew/modality per numeric column
  missingness.py           co-occurring / one-directional null patterns
  quality_context.py       builds DataQualityContext from a run's validation_events
  engine.py                 orchestrates all of the above, applies the row/column caps
  pipeline.py               persistence: writes exploration_findings + an AgentTrace row
app/routers/findings.py    GET /findings/{run_id}
```

**`ExplorationFindings` is the second major interface in this project, after
`DataContract`, and gets the same treatment.** Two unbuilt agents (Query,
Narrative) will consume it directly, so every field is a closed enum or a
discriminated union rather than a string a future caller has to defensively
re-validate - `Finding.payload` is a Pydantic discriminated union keyed on a
`finding_kind` literal (not left to duck-typing), and the `summary_stat`
variant is itself a nested discriminated union keyed on column kind
(numeric/categorical/datetime). `FindingType("not_a_real_type")` raises
before any downstream code sees it, the same way `FixAction` does in Phase 2.

**No causal language anywhere in the schema, enforced by what vocabulary
exists to inherit, not by review.** Field names and enum values describe
statistical relationships only - `CorrelationPayload.coefficient`, never
`driver` or `impact`; `MissingPatternRelationship.ONE_DIRECTIONAL` with
explicit `narrower_column`/`broader_column` fields, never `implies` (a word
close enough to causal phrasing to invite it). The Narrative Agent (a later
phase, built on an LLM) will be tempted to phrase a correlation causally;
the only reliable defence is that the structured input it receives has no
causal words to draw from. `test_no_causal_vocabulary_anywhere_in_the_schema`
walks the full JSON schema checking for exactly this.

**Exploration runs only after a run reaches `status == "completed"` - never
`awaiting_approval`.** A run halted on unresolved validation events has a
frame that could still change (an approval might alter `fix_chain`), so
`app/exploration/pipeline.py::run_exploration_for_run` is a no-op unless
`run.status == "completed"`, called from both `POST /ingest/{source_id}`
(synchronous auto-fix path) and `POST /approvals/{id}/resolve` (whichever
resolution resumes the last pending event on a run). Both callers pass the
REPAIRED frame - `contract.data` after `fix_chain` application in the ingest
path, `apply_fix_chain(...)` replayed against the snapshot-or-fresh-fetch
base in the approvals path - never the raw ingested one.

**The validation/exploration boundary is kept structurally clean, not just
by convention.** Exploration never re-detects or re-reports what
ValidationEngine already found (nulls, drift, schema) - `FindingType` has no
member for any of that, so there is no field a rule-family name could even
be written into. What DOES cross the boundary is `DataQualityContext`
(`app/exploration/quality_context.py`): counts of how this run's
`validation_events` actually resolved (`ResolutionKind`, one member per
real `action_taken` string already used elsewhere in the codebase, not a
second taxonomy that could drift out of sync) plus whether the active
baseline was provisional - carried onto every `ExplorationFindings` object,
never interpreted. A finding computed on data with an `auto_fix_reverted`
event in its history is still presented as a plain statistical fact; the
caller decides how much to trust it.

**Row and column caps are seeded and self-documenting, not silent.** Above
`max_correlation_columns` (default 50), the highest-variance columns are
kept and everything else is named in `skipped` with the reason; above
`row_cap` (default 50,000), the four analyses expensive enough to need it
(correlation, outliers, trend, distribution shape - summary statistics and
missing-pattern detection always run on the full frame, since "count" and
"null co-occurrence" must reflect real data, not a sample) run against a
`df.sample(random_state=row_cap_seed)` subset, with that seed recorded on
every finding's `Evidence.sampling_seed` so the result is reproducible, not
just repeatable. `test_byte_reproducible_across_two_runs` asserts two runs
against identical input produce byte-identical serialized output.

**A trend's R-squared floor and its seasonality flag are two independent
gates, not one.** Below `trend_r_squared_floor` (default 0.3), no trend is
reported at all - recorded in `skipped` as insufficient fit rather than
emitted at low confidence. Seasonality is detected separately (basic
autocorrelation of the linearly-detrended residuals at a fixed set of
plausible lags) and set as its own `seasonality_detected`/
`seasonality_period` fields on the `TrendPayload`, never folded into
slope/direction - a later agent must not be able to read a seasonal cycle
off a `TrendPayload` and present it as a monotonic trend. A near-perfect
linear fit (residuals at floating-point noise) is explicitly excluded from
seasonality detection - autocorrelation on numerical noise is meaningless
and was observed to spuriously "detect" a period that wasn't there.

**Part 0 (tightened alongside this phase, then corrected): a fix may only
regress a rule that was actually PASSING, never one that was merely
untestable.** The first version of this check re-ran the full
`ValidationEngine` before and after a fix and reverted on ANY new failure,
full stop - which conflated two different things under one name. Silently
treating "this rule wasn't evaluated" as equivalent to "this rule passed" is
exactly the kind of absence-as-pass bug the rest of this system goes out of
its way to avoid elsewhere (see the design principle below), and it made
`safe_type_cast` unable to ever fix a `dtype_mismatch` sitting in front of a
pre-existing drift: the drift is invisible while the column is the wrong
dtype (`_check_drift`/`_check_categorical_drift` can't score a rule they
can't evaluate), so fixing the dtype and *revealing* that drift looked
identical, byte-for-byte, to *causing* one - both showed up as "a new
failure after the fix," and the old check reverted both, permanently
blocking a dtype fix that was actually correct.

**`ValidationEngine.evaluate()` (`app/validation/engine.py`) now returns
three states per rule, never two: `passed`, `failed`, or
`not_applicable`.** `not_applicable` covers every case that used to be a
bare `continue` in a rule-check loop - drift or categorical-drift checks
skipped because the column isn't numeric-typed / isn't categorical-typed /
exceeds the top-N cardinality ceiling right now, `dtype_mismatch` skipped
because the column is missing entirely, `row_count_drop` skipped because
the baseline has no row count to compare against. `validate()` (the
pre-existing FAILED-only view every other module already consumes -
correlation, diagnosis, the gate) is now a filter over `evaluate()`, not a
second independent set of checks, so nothing about its behavior changed for
any of those callers.

**`attempt_fix`'s do-no-harm check now diffs by RULE IDENTITY across three
states, not by set membership across two.** A rule regresses only if it was
`passed` on the pre-fix frame and `failed` on the post-fix one - that still
reverts, exactly as before. A rule that was `not_applicable` pre-fix and
`failed` post-fix was REVEALED by the fix, not caused by it: the fix stays
committed, and the reveal comes back on `RepairResult.revealed_failures`
for the caller to raise as its own fresh `validation_event`, diagnosed and
gated exactly like anything the initial `validate()` pass found - never a
second-class, silently-absorbed outcome. Newly-applicable rules are
re-evaluated exactly once against the post-fix frame; there is no loop
chasing convergence.

`test_fix_that_unmasks_a_not_applicable_rule_is_kept_and_the_reveal_surfaced`
(`tests/test_repair.py`) is the organic repro: `safe_type_cast` clears its
own `dtype_mismatch` on a column that's simultaneously 1000x the baseline
scale, and the result is `verified=True` with `distribution_drift:amount`
on `revealed_failures` - not a revert. A second test,
`test_normalize_case_collapsing_distinct_categories_is_reverted`, pins the
`normalize_case`/category-collapse scenario from the original phase brief -
`categorical_drift`'s own raw-differs/normalized-matches condition is
invariant under any further casefold-preserving remap, so a same-column
collapse can only ever re-fire the *same* rule in this engine, never a
different one; genuine cross-column fallout needs a stub `ValidationEngine`
(via `attempt_fix`'s `engine=` parameter) with the newly-failing rule
explicitly `passed` pre-fix, to prove the regression path still reverts
byte-identically once it's a real regression and not a reveal.

**A revealed failure is diagnosed and gated by the exact same code as any
other failure, not a lesser copy of it.** `app/resolution.py::process_group`
factors the "diagnose one group, gate it, apply-and-verify or escalate"
step out of `app/routers/ingest.py`'s synchronous auto-apply loop so
`app/routers/approvals.py`'s human-approve path can reuse it verbatim for
its own reveals. Both routers now process a QUEUE of `(group, events)`
pairs rather than a fixed list: `raise_revealed_events` turns each
`RepairResult.revealed_failures` entry into a fresh `ValidationEvent` +
singleton `CorrelatedGroup` and appends it to the queue, where it gets
diagnosed and gated like anything else - `tests/test_resolution.py` proves
the drift revealed above escalates on that second pass, because
`distribution_drift`'s applicability matrix permits no auto-fix action at
all, for any diagnosis, at any confidence (same guarantee Part 5 already
established for a drift detected the normal way). `get_diagnostic_agent`
moved to `app/diagnosis/dependency.py` so both routers' `Depends(...)`
resolve to the same function object - one `app.dependency_overrides` entry
in tests covers both.

## Phase 3 finding: the fix_chain "replay against source" design assumed an
immutable source

`Run.fix_chain` (Part 5) was built as a recipe - an ordered list of
`{action, spec}` - replayed against a fresh connector fetch rather than a
stored copy of the data, specifically to avoid duplicate storage. That's
correct when the source is a file: re-fetching returns the same bytes every
time. It's wrong for SQL or an API: a human approving a pending fix some time
after ingestion causes a re-fetch that pulls whatever the table/endpoint
holds *now*, not what the Diagnostic Agent actually saw. Every failure mode
here is silent - nothing raises, the run just resolves against the wrong
data, and the audit trail is left claiming a decision about data that no
longer exists in the form it was decided on.

Fixed with `BaseConnector.source_immutable` (a required abstract property,
`True` for `FileConnector`, `False` for `SQLConnector`/`APIConnector` - a
future connector has to answer this explicitly, there's no default to
inherit wrongly) and `app/run_snapshots.py`: any run that reaches
`awaiting_approval` against a non-immutable source snapshots its raw,
pre-fix frame to Parquet under `.run_artifacts/{run_id}/`. Later resolution
(`app/routers/approvals.py::_base_contract`) replays `fix_chain` against that
snapshot instead of re-fetching. Synchronous auto-apply during ingest is
unchanged - it already works from the in-memory frame within the same
request, before anything underneath it could change.
`tests/test_run_snapshot_immutability.py` mutates a live SQLite table between
ingest and approval (renaming a column back, independent of the pending fix)
and asserts the approval still verifies correctly against the snapshot; the
same test fails informatively if pointed at a version that re-fetches instead.

## Phase 5: the Narrative Agent

Phase 5 scope is the Narrative Agent only - no Query or Modeling agent, no
LangGraph orchestrator. It runs `app/narrative/pipeline.py` right after
exploration completes, turns `ExplorationFindings` into a human-readable
report, and persists it (`reports` table) behind `GET /reports/{run_id}`.

```
app/narrative/
  models.py         GroundedClaim, NarrativeProse, PostCheck*, ChartRef, NarrativeReport - the schema
  config.py          banned causal lexicon, regeneration cap, chart cap, all configurable
  numerics.py        shared numeral extraction/normalization (0.31 / 31% / 0.310 are "the same number")
  grounding.py        stage 1's post-parse validation: unknown finding_ids rejected here
  agent.py            NarrativeAgent - the two LLM-calling stages, resilience mirrors DiagnosticAgent
  dependency.py        get_narrative_agent() - degrades to None on construction failure, never 500s
  postchecks.py        Part 2: number fidelity, causal language, claim coverage
  charts.py            Part 3: chart type selected by data shape - no LLM involvement at all
  quality.py            Part 5: deterministic quality-context rendering, never LLM-authored
  template.py           Part 4: the deterministic fallback - one claim per finding, always available
  pipeline.py            orchestrates stage 1 -> stage 2 -> post-checks -> regenerate-once -> template, persists
app/routers/reports.py    GET /reports/{run_id}
scripts/narrative_evaluation.py   automated metrics + human rubric + the adversarial test, as a script
```

**Every LLM component before this one was constrained by a closed enum with
a deterministic gate behind it. Prose has no enum to hide behind.** The
structural answer is the same move applied differently: split generation
into two stages joined by a validated, non-empty-by-construction
intermediate. Stage 1 (grounding) sees the findings and produces
`GroundedClaim` objects - `claim_text`, `finding_ids` (non-empty, enforced by
`Field(min_length=1)` the same way `FixAction` can't hold a free-text
action), and `values` (the exact numbers, as structured `ClaimValue`
entries). A claim citing a `finding_id` absent from the actual run's
findings is rejected in `app/narrative/grounding.py::filter_grounded_claims`
immediately after stage 1 returns - the empty-list half of "reject at parse
time" is a Pydantic field constraint with no external context needed; the
unknown-id half needs the run's own data, which a bare model class parsed
via `LLMClient.complete()` has no access to, so it lives here instead.
**Stage 2 (expansion) receives ONLY the validated claim list - never the
findings object.** It cannot invent a fact the data never established
because it never receives the data that fact would come from. This was not
short-circuited by passing findings to both stages "for context";
`tests/test_narrative_pipeline.py::test_stage2_receives_only_claims_never_the_findings_object`
spies on the actual prompt sent to stage 2 and asserts none of the
findings-only vocabulary (`evidence`, `data_quality_context`, `finding_type`)
ever appears in it.

**The report reading well is not the acceptance criterion - grounding is,**
enforced by three deterministic post-checks run on every generated prose
candidate (`app/narrative/postchecks.py`), never an LLM judging its own
output: (1) **number fidelity** - every numeral extracted from the prose
must match a value in the grounded claim set (formatting variants like
0.31/31%/0.310 normalized via `app/narrative/numerics.py`, checked in both
directions); (2) **causal language** - a banned lexicon (`caused`, `drove`,
`driver`, `led to`, `due to`, `impact`, `effect`, `explains`, and their
common inflections) scanned against the generated text, permitting only
`is associated with` / `correlates with` / `moves together with`; (3)
**claim coverage** - every `GroundedClaim` must be represented somewhere in
the narrative body (numeric overlap first, a keyword fallback for claims
without a distinctive number), so stage 2 can rephrase and merge freely but
never silently drop one. Each check reports precisely what failed and
where, not a pass/fail bit - all of it persisted in `post_check_results`,
not just used as an internal switch.

**A failure triggers exactly one regeneration attempt, then the
deterministic template - never a third try, never a silent ship.** The
`generate_narrative_report` state machine
(`app/narrative/pipeline.py::_try_llm_report`) treats "LLM unreachable",
"stage 1 grounded nothing usable", and "post-checks failed twice" as the
same outcome: `GenerationMode.TEMPLATE`, with the specific reason recorded
on the report (`fallback_reason`) rather than merely implied.
`app/narrative/template.py::build_template_claims` derives exactly one
`GroundedClaim` per `Finding` - deterministically templated from that
finding's own payload and evidence, so the claim IS the finding restated as
a sentence and is trivially, perfectly grounded. Less readable by design;
that trade (lose fluency, never correctness) is the entire point of Part 4.
`app/narrative/dependency.py::get_narrative_agent` catches LLM-client
construction failure too (missing API key, unknown provider) and returns
`None` rather than letting FastAPI's dependency resolution turn it into a
500 - the run must never fail for lack of an LLM, and that has to hold even
before the first call is attempted, not just after.

**Charts are selected by data shape, never by the LLM - `app/narrative/charts.py`
has no LLM dependency in its call graph at all.** Four deterministic
mappings: a `TrendPayload` (datetime-indexed numeric) draws a line; a
`CorrelationPayload` (numeric pair) draws a scatter; a categorical
`summary_stat` draws a bar from its own `top_frequencies`; a numeric
`summary_stat` draws a histogram pulled from the repaired frame. Every
other finding type produces no chart - not a fifth invented shape.
`test_chart_selection_ignores_an_llm_recommendation_naming_a_different_chart_type`
feeds a fake model a recommendation that explicitly asks for a bar chart on
a trend finding and confirms the emitted chart is a line anyway: chart
generation is called with findings and the repaired frame only, and never
sees narrative output to begin with.

**Quality context is a required field, rendered first, in both generation
modes - never a trailing caveat that fluent prose can bury.**
`app/narrative/quality.py::render_quality_context_summary` is deterministic
and is the ONLY thing that ever populates
`NarrativeReport.quality_context_summary`, called identically whether the
report ends up LLM-generated or templated.
`NarrativeReport.rendered_text()` places it before the narrative body
unconditionally, and recommendations (when present) are confined to their
own labelled section afterward, each naming the `claim_id` that motivated
it - a structural fact about the model (`Recommendation` is its own field,
never inline text), not a formatting convention that could be skipped.

**Part 0 (this phase): the reveal chain gets an explicit depth cap.**
`app/resolution.py`'s reveal queue (Phase 4's do-no-harm correction)
terminated only because no allowlisted action happens to be able to undo
another's precondition - true today, but an accident of
`ALLOWLIST_ACTIONS`, not a guarantee. `process_queue` now tracks a depth per
queued group (0 for every group from a run's initial `validate()` pass, d+1
for anything revealed by processing a depth-d group) and, once depth
exceeds `REVEAL_DEPTH_CAP` (default 2, `Run.reveal_depth_reached` records
the actual maximum reached), escalates whatever's left straight to
`awaiting_approval` with the cap itself as the recorded reason - without
attempting a diagnosis. Since no real fix chains this deep,
`tests/test_resolution.py::test_reveal_chain_hitting_depth_cap_escalates_cleanly_rather_than_spinning`
monkeypatches `process_group` to always reveal a fresh group (an infinite
generator if nothing bounds it) and confirms the queue still terminates,
processing exactly `reveal_depth_cap + 1` groups before escalating the rest.

**The adversarial test is the headline of this phase.** Feed the pipeline a
finding with a strong, obviously-tempting-to-narrate correlation (delivery
time vs. review score, `|r| > 0.85` in the fixture) and a model that reaches
for causal language regardless of instructions -
`tests/test_narrative_pipeline.py::test_adversarial_strong_correlation_does_not_assert_causation`
and `scripts/narrative_evaluation.py`'s `adversarial_correlation_causal`
scenario both confirm the shipped output never asserts causation: the causal
model fails post-checks twice, falls back to the template, and the template
states only the correlation coefficient - never a cause.

## Phase 6: the Query Agent

Phase 6 scope is the Query Agent only - no Modeling Agent, no LangGraph
orchestrator. `POST /ask` answers a natural-language question about a
source's repaired data by generating CODE, validating it deterministically,
running it in a sandbox, and returning the answer alongside the code that
produced it.

```
app/query/
  models.py            QueryKind, GeneratedQuery, EscalationReason, QueryAnswer - the schema
  agent.py              QueryAgent - single generation call, resilience mirrors NarrativeAgent, owns its cache
  dependency.py          get_query_agent() - degrades to None on construction failure, never 500s
  cache.py                keyed on (provider, model, schema hash, normalized question)
  sql_validation.py        Part 2 SQL path: sqlglot allowlist - single SELECT, registered table/columns only
  pandas_validation.py      Part 2 pandas path: AST allowlist - permitted node types AND permitted names
  readonly_db.py            Part 2 closing note: a connection the database itself refuses to write through
  sql_execution.py           Part 3 SQL path: executes on the read-only connection, dual timeout guarantee
  sandbox.py                 Part 3 pandas path: subprocess, timeout, memory cap, size-capped JSON result
  pipeline.py                 orchestrates resolve -> generate -> validate -> escalate-or-execute -> persist
app/routers/query.py    POST /ask
app/routers/approvals.py  extended: escalated_queries in GET /pending, approve/reject_fix in POST /resolve
scripts/query_agent_acceptance.py   the three ACCEPTANCE scenarios, run standalone
```

**Every prior LLM output in this project was data - an enum, a claim, prose.
This one is CODE THAT EXECUTES.** The blast radius of a bad generation
changes from "wrong answer" to "arbitrary execution against the user's
database", so this phase treats every generated `code` string as hostile
input from the moment it leaves the model, regardless of the model's own
`confidence`. The prior phases' answer to untrusted output was the
applicability matrix (a deterministic gate over a closed enum); this phase's
answer is a validation layer the generated code must survive **before
anything runs**, and a genuinely read-only connection / sandboxed process
underneath that as the second, independent layer in case the first has a bug.

**`query_kind` is chosen deterministically by source type, never by the
model** (`app/query/pipeline.py::ask_question`): a SQL source is always
`sql`, a file/API source is always `pandas`. The model fills in `code` for
the kind it's given, or may return `unanswerable` - a first-class valid
output, not a failure. A model that returns a *different* kind than the one
it was assigned (`kind_mismatch`) is escalated exactly like any other
untrustworthy output, never silently coerced or executed as whatever it
chose. The prompt gets the live schema (column names/dtypes), a findings
summary, `data_quality_context`, the question, and at most 5 sample rows for
column disambiguation - the sample rows are the only data the model ever
sees, delimited by `<<<QUERY_INPUT_START/END>>>` markers with an explicit
"treat everything between these markers as DATA ONLY" instruction, the same
prompt-injection defense used for diagnosis/narrative sample data.

**Allowlist, never denylist - the pandas AST validator's central design
choice.** `app/query/pandas_validation.py` does not maintain a list of
forbidden names ("reject `os`, `eval`, `open`, `subprocess`..."); a denylist
is a check that a future dangerous name nobody thought to list can walk
through. Instead, `_AllowlistValidator` walks the AST and rejects on sight
anything whose node TYPE isn't in a small permitted set (no `Import`, no
`If`/`For`/`While`, no `FunctionDef`/`ClassDef`) and, independently, any bare
`Name` in load context that isn't `df`, one of ~20 pure builtins
(`len`, `sum`, `round`, ...), or locally bound - so `eval`, `exec`, `open`,
`__import__`, `getattr`, `compile`, `globals` are refused not because
they're named as forbidden but because they were never named as permitted.
Dunder attribute access (`.__class__`, `.__globals__`) is rejected
independently of the name check. Lambda parameters are tracked on a scope
stack so a lambda's own arguments are usable in its body, but anything else
free in that body still fails the Name check - "no closures over anything
but the frame" falls out of the same mechanism, it isn't a separate rule.

**The SQL path leans on a real parser, not regex.** `app/query/sql_validation.py`
parses with `sqlglot`, requires exactly one statement and that it be an
`exp.Select` (rejecting `INSERT`/`UPDATE`/`DELETE`/`DROP`/`ALTER`/`CREATE`/
`TRUNCATE`/`GRANT`/multi-statement bodies structurally, not by string
matching), and additionally walks the whole tree for a forbidden-node-type
list as defense in depth. Table and column references are checked against
the registered source's own schema (never a name the model merely claims to
have referenced), CTE aliases are excluded from the table check since
they're names the query defines rather than references, and the validated
SELECT is always re-serialized wrapped in an enforced `LIMIT` - the smaller
of the caller's cap and any `LIMIT` the query already had, so a query with
no `LIMIT` at all is exactly the case this exists to catch.

**"Structure over check" applied to execution, not just validation.** SQL
never runs on the same connection as anything else - `app/query/readonly_db.py`
opens a connection the *database itself* refuses to write through
(`PRAGMA query_only` / `SET default_transaction_read_only` / `SET SESSION
TRANSACTION READ ONLY`, dialect-specific since there's no ANSI-standard
"make this read-only" statement), so even a validator bug or an
unrecognized-as-a-write dialect construct still hits a connection incapable
of writing. An unsupported dialect is refused outright rather than executed
without this guarantee. pandas code runs in a genuinely separate OS process
(`multiprocessing`, `spawn` context) whose exec namespace contains only the
same builtins allowlist the validator already enforced plus the dataframe -
"no filesystem, no network" isn't a promise kept by convention, it's a
namespace that structurally does not contain the means. Both paths enforce
a wall-clock timeout and a result-size cap (default 10k rows, truncation
flagged rather than silently dropped); pandas additionally enforces a memory
cap (`resource.setrlimit` on POSIX - a hard kernel-level guarantee; a
parent-side `psutil` RSS-polling watchdog on Windows, disclosed in the
module docstring as strictly weaker, not silently assumed equivalent).

**A validation or execution failure is NEVER repaired by re-prompting the
model with the error message.** That would turn the validator into a hint
channel for getting past itself - the one thing every other phase's
resilience loop (backoff, repair-once-on-malformed-JSON) is careful never to
do when the "malformed" part is actually "unsafe". `app/query/pipeline.py::ask_question`
runs a fixed sequence of deterministic checks after generation - unanswerable,
kind mismatch, unknown column (checked against the live schema regardless of
the model's own confidence), static validation, confidence threshold, then
execution - and the first failure escalates immediately, full stop. Static
validation runs even for an already-low-confidence answer, never skipped as
"why bother" - it's what makes `low_confidence` (the only reason a human can
later approve-and-run) mean "validated-safe, just uncertain" rather than
"we didn't get that far".

**Escalations reuse the existing approvals mechanism - no second review
path.** Every escalation reason from Part 4 (`unanswerable`, `kind_mismatch`,
`unknown_column`, `static_validation_failed`, `low_confidence`,
`execution_failed/timeout/killed`) lands a `QueryRun` in the same
`awaiting_approval` state `ValidationEvent` uses, listed under
`escalated_queries` in `GET /approvals/pending` and resolved through the same
`POST /approvals/{id}/resolve`. `reject_fix` dismisses any of them. `approve`
is restricted to `low_confidence` (`APPROVABLE_ESCALATION_REASONS`) - the
only reason that leaves behind code which already passed static validation
and simply fell under the confidence threshold; approving anything else
would mean running code that either doesn't exist (`unanswerable`) or never
validated. Approval **re-validates and re-executes from scratch**
(`app/query/pipeline.py::resolve_escalated_query_approval`) rather than
trusting the stored `generated_code` string - the schema or data may have
drifted since the escalation was recorded, and a stored string is untrusted
text the moment it's read back from the database. A re-validation or
re-execution failure at approval time lands on `rejected`, exactly like a
human-approved fix that fails post-condition verification in Phase 4/5:
`awaiting_approval` can only legally end at `resolved` or `rejected`, never
loop back. `LLM_UNAVAILABLE` is deliberately **not** one of these - Part 5's
own "does not fail the service" clause, not a Part 4 escalation reason,
since there's no generated code for a human to review; it's recorded on the
`QueryRun` for history but starts (and stays) `rejected`, never
`awaiting_approval`.

**Quality context is attached to the answer, not a footnote - same rule as
Phase 5.** `app/narrative/quality.py::render_quality_context_summary` is
reused as-is (never re-implemented, never LLM-authored) and populates every
`QueryAnswer.quality_context_summary`, answered or escalated alike: "An
answer computed on repaired data must say so."

**The generated code is always shown, even on escalation.** `QueryAnswer.code`
/ `QueryRun.generated_code` are populated the moment generation succeeds,
independent of whether the code goes on to validate, execute, or run at
all - the user must be able to see exactly what would have run, not just
that something was attempted.

**Caching lives inside `QueryAgent`, not the pipeline** (mirrors
`DiagnosticAgent`'s pattern exactly): keyed on `(provider, model, schema
hash, normalized question)`, checked before the LLM call and populated only
on a genuine LLM response - a cache hit still goes through every Part 4
deterministic check and Part 3 execution fresh, since the schema or
confidence threshold may have changed since the entry was written. Provider
and model are part of the key for the same reason the diagnosis cache
correction earlier in this project required it: a `FakeLLMClient` run and a
real Gemini run over the same question must never collide.

**Part 0, resolved: `FREE_TIER_MODELS`/`DEFAULT_MODEL` now point at
`gemini-3.5-flash` / `gemini-3.5-flash-lite`,** verified directly against
the API rather than guessed - `GET /v1beta/models?key=$GEMINI_API_KEY`
listed both as reachable, cross-checked against
[ai.google.dev/gemini-api/docs/pricing](https://ai.google.dev/gemini-api/docs/pricing)
for a "Free of charge" row. (The two candidates tried earlier in this
project's history, `gemini-2.5-flash`/`gemini-2.5-flash-lite`, still work
but are one generation behind; `gemini-2.0-flash`/`gemini-2.0-flash-lite`
are documented as shut down 2026-06-01 - the concrete failure mode this
allowlist exists to catch loudly at construction time rather than as a
mystery 404 mid-run.)

**A second, more interesting bug surfaced the moment this ran for real:
Gemini 3.x models spend "thinking" tokens out of the same budget as
`max_output_tokens`, and `gemini-3.5-flash` does this by default.** The
previous 500-token budget (sized for 2.x, which didn't think) left as few
as ~56 tokens for the actual answer, truncating every structured response
mid-string (`finish_reason=MAX_TOKENS`, confirmed via `usage_metadata.
thoughts_token_count=444`) - every live call was failing, silently
indistinguishable from a malformed-JSON repair-then-escalate until the raw
response was inspected directly. Forcing `thinking_config.thinking_budget=0`
"fixes" `gemini-3.5-flash` but `gemini-3.5-flash-lite` (which doesn't think
by default) rejects that same setting with `400 INVALID_ARGUMENT` - since
both models share one code path, `MAX_OUTPUT_TOKENS` was raised to 4096
instead (generous enough to absorb flash's hidden reasoning AND the actual
answer) rather than trying to force thinking off in a way that doesn't work
uniformly across the allowlist. Verified live end to end afterward: both
`scripts/part6_evaluation.py` (real diagnosis calls, 11/12 correct,
0 wrongly-auto-fixed) and `scripts/narrative_evaluation.py` (both live
scenarios `generation_mode=llm`, adversarial correlation correctly avoided
causal language against a real model, not just the scripted adversarial
fake) ran clean, and a direct live `QueryAgent.generate()` call produced
valid pandas code that passed `validate_pandas_code` unmodified.
`scripts/query_agent_acceptance.py` still uses a `FakeLLMClient` for its
three scenarios, deliberately - the Query Agent's own
generate -> validate -> execute -> escalate logic is independent of which
model fills in `code`, and a scripted hostile generation is the only
reliable way to exercise the refused-before-execution path on demand.

**A live run finally answers a question open since Phase 2: real
self-reported confidence clusters, it doesn't spread.** The 11 diagnoses
`gemini-3.5-flash` produced in that `part6_evaluation.py` run scored
`0.95, 0.95, 0.95, 0.95, 0.95, 0.90, 0.95, 0.95, 0.98, 0.95, 0.95` - a
0.08-wide band pinned to the top of the scale, not a gradient distinguishing
"obvious rename" from "genuinely ambiguous distribution shift" (both scored
0.95). That is exactly why the threshold sweep in the same run is a flat
line with a cliff: routing is byte-identical from `t=0.5` through `t=0.9`
(nothing is below 0.9 to begin with) and then flips hard at `t=0.95`, where
`app/gate.py`'s `confidence <= threshold` check (line 95) fails all nine
diagnoses sitting at exactly 0.95 simultaneously. Self-reported confidence
here reads as closer to a binary "the model is willing to answer" flag than
a calibrated probability - which retroactively justifies every deterministic
layer this project has leaned on instead of it: the applicability matrix
(Phase 2/4) decides auto-fix eligibility from rule family and correlation
shape, never from confidence; four whole rule families are matrix-excluded
from auto-fix regardless of what confidence says; and Phase 6's
unknown-column check runs "regardless of the model's reported confidence"
by explicit design, not as a defensive afterthought. Phase 7 should keep
treating confidence the same way - a coarse gate, not a ranking signal.

## Phase 7: the Modeling Agent

Phase 7 scope is the Modeling Agent only - no LangGraph orchestrator, no
dashboard. `POST /predict` answers a prediction question by choosing the
task type, the model family, the split, and the metric **deterministically
from data shape**, training a small fixed candidate set, and reporting only
held-out performance against a trivial baseline.

```
app/modeling/
  models.py         IntentKind/TaskType/ModelFamily/EscalationReason/ModelResult - the schema, no causal vocabulary anywhere
  config.py          ModelingConfig - every threshold (leakage correlation, row floors, baseline margin, score floors...)
  agent.py            ModelingAgent - the ONE LLM call: intent classification only, resilience mirrors QueryAgent
  dependency.py         get_modeling_agent() - degrades to None on construction failure, never 500s
  cache.py                keyed on (provider, model, schema hash, normalized question)
  task_selection.py         Part 2 (data-shape half): task type + time-axis + aggregation, from shape alone
  leakage.py                  Part 3: deterministic feature exclusion, run before training, every drop recorded
  splitting.py                 Part 4: TimeSeriesSplit (forecast) / StratifiedKFold (classification) / KFold (regression)
  baselines.py                  Part 5: naive/seasonal-naive/majority-class/mean - the trivial-baseline gate
  automl.py                      Part 2 (model-selection half) + Part 6: small fixed candidate set, cross-validated, held-out only
  pipeline.py                     Part 1/6/7: intent routing, escalation checks in fixed order, train, persist
app/routers/predict.py    POST /predict, GET /models/{run_id}
app/routers/approvals.py    extended: escalated_models in GET /pending, approve/reject_fix in POST /resolve
scripts/modeling_acceptance.py    the three ACCEPTANCE scenarios, run standalone
```

**The failure mode this phase defends against is different from every prior
one, and the defenses have to be structural, not bolted on.** A bad
diagnosis gets caught by the gate; a bad query gets caught by the validator;
a bad narrative gets caught by number fidelity. A bad model produces a
plausible number with a good-looking accuracy score, and nothing downstream
can tell it's wrong. Three specific ways an AutoML component silently
produces garbage are each prevented by a structure that cannot hold the bad
value, not a check bolted on after: **target leakage** (`app/modeling/leakage.py`
runs before any model ever sees the data), **random cross-validation on
time-ordered data** (`TimeSeriesSplit`'s constructor has no `shuffle`
parameter at all - `app/modeling/splitting.py::get_splitter` picks the
splitter by task type, and the time-series path has nothing to misconfigure),
and **reporting a model that doesn't beat a trivial baseline** (every
candidate and every baseline is scored the same way, on the same folds, and
`app/modeling/pipeline.py::_beats_baseline` gates what's allowed to be called
"answered" before persistence ever happens).

**The LLM's only job is intent classification, and even that is checked
before it's trusted.** `app/modeling/agent.py::ModelingAgent.classify_intent`
returns exactly one of `retrieval` / `prediction` / `unanswerable`, plus a
`target_column` for the prediction case - never a model choice, a feature
list, a split, or a metric. An LLM claiming `prediction` for a column that
doesn't exist in the live schema is checked deterministically
(`app/modeling/pipeline.py::predict`) and routed to `target_not_found`
escalation, never to training, at any reported confidence - the exact same
pattern as Phase 6's unknown-column check. `retrieval` intent is handed
straight to the Phase 6 Query Agent's own `ask_question()` rather than
reimplemented here; `POST /predict` returns whichever answer shape actually
applies (`routed_to: "retrieval"` or `"prediction"`), rather than folding
both into one schema.

**`POST /predict` is a separate endpoint from `POST /ask`, not an
extension of it** - a deliberate choice, not an oversight. `/ask`'s
response shape is Query-specific and already has hundreds of tests pinned
to its exact contract; overloading it with modeling-only fields (or
branching its schema on intent after the fact) would have meant either
polluting that contract or hiding a second implicit schema inside it.
Keeping them separate means each surface stays a direct match for its own
domain, and the retrieval-routing above works by calling the existing
endpoint's own pipeline function, not by merging two HTTP contracts into one.

**Task type is chosen from data shape alone** (`app/modeling/task_selection.py::select_task_type`):
a datetime column paired with the target means forecast; a categorical
target under a cardinality ceiling means classification; a continuous
numeric target with no time axis means regression; anything else is
`unsupported` and escalates. One real-world wrinkle surfaced immediately
against Olist-shaped CSVs: **CSV/SQL ingestion never coerces date-like text
to a real `datetime64` dtype** (`app/connectors/file_connector.py`'s
`pd.read_csv` has no `parse_dates`, and no phase before this one needed one)
- a genuine order-date column arrives as plain text every time. Rather than
changing that project-wide convention (out of scope, and every other
phase's dtype-based `datetime_columns()` check stays exactly as it was),
`choose_datetime_column` falls back to recognizing a text column whose
values parse as dates almost entirely, scoped to this module alone; the
chosen column is coerced to real `datetime64` in a local copy before
anything downstream (feature exclusion, aggregation, frequency inference)
touches it.

**Forecasting a "volume" question means aggregating first, and the
aggregation method is itself data-shape-driven.** `build_time_series`
collapses the raw per-row frame into one row per period before any
splitter or model sees it: a numeric target is summed per period (a
measure - "total sales"), a non-numeric target is counted per period (a
volume - "order volume" = count of `order_id` per period). The period
length itself is chosen from the datetime column's *span*, not inferred
from raw (almost always irregular, per-transaction) timestamps via
`pd.infer_freq`, which needs an already-regular index to return anything
useful - a longer span aggregates to a coarser, more business-meaningful
grain (`MS` beyond a year of history, `W` beyond ~2 months, `D` otherwise).

**Leakage prevention runs before training and is structurally impossible
to skip** (`app/modeling/leakage.py::select_features`), checking four
classes for every candidate feature and recording exactly why each drop
happened: an identifier (flagged by Exploration's own `NEAR_UNIQUE`
cardinality note, or high-cardinality-on-its-own - scoped to
categorical/object columns only, since a *numeric* column with a near-unique
value per row is normal for a continuous measure, not an identifier
signature); constant or high-null; near-perfectly related to the target
(Pearson correlation for a numeric target, exact-match agreement rate for a
categorical one - this second form is what catches "a copy of the target
under another name" regardless of task type, not just the numeric case);
and a timestamp at or after the chosen time-axis column's own value on any
row, for time-indexed tasks. The injected-leak test
(`tests/test_modeling_unit.py::test_injected_target_copy_is_dropped_and_recorded`)
copies the target under another name and asserts it's dropped and recorded,
not merely down-weighted.

**A shuffled split on time-ordered data isn't avoided by convention, it's
absent from the API.** `app/modeling/splitting.py::get_splitter` picks
`TimeSeriesSplit` for forecast, `StratifiedKFold` for classification, plain
`KFold` for regression - and the test asserts this on the *splitter object*,
not the score: `TimeSeriesSplit(shuffle=True)` raises `TypeError` before a
splitter is even constructed, unlike `KFold`/`StratifiedKFold` where
`shuffle` is a real, settable parameter (deliberately `True` there, since
those rows genuinely aren't time-ordered).

**Every candidate is scored the same way as its baseline, and only the
held-out score is ever reported.** `app/modeling/automl.py`'s
`evaluate_classification`/`evaluate_regression`/`evaluate_forecast` score
every candidate AND its baseline on the identical folds with the identical
metric, and the winner is picked by that cross-validated score - never by
fitting on all the data and reporting how well it fits the data it was fit
on. `tests/test_modeling_pipeline.py::test_out_of_sample_score_is_never_a_training_score`
demonstrates the gap directly: a `RandomForestClassifier` given a
unique-per-row feature memorizes the training data almost perfectly
(F1 > 0.95) when fit and scored on the same rows, but the reported
out-of-sample score is materially lower, because it never sees that
memorized fit.

**The baseline gate, the score floor, and the class-imbalance check are
three separate, ordered reasons a model can fail to be "answered" - and
none of them is optional.** `app/modeling/pipeline.py::_finish_classification`
checks severe class imbalance *first*, before the baseline-beat or
score-floor checks, because a high F1 on a severely imbalanced target
(`majority_class_share` past the configured threshold) is exactly the
misleading number Part 6 calls out - reporting the class distribution and
escalating takes priority over a score that happens to also clear the other
gates. `no_model_beats_baseline` is not one of `APPROVABLE_ESCALATION_REASONS`
(a model that's flatly worse than guessing has nothing a human should be
able to override); `below_score_floor` and `severe_class_imbalance` are,
since both leave behind a genuinely trained, fully-scored model that a
human might reasonably accept anyway despite the caveat - the same
distinction Phase 6 drew around `LOW_CONFIDENCE` being the only
`approve`-able `QueryRun` escalation.

**Approving an escalated model doesn't retrain it.** Unlike Query's
`approve` path (which re-validates and re-executes stored code from
scratch, because a stored code string is untrusted the moment it's read
back), there is no code artifact behind a `ModelRun` - the training already
ran, and its full result (every candidate score, every baseline score, the
excluded-feature list) is already persisted. `resolve_escalated_model_approval`
simply transitions `awaiting_approval -> resolved`, accepting the
already-computed result despite its caveat rather than silently retraining
on approval.

**Feature associations, never importances presented as causes.** Same rule
as `app/exploration/findings.py`'s module docstring, extended: the field is
named `association_strength`, not `importance` or `driver` or `impact`,
and `tests/test_modeling_pipeline.py::test_no_causal_vocabulary_in_output_schema`
asserts no forbidden word appears in any `ModelResult`/`ModelRun` field name
by reflection, not by spot-checking one response.

**Losing the LLM doesn't lose the modelling capability.** A request that
explicitly names `target_column` skips intent classification entirely (no
LLM call is made at all) and trains directly - `get_modeling_agent()`
degrading to `None` on construction failure only removes the ability to
route a free-text *question*, exactly mirroring Phase 6's
`get_query_agent()` pattern.

**Reproducibility is seeded and tested, not assumed.** Every sklearn
candidate gets `random_state=config.seed`; `StratifiedKFold`/`KFold`'s own
shuffle uses the same seed; fold order and candidate iteration order are
both fixed (dict insertion order, `LOGISTIC_REGRESSION` before
`RANDOM_FOREST_CLASSIFIER`, etc.), so ties break the same way every run.
`tests/test_modeling_pipeline.py::test_identical_requests_reproduce_identical_results`
fires the same request twice through the real API and asserts the full
result - model family, every candidate score, every baseline score, feature
associations - matches exactly, not just the winner's headline number.

**ACCEPTANCE, verified via `python -m scripts.modeling_acceptance`:** an
Olist-shaped monthly-order-volume forecast trains with a forward-chained
split, beats the naive baseline, reports an 80% prediction interval, and
lists its excluded (identifier) feature; a prediction question naming a
nonexistent column escalates cleanly at 0.99 reported LLM confidence; and a
flat, trendless series escalates as "no model outperformed the baseline"
rather than being dressed up as a usable one.

## Phase 7.5: remediation, before the orchestrator

Five defects, all found the same way: an investigation ran the pipeline
against data actually shaped like real data (a transactional table with a
primary key, a genuine date column, multiple sheets, a live model) instead
of the synthetic shapes every existing test used, and each one had quietly
been doing nothing. **The acceptance bar for this phase was behaviour on
realistic input, not passing tests** - every one of the 399 tests already
passing was green while all five of these were broken.

### Part 1: identifier-shaped columns no longer reject the whole baseline

**The detection was right; the response was wrong.** `app/baseline_sanity.py`'s
cardinality-equals-row-count floor correctly recognizes a primary key - it
just used to treat that recognition as grounds to refuse the ENTIRE
baseline, which meant no schema conformance, no null thresholds, no drift
detection, on any transactional table with an `order_id` column. A primary
key is not evidence the dataset is garbage; it's a column that should never
have been profiled as a category in the first place.

`app/profiling.py::BaselineProfiler` now excludes an identifier-shaped
column from profiling entirely (never builds a meaningless "top values"
table over what is really a per-row unique key) and records it on
`profile["excluded_columns"]: [{"column", "reason"}]` - the same pattern as
Phase 4's `skipped` list. The baseline is still built from everything else.
`app/baseline_sanity.py` now only rejects the WHOLE baseline for genuine
data pathology - a column above the null ceiling, or a zero-variance
numeric - reviewed against the same over-reach and kept deliberately
unchanged: unlike an identifier, there's no sensible way to exclude a
column whose own data looks broken and still trust the rest of the file
around it. If every column turns out to be identifier-shaped, there is
nothing left to baseline, and that one case still rejects.

**Downstream, an excluded column returns `not_applicable`, never a silent
absence.** `app/validation/engine.py` gained a shared
`_excluded_column_outcomes` helper, called from every rule family that
iterates baseline columns (`missing_column`, `dtype_mismatch`,
`null_threshold`, `distribution_drift`, `categorical_drift`) - an excluded
column gets an explicit `NOT_APPLICABLE` outcome for each, with a stated
reason, rather than just never being mentioned. It's also excluded from
`unexpected_column` - a column this baseline deliberately has no opinion
about is not "unexpected" just for existing in the live data.

### Part 2: datetime becomes a real column kind, decided once, shared everywhere

**`app/column_kind.py` is new: the one function that answers "what kind is
this column", shared by profiling (Phase 2), exploration (Phase 4), and
modeling (Phase 7).** `ColumnKind` (NUMERIC/CATEGORICAL/DATETIME) previously
lived in `app/exploration/findings.py` alone and profiling.py had its own,
separate, datetime-blind two-state version (`"numeric"`/`"categorical"`
only) - the investigation's finding #5 (Modeling and Exploration disagreeing
about the same column in the same run) came from exactly this kind of
duplication. `app/exploration/findings.py` now re-exports the shared
`ColumnKind` instead of defining its own; `app/profiling.py` and
`app/modeling/task_selection.py` both call the shared `column_kind()`
directly. There is exactly one implementation left to disagree with itself.

**The actual defect wasn't that `ColumnKind` lacked a DATETIME member - it
already had one.** It was that nothing ever produced a `datetime64` dtype
for `column_kind()` to detect: CSV/SQL ingestion never coerces date-like
text, so a real date column arrived as plain object/string every time, and
every dtype-based kind check downstream (correctly) called it categorical.
`app/datetime_coercion.py` is new: a shared module run exactly ONCE, at
connector fetch time, so the coercion decision is made in one place and
everything downstream just reads the dtype it settled - no module-local
heuristic is left anywhere to disagree with anyone (Modeling's own
`_looks_like_datetime`, the direct cause of finding #5, is deleted
outright, not patched).

Detection priority, in order:
1. **Already `datetime64`** - used as-is, no inference attempted.
2. **A declared SQL type is authoritative.** `app/connectors/sql_connector.py`
   already reflects `declared_schema` and already know how to detect a
   declared-vs-actual mismatch (`_dtype_mismatches`) - `dtype confidence is
   a property of the source format, not the data`, the module's own
   docstring, was always pointing at this. A column whose reflected SQL
   type is DATE/TIME-shaped is coerced unconditionally, never gated behind
   a parse-rate threshold, because the source has already told us the type
   with certainty a heuristic never could. The coercion runs BEFORE the
   dtype-mismatch comparison in `SQLConnector.fetch()`, so a declared-
   temporal-vs-actual-text disagreement becomes a resolved coercion, not a
   dangling warning - see Part 3 for exactly which warnings this affects.
3. **An object column with no type metadata** (CSV, API JSON, a cell
   `read_excel` didn't already parse) is a parse-rate GUESS, and guesses get
   a floor: coerced only if parsing succeeds on nearly all non-null values
   (`DEFAULT_MIN_PARSEABLE_RATIO = 0.99`). **A column that partially
   parses is a data-quality signal, not an ambiguous type** - `errors=
   "coerce"` silently nulling the failures would make a genuinely corrupt/
   mixed-format column indistinguishable from an honestly-ambiguous one.
   That column is left untouched and reported via `connector_metadata
   ["datetime_parse_attempts"]` for a new, proper `ValidationEngine` rule
   (`DATETIME_PARTIAL_PARSE`, three-state, baseline-independent like the
   encoding checks) to raise as a diagnosable event instead - not a bolted-
   on special case, and never coerced-and-nulled.

**A real false positive surfaced immediately and would have been silent
without the "coercion must not be silent" rule this project has now been
bitten by five times** (latin-1 decoding, `httpx params={}`, `read_excel`
ignoring cell formats, `json default=str`, and now this): pandas' free-text
date parser is liberal enough to accept a bare month name (`"jan"`) as a
valid date, defaulting year/day - which silently misclassified an ordinary
three-row categorical column (`"jan"`/`"jan"`/`"feb"`) as datetime and broke
`test_pandas_valid_query_answers_end_to_end`'s grouped result the moment
real ingestion ran through the new coercion path. Fixed by requiring at
least one digit before a value is even attempted (`str.contains(r"\d")`) -
every real date representation (ISO, US, `"Jan 5 2022"`) contains one; a
pure-alphabetic token never does, and is counted as a parse failure rather
than excluded from the rate. `tests/test_datetime_coercion.py::
test_bare_month_names_are_not_misidentified_as_dates` pins this down
directly - it is not a hypothetical.

**Coercions are recorded, never silent** - `connector_metadata
["datetime_coercions"]: [{"column", "method", "parse_success_rate", ...}]`,
merged into every connector's metadata identically (`SQLConnector`,
`FileConnector`, `APIConnector`).

### Part 3: connector warnings get a real destination

Connector-warning `ValidationEvent`s (the multi-sheet Excel notice, a SQL
declared-vs-actual dtype disagreement) were written at `state="detected"`
and never advanced by any code path - not diagnosed, not gated, not
returned by `GET /findings` or `GET /approvals/pending`. They looked
handled and were not; the only way to ever see one was to query Postgres
directly.

**Implemented as informational items in the EXISTING approvals surface, not
a second review pipeline** - chosen over a dedicated endpoint because this
project already has exactly one human-in-the-loop mechanism
(`GET /approvals/pending` / `POST /approvals/{id}/resolve`), and a connector
warning needing "visible, acknowledgeable, terminal-state" is a smaller ask
than anything ValidationEvent/QueryRun/ModelRun already get through it, not
a bigger one.

- `app/state_machine.py` gained a second edge, `DETECTED -> RESOLVED`,
  reserved exclusively for acknowledging an informational event that was
  never diagnosed in the first place (every other `DETECTED` event still
  goes through `DIAGNOSED` as before).
- A new decision, `"acknowledge"`, restricted to connector-warning-shaped
  events (`rule_failed` starting with `connector_warning:`) via a new
  `_resolve_connector_warning` handler in `app/routers/approvals.py`, and a
  new `"connector_warnings"` key in `GET /approvals/pending`'s response
  (queried by `state == DETECTED`, not `AWAITING_APPROVAL` - these never
  reach that state at all).
- **Non-blocking** was already true before this phase (connector warnings
  are added to the run independently of the diagnose/gate pipeline that
  decides `run.status`) - this phase made them visible and acknowledgeable
  on top, not blocking for the first time.
- **Audit trail**: a new read-only `GET /runs/{run_id}/audit-trail`
  (`app/routers/audit.py`) lists every `ValidationEvent` for a run at
  whatever state it's in - resolution still only happens through
  `/approvals`, this is purely so a human or a script can see the full
  history for a run in one place instead of querying the database directly.

**Which warnings became actions, and which remain informational (Part 2's
interaction with Part 3):** a SQL declared-vs-actual TEMPORAL mismatch is
now resolved by coercion before it ever becomes a `connector_warning` at
all - the defect for that specific case isn't relocated to a better
destination, it's gone at the source. Non-temporal SQL mismatches (numeric,
boolean, text category), the multi-sheet Excel notice, and the API
page-cap-hit warning all remain informational `connector_warning` events,
now reaching the destination above instead of a black hole.
`DATETIME_PARTIAL_PARSE` (new in Part 2) is NOT a `connector_warning` at
all - it's a first-class rule in `ValidationEngine.evaluate()`, which means
it already goes through the full diagnose/gate pipeline like any other
detected failure; it never needed Part 3's destination in the first place.

### Part 4: the diagnosis cache never persisted

`scripts/part6_evaluation.py::main()` wrote `diagnoses.json` inside
`tempfile.mkdtemp(prefix="part6_eval_")` - a fresh, randomly-named directory
every invocation. The cache worked WITHIN one run and never ACROSS runs:
every re-run started from empty and re-spent quota re-diagnosing the exact
same corruptions a prior run had already paid for, and a replay attempt in
an earlier investigation hit a cache-key mismatch trying to work around it.

Fixed by constructing `DiagnosisCache()` with no path override at all, so
it falls back to its own already-existing, already-`.gitignore`d default
(`DEFAULT_CACHE_PATH = ".cache/diagnoses.json"`, or `$DIAGNOSIS_CACHE_PATH`)
- the exact same path production ingest already uses. `scratch_dir` stays
an ephemeral `mkdtemp()` for the file-case CSV/xlsx fixtures, which are
cheap to regenerate every run from a fixed seed; only the cache needed to
stop being ephemeral. `scripts/narrative_evaluation.py` was checked against
the same question and doesn't use a persistent, DiagnosisCache-shaped cache
at all - nothing to fix there (pinned by
`tests/test_evaluation_scripts.py::test_narrative_evaluation_script_has_no_persistent_cache_to_break`
so a future change introducing one doesn't silently reintroduce the mistake
unnoticed).

`tests/test_evaluation_scripts.py::test_second_evaluation_run_makes_zero_llm_calls`
simulates two separate invocations sharing the stable path and asserts the
second makes zero calls on `FakeLLMClient.call_count` - not inferred from
timing. Confirmed for real, not just in the test suite: running
`scripts/part6_evaluation.py` against the live provider populated
`.cache/diagnoses.json` with 11 genuine diagnoses on the first pass; a
second, independent replay script (below) reused every one of them with
zero cache misses.

### Part 5: confidence gating, honestly

**Not deleted** - a future or better-calibrated model may spread
meaningfully, and the mechanism needs to survive to catch it when it does.
Instead, documented and made observable:

- `app/gate.py::evaluate_gate` now has an explicit comment on the exact
  line that matters: `confidence <= confidence_threshold` FAILS, so a
  diagnosis sitting at EXACTLY the threshold value is rejected, not
  accepted. This is mechanically why an earlier investigation's real
  11-diagnosis live run produced a threshold sweep that was flat from
  `t=0.00` through `t=0.94` and then dropped from 5 auto-applies to 0 in one
  step at `t=0.95` - 9 of those 11 diagnoses sat at exactly `confidence=0.95`.
- `scripts/part6_evaluation.py` gained `confidence_distribution()` -
  min/max/mean/distinct-count over every diagnosis's self-reported
  confidence, printed every run, so calibration is something the script
  OBSERVES, never something assumed true because there's a threshold to
  sweep against. A fresh live run while building this phase reproduced the
  clustering directly: `n=8 min=0.950 max=0.980 mean=0.961
  distinct_values=[0.95, 0.98] (count=2)` - 2 distinct values across 8 live
  diagnoses, with the script now printing an explicit note when the
  distinct count is this low that the sweep below has little real
  separating signal to work with.
- **The finding, stated plainly:** at the confidence values a real model
  actually returns, the threshold contributes close to no discriminating
  work at the threshold this system actually runs at (`DEFAULT_CONFIDENCE_
  THRESHOLD = 0.8`) - every live diagnosis observed so far clears it. What
  actually separates an auto-applied fix from an escalation in the observed
  data is the applicability matrix (`permitted_actions` - `ESCALATE` is
  never in any matrix's permitted set, so a rule family the matrix doesn't
  trust escalates at every threshold from 0.00 to 1.00, regardless of
  confidence) and `risk_level` (which, in every live sample collected,
  partitioned identically to the matrix's own determination - `"low"` for
  exactly the matrix-eligible categories, `"high"` for exactly the blocked
  ones). One honest caveat: in every sample collected so far, the model's
  own suggested action and risk_level already agreed with the matrix's
  determination every time - the data shows the layers AGREEING, not the
  matrix catching a wrong call; its protective value is structural and
  guaranteed, not (yet) empirically demonstrated by a save.

**`scripts/part6_wrongly_auto_fixed_replay.py` is new**: re-verifies the one
figure an earlier investigation could not safely re-confirm without risking
a live call. It replays the FULL `attempt_fix` + post-condition-verification
pass against whatever is already sitting in the stable cache Part 4 fixed,
using a `PoisonPillClient` whose `.complete()` raises immediately instead of
ever reaching the network - a cache miss on any individual case aborts
loudly and is reported separately (never silently re-diagnosed live).
Run for real while building this phase, against the same 11-diagnosis cache
Part 4's fix produced: **all 12 correlated groups replayed from cache with
zero cache misses and zero live calls**, and `wrongly_auto_fixed` came back
**0 at every one of the 10 swept thresholds** - confirmed from an actual
replayed run, not carried forward as an assumption. The replay additionally
surfaced something the gate-layer analysis alone couldn't show: 2 of the 6
auto-apply attempts were caught and reverted by post-condition verification
(`auto_fix_reverted_by_verification`), not merely permitted by the matrix -
that layer is doing real, demonstrated work in this data, not only
structurally-guaranteed-but-unexercised work.

### Phase 7.5 tests

Every part above has direct test coverage extending the 399:
`tests/test_baseline_sanity.py` and `tests/test_profiling.py` (Part 1),
`tests/test_datetime_coercion.py` and additions to
`tests/test_validation_engine.py` (Part 2), additions to `tests/test_api.py`
(Part 3), `tests/test_evaluation_scripts.py` (Part 4). **The deliverable is
`tests/test_realistic_data_regression.py::test_realistic_olist_shaped_ingest_end_to_end`**
- a single end-to-end ingest of an Olist-shaped CSV (unique `order_id`,
unique per-row timestamps, a real date column, as plain ISO text) asserting
every symptom the original investigation found is gone at once: a baseline
IS established, `order_id` is excluded and recorded, `order_purchase_
timestamp` is profiled AND explored as `datetime`, at least one trend
finding fires, and Exploration/Modeling agree on the column's kind in the
same run. Every defect in this phase existed because nothing exercised the
pipeline on data shaped like real data; this test is the one that would
have caught every one of them before an investigation had to.

## Phase 8: the LangGraph orchestrator and dashboard - the final phase

Phase 8 replaces `app/routers/ingest.py` and `app/routers/approvals.py`'s
own sequencing logic with a LangGraph `StateGraph` that owns it instead
(**REPLACE, not WRAP** - decided explicitly before any code was written:
"a router rewrite that quietly rewrites well-tested behaviour is the main
risk in this phase," so the graph calls the existing diagnose/gate/apply
pipeline in `app/resolution.py` rather than reimplementing it as graph
nodes), adds a canonical audit endpoint, and adds the dashboard - the last
of the five agents' user-facing surfaces this project was missing.

```
app/graph/
  state.py            IngestState - run_id/source_id/route ONLY (see Rule C below)
  checkpointer.py       SQLite (local) or Postgres (docker-compose) - see .env.example
  nodes.py                ingest/validate/resolve/await_human/explore/narrate
  build.py                 the StateGraph itself: conditional edges, safe_node (Rule B)
  query_graph.py            POST /ask's subgraph - generate -> validate -> execute
  predict_graph.py           POST /predict's subgraph - classify -> train -> gate
app/dashboard/templates/   five server-rendered screens, no build toolchain
app/routers/
  ingest.py               now a thin graph entry point (creates the Run, invokes the graph, serializes)
  approvals.py              a validation-event resolution now RESUMES the graph at await_human
  audit.py                   GET /audit/{run_id} - canonical; GET /runs/{run_id}/audit-trail kept as a deprecated alias
  dashboard.py                Sources / Approvals / Reports / Audit / Ask
tests/
  golden_scenarios.py + golden_compare.py + golden/ingest_capture.json   the migration safety net (see below)
  test_migration_golden_capture.py    proves the graph reproduces every pre-migration scenario
  test_graph_orchestration.py           conditional edges, Rule B, Rule D, process-restart resumability, audit reconstruction
  test_query_predict_subgraphs.py         confirms the query/predict subgraphs branch for real
  test_dashboard.py                         each screen renders
```

### The migration safety net came first, and was proven green before any router changed

Before writing a single line of graph code: a 20-scenario characterization
test battery (`tests/golden_scenarios.py`) was run against the
UNMIGRATED routers and its output captured to `tests/golden/ingest_capture.json`
- every domain outcome (`run_status`, `fix_chain`, `events`, `baseline_active`,
`exploration_present`, `report_present`, `report_generation_mode`,
`reveal_depth_reached`) plus, critically, the ORDERED sequence of
`AgentTrace.agent_name` values per run - sequencing is exactly what a
migration to a different orchestration model risks silently changing, and
exactly what an audit trail claims to record, so capturing only terminal
state would have missed the failure mode this migration was most likely to
introduce. Failure-path fixtures (a connector that raises, a baseline that
fails its sanity floor) are captured alongside every success path, for the
same reason.

Two rules governed the whole migration, set explicitly before touching any
router:

1. **Preserve by default; any intentional divergence is recorded IN the
   golden file with a written reason, never silently updated.** A diff is
   then always either a bug or a documented improvement - never ambiguous.
2. **The golden file is never regenerated to make a failing test pass.**
   `tests/golden_compare.py` has no write path at all - Rule 2 is enforced
   structurally, not by discipline. `scripts/record_golden_baseline.py` is
   the one explicit, standalone, human-reviewed place recording is ever
   allowed to happen, and it is never imported by pytest.

`tests/test_migration_golden_capture.py`, completely unchanged as a file,
is what proves the migration: it matched the golden file exactly before
the graph existed (confirmed green and deterministic, checksummed, and
reported before Part 1 began), and matches it again - now via a subsequence
check tolerant of documented insertions/reorderings - after the graph
replaced the routers underneath it. There is no separate
"post-migration" test; the proof is this one test's behaviour not changing
while the implementation under it did.

**Two divergences were found, both recorded in `_divergences` with a
reason, neither silent:**
- The ingest node now writes a trace on the connector-fetch-failure path
  (previously `agent_trace_sequence: []` for that scenario - unanswerable
  for exactly the runs someone would most want to audit). Taken as a
  deliberate improvement, per Rule 1.
- Every scenario re-validating against an existing baseline now shows
  `ingestion` FIRST in its trace sequence, not wherever the old monolithic
  function happened to construct the HTTP response after validate/
  correlate/diagnose/gate had already run. The graph's ingest node writes
  its own trace as it executes - first, causally, same as it always
  happened - so this is the trace order becoming MORE accurate, not less.

**One correction was made to the migration's own design during
implementation, caught by this same safety net, not shipped:** the ingest
node originally snapshotted every mutable-source (SQL/API) run
unconditionally, to close a re-fetch race between the now-separate
ingest/validate/resolve node calls. `tests/test_three_connector_equivalence.py`
caught the actual cost of that choice: `base_contract_for_run`
(`app/repair.py`) loads from the Parquet snapshot instead of a live fetch
whenever `run.snapshot_path` is set, and a snapshot only ever persists the
DataFrame - never `connector_metadata`, where `declared_schema` lives. Every
mutable-source run, even one that never paused, was silently losing that
metadata from its own ingest response. Fixed by moving the snapshot back to
being taken only once a run is actually known to be pausing at
`awaiting_approval` (inside `resolve_node`, matching the pre-migration
condition exactly) - the same place `app/run_snapshots.py`'s own,
pre-existing module docstring already documented it should happen.

### Rules B, C, D - made structural, not conventions

- **Rule B (every graph exit sets a terminal status):** `app/graph/build.py::safe_node`
  wraps every node exactly once, in one place - a node raising for any
  reason is caught there, the run is marked `failed` with a trace recording
  why, and nothing about this depends on an individual node author
  remembering to add a try/except. `tests/test_graph_orchestration.py::
  test_node_failure_never_leaves_run_non_terminal` is parametrized over
  `app/graph/nodes.py::NODE_NAMES` - a node added later is covered
  automatically, not by remembering to write a new test for it.
- **Rule C (the checkpoint stores graph POSITION only):** `IngestState`
  (`app/graph/state.py`) carries `run_id`/`source_id`/`route` and nothing
  else - no DataFrame, no domain object. Every node re-derives the working
  frame fresh, every time, via `app/repair.py::repaired_contract_for_run`,
  which reads `Run.fix_chain`/`Run.snapshot_path` - both already committed
  to the database by the time any node runs. `Run` and `ValidationEvent`
  stay the single authoritative source for domain state; the checkpoint
  never becomes a second copy of it that could drift out of sync.
- **Rule D (domain state wins on resume):** `resolve_node` re-reads
  `run.status` first, before doing anything else, and routes to `failed`
  immediately if a prior path already got there. `await_human_node`
  re-checks that the event named in its resume payload is STILL
  `awaiting_approval` before touching it; if domain state changed
  underneath the checkpoint (resolved through another path entirely), that
  reality wins and the stale decision becomes a no-op, not a blind replay.
  `tests/test_graph_orchestration.py::
  test_reconcile_on_resume_when_domain_state_changed_underneath_checkpoint`
  resolves an event DIRECTLY in the database, bypassing the graph, then
  resumes with a now-stale decision, and asserts the graph routes correctly
  rather than re-processing an already-resolved event.

### Resumability across a genuine process boundary

`app/graph/checkpointer.py` persists to SQLite (`GRAPH_CHECKPOINT_PATH`,
local dev default) or Postgres (`GRAPH_CHECKPOINT_POSTGRES_URL`, used in
docker-compose - the same instance `DATABASE_URL` already points at, one
durable store rather than a second one to back up separately).
`tests/test_graph_orchestration.py::test_resume_after_simulated_process_restart`
escalates a run, then calls `reset_graph()` - which drops the cached
compiled graph AND explicitly closes the checkpointer's underlying
connection, not just the Python reference to it - before resolving the
escalation through the ordinary approvals endpoint. The next `get_graph()`
call has to reopen the on-disk store from nothing but what was actually
persisted to it; nothing about a resumed run depends on anything held in
this process's memory, which is what makes "a human approval arrives days
later, against a different process" actually safe rather than merely
untested.

### `GET /audit/{run_id}` - the canonical audit surface

`GET /runs/{run_id}/audit-trail` (Phase 7.5) is kept, unchanged, as a
deprecated alias - it only ever listed `ValidationEvent` rows, never the
`AgentTrace` timeline or which edge the graph took at each branch point.
**The edge is the part a plain event list can't answer** - a trace showing
which nodes ran but not why the graph branched is not an audit trail, so
`AgentTrace.edge_taken` (new, nullable, additive) is populated by every
graph node's own trace write, and `GET /audit/{run_id}` returns the full
ordered `trace` (node, input, output, confidence, edge_taken, timestamp)
alongside every `validation_events` row's complete lifecycle (diagnosis,
gate_reasons, reversal, resolved_by), the active baseline, and - Part 0's
explicit condition on keeping query/model approvals OUTSIDE the graph -
every `QueryRun`/`ModelRun` answered against this run's data, generated
code included. `tests/test_graph_orchestration.py::
test_audit_endpoint_explains_a_human_resolved_escalation_and_covers_query_runs`
is the test that holds this condition: without the query/model coverage,
"why did this run produce this output" stops holding for exactly the paths
most likely to be examined. The dashboard's Audit screen uses only this
canonical endpoint, never the deprecated alias.

### Query and predict subgraphs - deliberately minimal

Both `/ask` and `/predict` stay outside the LangGraph checkpointing model
entirely - approving an escalated query or model is "re-validate and
re-run from scratch" (`resolve_escalated_query_approval`) or "accept the
already-computed result" (`resolve_escalated_model_approval`), never a
resumed position, so a checkpoint would be pure ceremony. They DO get a
`StateGraph` each (`app/graph/query_graph.py`, `app/graph/predict_graph.py`),
scoped narrowly to real branch points only, on purpose:
`generate -> validate -> execute` for query (matching the exact escalation
order Phase 6 already established), `classify -> train -> gate` for
predict (classify routes a retrieval-intent question straight to the
existing Query Agent rather than reimplementing that path). Every check
inside each node kept its exact original order and logic - only the node
boundaries are new; a node per one-line "if X: escalate" would have been
decoration, not orchestration, which is why neither graph has one node per
individual Part 4/Part 1 check. `tests/test_query_agent.py` (28 tests) and
`tests/test_modeling_pipeline.py`/`test_modeling_unit.py` (53 tests) -
every one of them pre-existing, none rewritten for this migration - still
pass unmodified against the graph-based implementation underneath.

### The dashboard

Five server-rendered screens (`app/dashboard/templates/`), each a static
Jinja2 shell whose data comes entirely from client-side `fetch()` calls
against the platform's existing, already-tested JSON API - the dashboard
has no database session and no business logic of its own; it cannot drift
from what the API actually does because it never reimplements any of it.
No build toolchain (no npm/webpack) - the whole thing is plain HTML +
vanilla JS, served by the same FastAPI app as everything else, so
`docker compose up` needs no extra service or build stage for it.

- **Sources** (`/dashboard/sources`) - register a source (JSON
  `connection_config`, since a `file`/`sql`/`api` source's shape genuinely
  differs), trigger an ingest, and expand a source's run history.
- **Approvals** (`/dashboard/approvals`) - every pending item from
  `GET /approvals/pending`: provisional baselines, escalated validation
  events (a correlated group rendered and resolved as ONE item, with its
  diagnosis and gate reasons shown inline), connector warnings
  (acknowledge), escalated queries/models. All four validation-event
  decisions are exposed, not a binary approve/reject.
- **Reports** (`/dashboard/reports`) - the quality context is parsed out of
  `NarrativeReport.rendered_text()`'s own guaranteed `## Data Quality
  Context` marker and rendered FIRST, in its own visually distinct box,
  before the narrative body; the generation mode (`llm`/`template`) is
  shown plainly next to it. Charts render via Plotly (loaded from
  `cdn.plot.ly`, the one external asset this project relies on - a
  reasonable tradeoff for "minimal, functional," documented here rather
  than silently assumed).
- **Audit** (`/dashboard/audit`) - renders `GET /audit/{run_id}` and
  nothing else: the node/edge timeline, every validation event (a reverted
  auto-fix is visually flagged, not just listed the same as any other row),
  and the query/model runs answered against that data.
- **Ask** (`/dashboard/ask`) - the generated code is always shown alongside
  the answer, including on escalation (an escalated question can still
  carry code that just wasn't confident/valid enough to run unattended) -
  never the answer alone.

### Deployment

`docker compose up --build` brings up Postgres 16 and the FastAPI app (the
dashboard is served by that same app container - server-rendered HTML
needs no separate process or build stage of its own), with the app's
healthcheck-gated `depends_on: db: condition: service_healthy` unchanged
from Phase 1, plus a new healthcheck on the app service itself
(`GET /health`) so anything added later can depend on it the same way.
Two named volumes are new: `run_artifacts` (a run's raw pre-fix frame,
snapshotted once it's known to be pausing against a mutable source - see
Rule about resumability above; this must survive the container being
recreated, not just a process restart within it) and `app_cache`
(diagnosis/query/modeling LLM-response caches - losing this only costs a
re-spent LLM call, never correctness, but there's no reason to discard it
on every `docker compose up`).

**A real deployment bug was caught before it shipped, not after:** adding
`langgraph-checkpoint-postgres` to `requirements.txt` alone is not
sufficient - it depends on `psycopg` (v3), and `psycopg` alone (without the
`[binary]` extra, which pulls in a separate `psycopg-binary` wheel) raises
`ImportError: no pq wrapper available` the moment anything tries to
construct a `PostgresSaver`. This would have surfaced the first time any
ingest run against the docker-compose deployment reached
`awaiting_approval` - after `docker compose up` had already reported
success, after Postgres and the app were both healthy, well past the point
a demo or an early smoke test would likely have caught it. Confirmed fixed
by downloading real `manylinux2014_x86_64` wheels for
`psycopg[binary]>=3.1` against the Dockerfile's exact Python version
(3.11) before adding it to `requirements.txt` - the same "check for real
wheels before committing" habit this project has followed since the Great
Expectations/Python-3.14 finding in Phase 2.

**Redis: deliberately not included.** Nothing in this architecture needs
it. There is no task queue (every ingest/ask/predict request is answered
synchronously, within its own HTTP request/response), no pub/sub, and no
cache that needs to be shared across multiple app replicas rather than
living on local disk per-process (the diagnosis/query/modeling caches are
already file-based and content-addressed by a stable key, which is exactly
what makes them safe to lose and cheap to warm again - a property Redis
wouldn't improve on for a single-app-replica deployment). Adding it would
be a second moving part in `docker compose up` earning nothing back;
the honest thing is to say so rather than include it because a platform
like this "should probably have one."

**Post-release verification found a real inconsistency: diagnosis was the
only agent that couldn't run without an LLM.** `get_narrative_agent`/
`get_query_agent`/`get_modeling_agent` all degrade to `None` on
construction failure (Phases 5-7's own "the run must never fail for lack
of an LLM" rule); `get_diagnostic_agent` didn't, and it's the one agent
every ingest touches, not just some. With no `GEMINI_API_KEY` configured -
the DEFAULT state for a fresh clone, since `.env.example` ships it blank -
`POST /ingest` 500'd before the route body ever ran, on the very first,
completely clean ingest, not just a corrupted one. `docker-compose.yml`
also never passed any LLM env var into the app container at all, so even a
correctly-filled-in `.env` changed nothing there. Both are fixed now:
`get_diagnostic_agent` degrades to `None` the same way the other three do,
`app/resolution.py::process_group` treats `diagnostic_agent=None` as an
already-failed diagnosis (the exact shape a 429 or a malformed response
already produces, which `evaluate_gate` already escalates unconditionally
- no change to the gate itself was needed or made), and
`docker-compose.yml` reads `LLM_PROVIDER`/`GEMINI_API_KEY`/`GEMINI_MODEL`
from `.env` via `${VAR:-default}` substitution. See "Running without an
LLM" above.

### Demo script

A walkthrough for someone who has never seen this system, using a
Olist-shaped CSV (any CSV with an `order_id`-like column and a numeric
column works for the corruption steps below):

```bash
# 1. Bring the whole platform up.
docker compose up --build
# Postgres and the app both report healthy; http://localhost:8000/dashboard
# redirects to the Sources screen.

# 2. Put a CSV where the app container can see it.
mkdir -p ./data
cp /path/to/olist_orders.csv ./data/orders.csv
```

Then, in the browser at `http://localhost:8000/dashboard`:

1. **Sources** - paste `{"type": "file", "connection_config": {"path": "/data/orders.csv"}}`-shaped
   input (type `file`, config `{"path": "/data/orders.csv"}`) and click
   **Register**, then **Ingest**. The first ingest establishes a
   provisional baseline; nothing to approve yet.
2. Corrupt the file to demonstrate detection and auto-fix: rename a column
   (`city` -> `town`) and overwrite `./data/orders.csv`. Click **Ingest**
   again on the same source. The run detects the rename, diagnoses it, and
   (a matrix-permitted, low-risk action) auto-fixes and verifies it in the
   same request - `status: completed`.
3. Now demonstrate an escalation: corrupt the file with a genuine
   data-quality problem (null out ~50% of a numeric column, or drop a
   column entirely) and **Ingest** again. This time the run stops at
   `status: awaiting_approval` - go to **Approvals** and see the escalated
   group, its diagnosis, and its gate reasons shown together as ONE item.
4. Resolve it - click **Approve** (or **Reject fix**/**Accept as new
   baseline**, depending on what you corrupted) with a `resolved_by` name
   filled in. The run resumes and completes.
5. **Reports** - paste the `run_id` from step 4's ingest/approval response
   and click **Load**. The quality context box appears first, stating
   plainly whether this run's data carries any caveat; the narrative and
   any charts follow, with the generation mode (`llm` or `template`) shown
   next to them.
6. **Ask** - paste the `source_id`, ask a question in plain English (e.g.
   "what is the average order amount"), and see the generated code
   alongside the answer - never the answer by itself.
7. **Audit** - paste the same `run_id` and click **Load**: the full node/
   edge timeline, the resolved escalation from step 4 with who resolved it
   and how, and the question from step 6 if it was answered against this
   run's data. This one response is enough to answer "why did this run
   produce this output" without reading any code.
8. Forecasting (via `POST /predict`, not yet its own dashboard screen -
   see "What's not here yet" below):
   ```bash
   curl -X POST http://localhost:8000/predict \
     -H "Content-Type: application/json" \
     -d '{"source_id": "<id>", "question": "forecast next month order volume"}'
   ```
   Either reports a forecast that beats the naive baseline with an 80%
   prediction interval, or escalates honestly ("no model outperformed the
   baseline") - never a number dressed up as reliable when it isn't.

### What's not here yet

- A dedicated **Predict** dashboard screen - `POST /predict` works fully
  (step 8 above) and its escalations already appear in the Approvals
  screen's "Escalated models" section; only a purpose-built query-and-chart
  UI for it (the five screens the Phase 8 spec named did not include one)
  is out of scope for this phase.
- Multiple app replicas behind a load balancer - `GRAPH_CHECKPOINT_POSTGRES_URL`
  makes this SAFE (checkpoints live in shared Postgres, not a process-local
  file), but nothing in `docker-compose.yml` sets it up, since Phase 8's
  acceptance criterion is a single `docker compose up`, not a scaled
  deployment.

## Post-release: frontend/backend split

Phase 8 deliberately shipped the dashboard server-rendered from the same
FastAPI process (`app/dashboard/`, Jinja2 templates) specifically so
`docker compose up` needed no build toolchain. Requested afterward: a real
separate frontend, in its own directory, its own build. That reverses the
"no build toolchain" tradeoff on purpose, so it's worth being explicit
about what changed and why, the same way every other deliberate reversal
in this project has been.

**What moved, mechanically:** `app/`, `tests/`, `scripts/`,
`requirements.txt`, `Dockerfile`, and `pyproject.toml` all moved into
`backend/` unchanged (a filesystem move, not a rewrite - every import,
every test, every relative path convention inside those files already
worked relative to wherever the process's cwd is, so nothing needed
editing beyond that). `app/dashboard/` and `app/routers/dashboard.py` were
deleted outright, not deprecated - the new frontend replaces them
completely, and `jinja2` came out of `requirements.txt` since nothing
server-side renders HTML anymore.

**What's new:** `frontend/` - Vite + React + TypeScript + Tailwind, five
pages (`src/pages/`) mapping 1:1 to the five Phase 8 screens, an API client
(`src/api/client.ts`) that is the ONLY code in the frontend that talks to
the backend, and hand-written TypeScript types (`src/api/types.ts`) mirroring
the backend's actual response shapes - verified against live responses
during the split (not assumed from memory), not generated from a shared
schema, so a backend response shape change won't be caught until it's
actually exercised. `app/main.py` gained `CORSMiddleware` (`FRONTEND_ORIGINS`,
comma-separated, defaults covering Vite's dev port and the compose
frontend service) since the API now serves a genuinely different origin,
not itself.

**The reference-image question, resolved explicitly rather than guessed:**
the requested visual reference was a generic admin-dashboard template
(sales KPIs, visitor charts, a world map) with no correspondence to
anything this platform tracks. Confirmed before building anything: match
the VISUAL STYLE (dark sidebar nav, card-based layout, rounded corners,
a violet accent, generous spacing) applied to the platform's actual five
screens and real data (validation events, diagnoses, audit trails, quality
reports) - never the literal widgets, which would have meant inventing
fake sales metrics this system has no way to produce honestly.

**Nothing about the backend's behavior changed.** Every JSON endpoint,
every response shape, every escalation rule, the gate, the graph - all
identical to Phase 8. The 461 backend tests (462 minus the 7 that tested
the now-deleted server-rendered dashboard's own page rendering, which has
no equivalent to test on the backend side anymore) pass unmodified from
the new `backend/` location. The frontend has no tests of its own yet -
verified instead by running both the real dev server and a production
build against the real backend end to end (upload, ingest, escalate,
resolve, report, audit, ask) and diffing the actual JSON responses against
the hand-written TypeScript types field by field, not just by TypeScript
compiling cleanly.

**File uploads work exactly as before the split** - `POST /sources/upload`
(added in an earlier round) is backend-only and was never part of the
server-rendered dashboard's own code; the new frontend's Upload button
calls the identical endpoint the old one did.

**Docker Compose now has three services**, not two: `db`, `backend`
(renamed from `app` - same image, same healthcheck, build context now
`./backend`), and `frontend` (a multi-stage build - `npm run build`, then
nginx serving the static output on port 80, published at `3000`, with an
`nginx.conf` SPA fallback so a direct navigation or refresh to `/approvals`
or `/audit` still serves `index.html` instead of 404ing, since those are
real client-side-routed paths, not server endpoints). `VITE_API_BASE_URL`
is a Vite BUILD-time value, not a runtime one - it has to be a URL the
BROWSER can reach (the backend's published host port), never the
backend service's internal docker-network hostname, since API calls
happen client-side, not from inside the frontend's own container.

## The Business Analytics Agent

A third agent alongside Exploration and Modeling. Exploration answers "what
does this data look like"; Modeling answers "what will this column do next";
this one answers "what does this data say about the business".

**No LLM anywhere in method selection or computation.** Every analysis is
deterministic arithmetic over the repaired frame, seeded where randomness
exists (k-means). The LLM's only contact with this agent is downstream: the
Narrative Agent may cite these findings as claim sources, through the same
two-stage grounded generation and the same post-checks as everything else.

### Capability detection comes first

Each analysis needs a specific data shape, and on arbitrary ingested tables
most of those shapes are absent. An agent that assumed them would either
crash or - worse - silently never fire and become dead code nobody noticed.
This project has already been bitten by that exact failure mode once, in
datetime handling.

So `app/analytics/roles.py` scores every column for six roles (`entity_id`,
`transaction_id`, `item_id`, `event_date`, `monetary`, `quantity`) from
dtype, cardinality, sign, skew and name shape. Name hints only ever *adjust*
a score the statistics already established - a column called `customer_id`
holding one distinct value per row is not an entity identifier whatever it
is called.

A monetary column may contain a **small fraction of negatives**. Returns,
refunds and bad-debt adjustments are ordinary transaction data, and an
any-negative rule rejected `Price` on Online Retail II over 5 rows in
1,067,371 (0.0005%) - taking four analyses down with it. The rule's real job
is excluding profit/delta/change/variance columns, which run 30-50%
negative, so the ceiling is a configurable fraction (`DEFAULT_MAX_NEGATIVE_
FRACTION`, 5%) sitting an order of magnitude clear of both populations. The
negative count and fraction are recorded on the detection and shown next to
the role, so a column accepted *with returns in it* never looks like one
that had none.

Confidence is banded. Only `medium` and above is consumable; anything weaker
is surfaced as a candidate for a human to confirm rather than used silently.
`confirmed` is never machine-produced, so a reader can always tell a
person's decision from an inference.

Surfacing a candidate only means something if it can be acted on, so the
Analytics page pairs each unmet role with a column picker and a confirm
action (`POST /analytics/{run}/confirmed-roles`). A confirmation is stored
against the **source**, not the run, so re-ingesting the same file inherits
it and the question is asked once. It always outranks detection, is never
pre-selected in the UI, is withdrawable (`DELETE .../confirmed-roles/{role}`),
and one naming a column a later ingest no longer has is reported as stale
rather than raising.

`app/analytics/applicability.py` then reports every analysis - applicable or
not - and a refusal names the precise unmet requirement plus the closest
rejected candidate and why it was rejected. "Not applicable" with no reason
is the failure mode that layer exists to prevent.

### The seven analyses

| Analysis | Requires | Notes |
| --- | --- | --- |
| ABC / Pareto | monetary | Configurable 80/95 cumulative cutoffs; curve downsampled above 500 points, and says when it did. Reports what share of value the top 20% hold and **flags when that falls below a configurable floor** (50% default) - see below. |
| RFM | entity + date + monetary | Quintile scores; segment names from a rule table that is **data**, echoed in full in the output. |
| Cohort retention | entity + date | Configurable granularity (month default). Unobserved periods are `null`, never `0.0`. |
| Behavioural segmentation | entity + date + monetary | k-means on standardized RFM features, k by silhouette, **seeded**. Below the silhouette floor it reports "no stable segmentation found" as a first-class answer. |
| Market basket | transaction + item | Apriori (mlxtend), lift > 1 only, capped itemset size and rule count. Skips entirely, with a reason, when transactions are mostly single-item. |
| Retention / churn | entity + date | Repeat rate, gap distribution, and an inactivity flag from a **configurable window that is always stated**. |
| Historical CLV | entity + date + monetary | Labelled HISTORICAL and descriptive, never predictive. Reports revenue-based value and says so when no margin rate is supplied. |

### What "value" means: unit price vs line total

The monetary role names a numeric column. It does not say whether that
column holds a LINE TOTAL or a UNIT PRICE, and the difference decides
whether summing it means anything. On Online Retail II the monetary column
is `Price` - the price of ONE unit - so ABC/Pareto ranked by how expensive
an item is. Its Band A was led by `Manual` and `AMAZON FEE`: bookkeeping
entries with high unit prices and negligible volume. Computed as
`Price x Quantity` the same data leads with `REGENCY CAKESTAND 3 TIER`,
the dataset's actual best seller.

`app/analytics/value_basis.py` resolves this ONCE and every value-summing
analysis (Pareto, RFM's monetary quintile, CLV, behavioural segmentation)
takes its series from there, so they cannot end up ranking by different
quantities.

**The asymmetry that drives the design.** Two errors are available and they
are not equally bad. Summing a unit price understates volume sellers -
wrong, and obvious once seen. Multiplying a column that is ALREADY a line
total inflates every figure by the quantity, silently, and the result still
looks plausible. So derivation requires POSITIVE evidence that the column is
unit-price-shaped (`price`, `unit_price`, `rate`, `unit_cost`), never merely
the absence of evidence that it is a total. Names are checked total-first,
so `total_price` is a total that happens to contain "price". An unrecognised
name (`cost`, which could be either) is used as-is.

Which basis was used is reported in every result's `parameters` and stated
at the top of the Analytics page before any figure that depends on it -
"Value computed as `Price` x `Quantity`" or "Value taken directly from
`Amount`". A revenue total and a unit-price total look equally plausible in
isolation, so this is never left implicit.

When the monetary column reads as a unit price and NO quantity clears the
confidence floor, the analyses still run on the unit price and the gap is
raised in the confirm-a-role flow rather than filled with a guessed
multiplier.

### Entities that net to zero or below

Admitting returns into a monetary column creates a real case the analyses
have to answer for: an entity that returned everything nets to zero, one
issued a credit nets below it. Returns **net off** - a line of -100 reduces
its entity's total, it is never dropped - and the two analyses that treat
that total differently say so:

| Analysis | Behaviour | Why |
| --- | --- | --- |
| ABC / Pareto | Held out of the bands and the curve, reported as a `non_contributing_entities` finding with counts, combined net, and named examples | A cumulative share is only well-defined over non-negative parts. Ranking descending over mixed signs sends the curve **above 100% and back down** (measured at 106.8%). And a full returner is not a small buyer - filing it in band C would say something false about both. |
| RFM / CLV | Kept and ranked; a net-negative entity scores in the lowest monetary quintile | These **rank** entities rather than decomposing a total, so "returned more than they bought" is a meaningful position, not a broken one. |

Both denominators are published side by side - `total_value` over the ranked
contributors, `combined_value_total` over everything - so the two figures
reconcile instead of looking like an arithmetic error. When *every* entity
nets to zero or below, Pareto refuses with that as the stated reason rather
than drawing an empty chart. RFM reports `value_shares_meaningful: false`
when the overall total is not positive, because dividing by a negative
denominator would present a net-negative segment as holding a positive share.

On Online Retail II this quarantines 284 products: 283 with a recorded unit
price of 0.00, and one named `Adjust bad debt` netting -147,614.08.

### Encoding is detected from a sample, applied to the whole file

`chardet` sees only the first 64KB. On a large export whose header and first
thousands of rows are plain ASCII, it answers `ascii` with confidence 1.0 -
a true statement about the sample and a false one about the file. The first
`£` megabytes later then killed the read, and the run surfaced as a bare
`failed` with `UnicodeDecodeError` buried in an AgentTrace.

Two things fix that, and the second is the general one:

- An `ascii` verdict is widened to `utf-8`. UTF-8 is a strict superset, so
  every byte the sample saw decodes identically and the bytes it never saw
  now decode too. The validation rule that treats a substituted encoding as
  a corruption signal knows this pair is equivalent, so widening does not
  raise a false alarm - a genuine `latin-1` last resort still does.
- The candidate chain guards the **actual full-file parse**, not a 64KB
  sample of it. Verifying a sample and then parsing 94MB checks a different,
  smaller file that happens to share a prefix. Reaching `latin-1` cannot
  raise, and is reported as a warning rather than passed off as a clean read.

### Reporting when a method's own premise fails

Three analyses check the assumption their name carries and say so when it
does not hold, rather than presenting a number that quietly means less than
it appears to:

| Analysis | Premise | Floor | What happens below it |
| --- | --- | --- | --- |
| Trend (exploration) | the fit explains the series | R-squared 0.3 | the trend finding is skipped |
| Behavioural segmentation | the clusters are separable | silhouette 0.25 | "no stable segmentation found", as a first-class answer |
| ABC / Pareto | value is concentrated in a few entities | top-20% share 0.5 | bands are still reported, **flagged as weakly concentrated** |

Pareto differs from the other two on purpose: a concentration curve is a
truthful description of the data even when it is flat, so the result is kept
and annotated instead of withheld. The measured figure is reported either
way ("the top 20% of `product` hold 37.4% of total value"), so its presence
is never itself the signal - and on a small table the note names the real
denominator, since the "top 20%" of 3 entities is really the top 1.

### Deliberately out of scope

These were considered and **excluded because the data they require does not
exist in an ingested business table**. This is a scope decision with a
reason, not an omission - and none of them can be faked from order history
without inventing the very thing that makes them valid.

| Method | Why it cannot run here |
| --- | --- |
| Marketing attribution | Needs touchpoint logs - which channels a customer saw, in order, before converting. An orders table records the conversion and nothing before it. |
| Marketing mix modelling | Needs channel spend over time. No spend data is ingested. |
| Uplift modelling | Needs a randomised treatment flag. Nothing here assigns treatment. |
| A/B testing | Needs experiment assignment and a pre-registered metric. There is no experiment. |
| Conjoint analysis | Needs a survey instrument with designed attribute trade-offs. |
| Van Westendorp price sensitivity | Needs the four survey price questions. Observed transaction prices are not a substitute. |
| Price elasticity | Needs genuine price variation for the same item, ideally exogenous. A single observed price per item identifies nothing. |
| Causal inference (DiD, synthetic control, RDD, PSM, IV) | All need a treatment/control structure - a policy change, a cutoff, an instrument. An orders table has no such structure, and running these on observational sales data would produce confident numbers with no causal validity at all. |
| Inventory analytics, working capital | Need stock levels and ledger data. Neither is ingested. |

The pattern is consistent: each excluded method needs *something the
business did* (an experiment, a price change, a media buy) or *something
another system holds* (stock, ledger, touchpoints). Reporting them from
order history alone would be exactly the "assert more confidence than the
evidence supports" failure this whole project is built to avoid.

### No causal vocabulary, structurally

`app/analytics/findings.py` carries no causal words in any field name, enum
value or payload key - the same defence `app/exploration/findings.py` uses.
A segment **accounts for** a share of value. A basket rule is an
**association**: lift > 1 means two items co-occur more often than
independence predicts, never that buying one brings about the other. The
Narrative Agent can only inherit vocabulary these schemas actually contain,
so causal phrasing is prevented structurally rather than asked for politely.

A test asserts the ban over every schema in the module and over a real
result payload. It is strict enough that a docstring *explaining* the
prohibition had to be reworded, because Pydantic copies class docstrings
into the JSON schema.


## Personal data: detection at ingest, redaction at the egress boundary

> The full threat model — including what is deliberately **not** defended, and
> why this must not be exposed to a network you do not control — is in
> [SECURITY.md](SECURITY.md). Read §2 before deploying anything.


Every outbound call to a third-party model is a place where personal data can
leave. The layer that stops that has two halves, and the split between them is
the design.

**Detection is deterministic. No language model is involved.** Using a model to
find personal data would mean sending the data to find out whether it should be
sent, which is circular. `backend/app/privacy/detectors.py` is regex and
checksum only.

Two tiers, and the tiering is what keeps it honest:

- **High confidence** - email, phone, payment card (Luhn-checked), IP address,
  government id per locale. Pattern-verifiable, so auto-classified with no
  human involved: waiting for confirmation would mean leaking while you wait.
  The Luhn check is what separates a card column from a column of 16-digit
  order ids - roughly 90% of arbitrary 16-digit numbers fail it.
- **Low confidence** - person names, postal addresses, free text. These
  *cannot* be detected reliably from values, and a `customer_name` name
  heuristic both misses (`contact`, `bill_to`, `recipient`) and over-fires
  (`product_name`, `campaign_name`, `file_name`). They are never
  auto-classified as a fact. They are surfaced as **candidates**.

**The stored frame is never mutated.** Redaction applies only to the copy
leaving for a model - the same detect-before-transform separation the value
basis and the semantic roles already follow. `redact_records()` is the one
redaction function, and `RedactedSample` is the only type the agents accept
for sample rows, so forgetting to redact at a call site is a type error rather
than a code-review note. Redacted values become stable tokens per column
(`<EMAIL_1>`, `<EMAIL_2>`), so a model can still see that two rows share a
value - which is what it needs to spot a duplicate or a join key - without
seeing the value. Column NAMES are never redacted; the model needs the schema.

### Default-deny on candidates, and the one place it is relaxed

A candidate is masked until a person says otherwise. That default was chosen by
measurement, not by preference, and the measurement went both ways:

| path | policy | measured effect of masking candidates |
| --- | --- | --- |
| diagnosis | strict | none. 3/3 groups produced the same cause and the same confidence with redaction on and off, against a live model |
| query, modeling | strict | same reasoning; the model is reading structure, not entity names |
| narrative | permissive | **material**. The report went from 5 claims to 4 and from 574 to 275 characters, losing the value-concentration finding outright |

So the policy is per PATH (`POLICY_BY_PATH` in
`backend/app/privacy/redaction.py`), never global. Relaxing everywhere to
protect narrative quality would have traded a real leak for a readability gain.
Under `PERMISSIVE` only VERIFIED personal data is masked; a Luhn-valid card
column is masked on every path including that one.

The Privacy section on the Reports page states which columns are masked, on
which paths, which a person marked safe, and who marked them. There are no user
accounts, so "who" is stored and displayed as an attribution, not an identity,
and it is labelled that way.

### Clearing a column, and the one clearing that is refused

`POST /privacy/{run}/decisions` records that a column is or is not personal,
scoped to the SOURCE so a re-ingest inherits the answer. Two guards:

- A column detection **verified** as personal cannot be marked not personal.
  It is a 400 with the evidence in the message, not a stored-then-ignored
  decision - a person who believes their action took effect is worse off than
  one who was told it did not.
- A stale "not personal" cannot unmask a column that has since started holding
  personal data. A source can change shape; detection runs anyway and wins,
  and the classification says so in its reason rather than dropping the
  decision silently.

`DELETE /privacy/{run}/decisions/{column}` withdraws a decision, because
marking a column safe is the one action here that REMOVES protection and that
must not be a one-way door.

### The egress trail: what left, recorded without recording what left

Every outbound model call writes an `egress_events` row, surfaced as the
Egress section of `GET /audit/{run}` and the Audit page. It records the agent,
the provider CLASS and model, the redaction policy in force, which columns
were included, how much was sent, which columns the redactor masked and how
many distinct values in each.

"How much" carries its own unit, because the paths do not send the same thing:
`rows` for diagnosis, query and modeling; `findings` for the narrative agent's
grounding call; `claims` for its prose call. Both narrative calls are recorded
- an auditor counting how many times a run reached a third party has to get
the true number, and folding two calls into one row would give them the wrong
one. Each prose regeneration attempt is a row too, for the same reason.

**It never records the payload.** Writing the rows down to prove they were
protected would put a second, unclassified copy of exactly the withheld data
into the audit store. Column names are recorded because the model receives the
schema anyway and a name is not personal data; values never appear, masked or
otherwise - not even as redaction tokens, since a log full of
`<EMAIL_1>..<EMAIL_2000>` discloses a column's cardinality it was never meant
to describe.

Two structural guarantees rather than review notes:

- A record is built FROM a `RedactedSample`, and the only way to obtain one is
  to call `redact_records`. `redacted_columns` and `masked_value_counts` are
  copied off that object, so a record cannot claim a column was masked unless
  the redactor masked it. The narrative path, which has no sample to copy
  from, gets the same invariant by intersecting the policy's redactable set
  with what the redactor reported masking - a column that is redactable but
  appears in no finding was not masked on that call and is not claimed as
  masked. The log reports what happened; it cannot describe an intention.
- `EgressRecord` has no field a payload could go in, and a test asserts the
  exact field set. A later edit cannot quietly start putting rows in one.

A cache hit records nothing, because nothing left the machine. A trail that
counted cache hits as disclosures would overstate the exposure, and this trail
is worth having only if its numbers are true in both directions.


### Limits, and why each one refuses instead of truncating

| Limit | Default | Env var |
| --- | --- | --- |
| Upload size | 200 MB | `MAX_UPLOAD_BYTES` |
| Ingest rows | 2,000,000 | `MAX_INGEST_ROWS` |
| Ingest columns | 4,096 | `MAX_INGEST_COLUMNS` |
| Requests per window | 60 / 60s | `RATE_LIMIT_REQUESTS`, `RATE_LIMIT_WINDOW_SECONDS` |

The row and column limits live on `DataContract` — the one type every
connector produces and every agent consumes — so there is no ingest path that
skips them. They **refuse**; they never truncate. Analysing the first two
million rows of a larger file and reporting that as the dataset's profile
would give a baseline, a null rate and a Pareto band computed over part of the
data, with nothing in the report saying so: a silent fallback producing
confidently wrong output, which is the failure this codebase keeps removing.

An unparseable override (`MAX_INGEST_ROWS=lots`) raises at startup rather than
reverting to the default, for the same reason: a limit the operator believes
is in force but is not is worse than no limit.

**Uploads are checked for content/extension agreement.** An extension is a
claim made by whoever named the file; the first bytes are the evidence. An
`.xlsx` renamed to `.csv`, a CSV renamed to `.xlsx`, a legacy `.xls` posing as
a modern one, and binary junk are each refused by name, and the refused file is
deleted rather than left on disk. This replaces a pandas traceback from inside
a connector — which reads as "the tool is broken" — with "this file is not
what it says it is". It is not virus scanning and does not claim to be.

**Rate limiting is off by default and always exempts loopback callers.** This
is a local-first tool: the normal deployment is one person on localhost, where
a limiter would only ever throttle its own operator, and the first thing
anyone would do is turn it off — leaving it off in the one deployment that
needs it. Exempting local callers is what lets it stay on when the app is
exposed. Its limitations are real and are stated in
[SECURITY.md](SECURITY.md) §2.3 rather than implied away.

### Prompt versions in the cache key

The diagnosis and query caches were keyed on the failure group and the model,
not on the prompt. Adding redaction changed the prompt without changing the
key, so a pre-redaction answer would have been replayed for a redacted prompt.
Both caches now carry a `PROMPT_VERSION` in the key. This is generic: any
future prompt change needs a version bump, or it silently serves stale answers.


## Run comparison

`GET /compare?run_a=&run_b=` (or `?source_id=` for that source's two most
recent completed runs), and a Compare screen. Two completed runs of ONE source,
side by side across schema, data quality, exploration, analytics, marketing and
model score.

**Computed on read; nothing is persisted.** A comparison is a pure function of
two already-stored artifacts, so caching it would create a second copy of facts
the run rows already hold. More importantly it would go stale: a completed
run's artifacts are not actually frozen - confirming a column role recomputes
that run's analytics in place (`replace=True`), and recording a privacy
decision recomputes its classification. A stored comparison would be wrong from
the moment someone acts on the system, and nothing would notice. Recomputing is
a handful of JSON reads and dict diffs: no model call, no connector fetch, no
frame rebuild.

### The refusals are the feature

A comparison view exists to report change, so the dangerous output is not an
error - it is a plausible number. Four things are refused rather than rendered:

| Situation | What happens |
| --- | --- |
| Different sources | The whole comparison is blocked. Every column, rule and finding would read as "changed", which describes two unrelated datasets rather than a change over time. |
| A run that never completed | Blocked. Exploration and analytics run only on a completed run, so the diff would be against partial output. |
| **Different value bases** | Analytics is `not_comparable`, with both bases named. Every value figure is a sum over that basis; a unit-price basis and a `price x quantity` basis differ by orders of magnitude and both look reasonable in isolation. |
| **A baseline superseded between the runs** | Analytics is `not_comparable`. The later run's figures are measured against a different definition of normal, so a delta would mix a change in the data with a change in what it was compared to. |

An analysis present in one run only is reported as **membership** (`appeared` /
`disappeared`), never as a delta against nothing - `Membership` and `Delta` are
separate types precisely so the two cannot be flattened into one shape where
"appeared" renders as a change from zero.

A relative change from zero is reported as **no relative change**, not as 0%,
not as infinity, and not as "new" - each of those is a different wrong answer.

Finding identity is built from type and columns, never from the finding's id.
Exploration ids are positional (`correlation-3` is the third correlation), so
inserting one earlier finding renumbers every later one and an id-based match
would report them all as simultaneously disappeared and appeared.

Nothing in the comparison is causal, in output or in UI copy. Two runs differ
in every uncontrolled way at once.

## The Session Summary Agent (the ninth agent)

Four to eight plain sentences about a run, for someone who will not read the
statistics. `GET /summary/{run}`, shown at the top of the run view above the
detailed report, and on the deck slide directly after quality context.

**It summarises the PERSISTED ARTIFACTS, never the exported deck.** Reading our
own rendered output back in would mean the summary silently changes whenever
deck rendering changes, and two artifacts would have to agree forever - the
two-sources-of-truth failure this codebase keeps finding. It reads validation
events, exploration findings, business analytics, the marketing pack, model
runs, and the run comparison. `tests/test_session_summary.py` enforces this by
parsing the package's imports: nothing under `app/summary/` may import `pptx`
or `app.export`.

### Same constraints as the Narrative Agent, and mostly the same code

The grounding filter, the number rounding, the causal lexicon and three of the
four post-checks are **imported from `app/narrative/`, not reimplemented** - a
second copy would be two definitions of "a fabricated number" that must agree
forever.

| | |
| --- | --- |
| **Two stages** | Stage 1 turns facts into claims citing fact ids; stage 2 turns claims into prose. |
| **Stage 2 sees no artifact** | Structural, not a prompt instruction: `_build_stage2_prompt(self, claims)` has no parameter an artifact could arrive through, and a test asserts that signature. |
| **Grounding** | A claim citing a fact id this run does not have is dropped before anything downstream sees it. |
| **Post-checks** | Number fidelity, causal language, claim coverage, plus a length bound (4-8 sentences). Deterministic, every attempt, never model-judged. |
| **Quality context** | Rendered deterministically by the narrative's own renderer, stored in its own column, and always first. |
| **Template fallback** | Built from the same facts, and always states WHY it was used. |

The length check is this agent's own. Both ends matter: a one-sentence summary
has dropped most of what the run found while still reading as complete, and a
twenty-sentence one is the detailed report again, for the reader who was
promised they would not have to read it.

### The egress boundary

Both stages are outbound calls, both under the **strict** redaction policy, and
both write an `egress_events` row. A fact's `text` never embeds a column value;
values quoted out of real columns travel separately in `column_values`, keyed by
column name, precisely so `redact_records` can mask them - which is what makes
the redaction actually apply rather than silently matching nothing.

Stage 2 records an egress row carrying no columns at all. That is deliberate: it
discloses nothing new, but the call still happened, and a trail that omitted it
would understate how many times a run reached a third party.

### Where it sits in the graph

Its own node, `summarise`, after `narrate`. A node rather than a line inside
`narrate_node` because it is a distinct agent with its own failure mode, and
that reads more honestly in the trace. Like narration, it is a
**degrade-not-fail** concern: a run whose analysis succeeded is never reported
as failed because its summary could not be written.

## Marketing depth (Part 3)

Four analyses added to the existing pack, all deterministic and all
capability-gated like the seven before them. Registered in the same `RULES`
tuple, so the engine's guarantees - one rule never sinking the rest, stable ids
assigned over the final list - cover them unchanged.

| Analysis | What it reports |
| --- | --- |
| Period-over-period movement | How each ad set's CPA, ROAS and CTR moved between the last window and the one before it |
| Fatigue | An ad set whose frequency rose while its CTR or ROAS fell, over the same days |
| Spend concentration | Which ad sets consume the budget, and which return it |
| Efficiency ranking | Each ad set's CPA and ROAS against the account median, with the median's value stated |

### Two pieces of machinery are reused, not rebuilt

`Delta` (`app/comparison/models.py`, Part 1) computes every movement, including
its rule that a relative change from zero is **undefined** rather than infinite
or zero.

`run_abc_pareto` (`app/analytics/pareto.py`) computes spend concentration. It is
called **unmodified**, through a synthetic `RoleDetection` naming the ad set as
the item and spend as the monetary column. The band cutoffs, the concentration
floor, the held-out non-contributing entities and the "concentration is weak for
this data" note stay one implementation; ad-set spend is simply another subject
to run it over. Both reuses are asserted structurally, by parsing
`app/marketing/depth.py`'s imports and checking it contains no band arithmetic
of its own.

### The materiality floor

`min_relative_movement` (default 5%) exists because without it every ad set
reported a movement on every run - a CTR shifting by 0.002% is arithmetic, not a
finding, and a list of them buries the one move that matters. A move **from
zero** is exempt: `Delta.relative` is undefined there, and that IS material - a
metric appearing from nothing - so it is reported rather than filtered out by a
comparison it cannot be measured against.

### The median, not the mean

Efficiency ranking compares against the account median. One runaway ad set drags
a mean far enough that most ad sets sit "below average", which is arithmetic
rather than a finding. The median's **value** is stated in every finding's
`compared_against`, not merely the word.

### Nothing here is causal

A frequency trend rising while a performance trend falls is reported as two
co-occurring movements over the same period, in those words. That is what the
columns support; anything stronger would be a claim they cannot carry. And every
finding about a derived metric still states its within-run basis, because CPA,
ROAS and CTR are derived *after* baseline profiling and no stored baseline
exists for them.

### Interactivity

Selecting an ad set narrows the page to it. Deterministic and entirely local:
the findings are already persisted per ad set (`payload.scope`), so filtering is
a filter over what the run produced - no new request, no model call. An E2E test
counts backend requests across the interaction and asserts zero. Account-wide
figures stay visible while a filter is on, because an ad set is read against the
account it sits in.

## The Agriculture Agent (the second domain pack)

Crop production statistics: yield, area and rainfall by district, crop and
season. Built to the Marketing Agent's structure - the same eight modules,
the same three severities, the same "a row is written even when the run does
not qualify" contract - because that structure is the extensibility claim and
this is the test of it.

### The two traps, guarded explicitly

Four detector bugs in this project came from a plausible-but-wrong role
assignment producing confident nonsense. This domain has two obvious ones.

**A crop year is a label, not a measure.** `Crop_Year` holds integers like
2015-2022: non-negative, whole, low cardinality. It satisfies every shape test
area, production, rainfall and price apply, and summed it yields a "total
production" in the millions that looks entirely plausible. Every agricultural
measure scorer refuses a column that reads as a year, by name **or** by value
range - the second half matters for a column named `Production_Year`, which
matches the production hint and would otherwise be taken as production.

**Area, production and yield are mutually confusable.** All three are
non-negative continuous numerics on the same rows, and shape cannot separate
them: a yield of 2.5 t/ha and an area of 2.5 ha are the same number. So for
these three the NAME IS MANDATORY - a column with no matching name hint scores
zero rather than taking a shape-only score, and each scorer stands down when a
more specific sibling name matches. Refusing to fill the slot is the correct
output when the only available evidence cannot distinguish the three.

### Preprocessing states everything it did

**Zero area never produces an infinity.** A crop listed but not sown is common
in real crop statistics, and `production / 0` gives `inf`, which propagates
through a mean and emerges as a plausible average. Those rows are counted,
excluded from the derived yield as NULL, and reported with the count.

**Every label normalisation is recorded.** Real season labels arrive as
`"Kharif     "`, `"kharif"` and `"Kharif"`; districts as `PALAKKAD` and
`Palakkad`. Collapsing them is necessary to group at all, and doing it silently
would leave a reader unable to tell whether two districts merged because they
are the same place or because the normaliser was too aggressive.

**The grain is stated and configurable** (district-crop-season by default). A
yield averaged over the wrong grain is a different number that looks equally
reasonable. The crop year is always part of the grain when present - collapsing
across years would silently average a district with its own history.

### Rules

| Severity | Rules |
| --- | --- |
| WARNING (escalates) | yield collapse against the district's **own** history, production drop beyond a margin, rainfall outside the season's own range, sharp fall in area |
| IMPROVEMENT | districts below the median yield **for that crop**, high yield variance |
| KEY VALUE | total area, total production, average yield with its basis, top crops, rainfall summary |

Every history rule compares a district-crop with **its own past**: soil,
rainfall and crop mix differ between districts, so a cross-district comparison
dressed as a collapse would be a false alarm every season. Below-median is per
crop, since rice and coconut yields differ by orders of magnitude and one
pooled median would rank every low-yield crop as underperforming.

Nothing here is causal. A yield falling in a season when rainfall also fell is
two measurements over the same period; this pack reports both and connects
neither.

### The domain-pack registry

`app/domain_packs.py` exists because the SECOND pack needed it. The run
comparison and the Session Summary both read the marketing table by name -
correct when there was one pack, a special case the moment there were two. They
now iterate the registry, so a third pack means appending one entry rather than
editing any consumer.

## Known issues

**Under concurrent load with SQL or API sources, source fetches still hold a DB session and could exhaust the connection pool; file sources are unaffected** (details in `docs/EVIDENCE.md` §5.8).

**`GET /ingest/{run_id}/status` is O(source size).** `_serialize_run_response`
rebuilds the repaired frame through the connector on every call, purely to
compute `metadata` (row count, column types, encoding). On a 1.07M-row,
94MB CSV that is 30.9 seconds of the 30.8-second response. Measured:

| endpoint | time | payload |
| --- | --- | --- |
| `GET /audit/6` | 0.35s | 3.9 KB |
| `GET /analytics/6` | 0.28s | 67 KB |
| `GET /ingest/6/status` | **30.8s** | 1.2 KB |

The audit query itself is fine (6 trace rows, 0 validation events). Every
page using the run picker pays this to populate its column hints, which is
why `/audit?run=6` appears to hang.

**FIXED.** The metadata is now cached on `Run.contract_metadata` when the
frame is already in hand (`app/graph/nodes.py`, both `ingest_node` and
`explore_node`), and refreshed whenever resolution changes `fix_chain` so a
run paused at `awaiting_approval` cannot serve a description of its pre-fix
frame. A run with no cached value falls back to the live rebuild, so runs
that completed before the column existed keep working. Held by
`backend/tests/test_run_metadata_cache.py`, which asserts the endpoint's
answer matches the frame the run actually has. The measurement above is
retained as the record of what the problem was.

**The repair path does not recover numbers from human-formatted text.**
A CSV column holding `$1,234.56`, `1.234,56`, `10 000` or `2.3%` arrives as
an object-dtype column of strings and stays one: encoding, delimiter, null
and dtype handling all run, but nothing parses a number back out of a string
a tool formatted for a person to read. Every downstream consumer then sees
text where it expected a measure - the column is not profiled numerically,
not correlated, not chartable, and not detectable as a monetary role.

Found through the marketing roles (an ad export writes spend as `$120.00`
and CTR as `2.30%`, so neither was detected and the agent refused files it
exists for), but it is **generic to any CSV with formatted numeric columns**
and is not specific to advertising data.

Current mitigation is partial and deliberately narrow. `app/numeric_text.py`
is one shared parser handling currency symbols, thousands separators,
European decimal commas and percent signs; `app/analytics/roles.py` reads
*through* it when scoring the marketing roles, and
`app/marketing/preprocess.py` uses it to transform for real, recording every
transformation in its output. Two callers, one implementation. But the
generic repair path is untouched: a formatted-number column in a
non-marketing CSV still reaches exploration, profiling and the baseline as
text. Making it general would mean parsing during repair, which changes what
every downstream agent sees and what a baseline profiles - a contract
decision, so it is recorded here rather than taken unilaterally.

**Quantity detection rejects real wholesale data.** `Quantity` on Online
Retail II fails three guards written for small-basket retail: it contains
negatives (22,950 returns), its maximum is 80,995 (cap 1,000), and it has
1,057 distinct values (cap 50). The line-total derivation therefore does not
fire on that dataset until a human confirms the role - which the
confirm-a-role flow offers, listing `Quantity` first with the detector's own
reason for rejecting it. Relaxing all three thresholds would let the
inflating error in through a wrongly-detected multiplier, so it is a
deliberate open question rather than a quiet re-tune.
