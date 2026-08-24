# Security model

This document states what this system defends against, what it does not, and
why. The second list is longer than the first, and that is the point: a
threat model that only lists wins is a marketing document.

**What this system is.** A local-first data-analysis tool. One person runs
`start.ps1`, points it at their own data, and drives it from a browser on the
same machine. It sends samples of that data to a third-party language model.
Everything below follows from those two facts.

---

## 1. What is defended

### 1.1 Personal data leaving for a third-party model

The one threat this system is genuinely built around, because it is the one it
creates by existing. See README §"Personal data" for the design.

| | |
| --- | --- |
| **Detection** | Deterministic — regex and checksum, no language model. Using a model to find personal data would mean sending the data to find out whether it should be sent. |
| **Boundary** | One redaction function, called on each of the four outbound paths. `RedactedSample` is the only type the agents accept, so skipping redaction at a call site is a type error rather than a review note. |
| **Default** | Default-deny on unverified candidates for diagnosis, query and modeling. Permissive — verified PII only — for narrative grounding, because masking candidates there was measured to destroy findings. Verified PII is masked on every path. |
| **Storage** | The stored frame is never mutated. Only the outbound copy is masked. |
| **Record** | Every outbound call writes an `egress_events` row: agent, provider, model, policy, columns, counts. Never the payload. |

**Residual risk, stated plainly.** Low-confidence personal data — a person's
name in a free-text column, an address split across fields — is not detectable
from values, and this system does not pretend to detect it. It is masked by
default on three paths and *sent in the clear on the narrative path*. A person
can mark a column personal or not personal, and until they do, the narrative
path is where unverified personal data can leave. That is a deliberate,
measured trade (README §"Default-deny on candidates"), not an oversight, and it
is visible in the Privacy section of every report.

### 1.2 Prompt injection via data content

Sample rows and findings reach the model inside explicit delimiters, with a
system prompt instructing that everything between them is data and never an
instruction. The model's response is parsed into a closed Pydantic schema; a
free-text or out-of-enum action cannot reach the caller. Any fix the model
suggests passes through a deterministic gate with an allowlisted action set
before anything is applied.

**Residual risk.** Delimiters and instructions are mitigation, not proof. A
sufficiently clever payload may still influence a diagnosis. What bounds the
damage is the layer separation: the model can only *suggest*, from a fixed
action vocabulary, and every applied fix is verified by post-condition and
reverted if it fails.

### 1.3 Generated code

The Query Agent's output is validated before execution — Python by AST walk
against an allowlist, SQL by `sqlglot` parse with a table allowlist. Neither
is executed as free text.

### 1.4 Credentials

`connection_config` stores an environment variable *name*, never a resolved
secret value. The guarantee is structural rather than a filter applied when
sources are read, so `GET /sources` has nothing to redact.

### 1.5 Resource exhaustion from one request

| Limit | Default | Env var |
| --- | --- | --- |
| Upload size | 200 MB | `MAX_UPLOAD_BYTES` |
| Ingest rows | 2,000,000 | `MAX_INGEST_ROWS` |
| Ingest columns | 4,096 | `MAX_INGEST_COLUMNS` |
| Requests per window | 60 / 60s | `RATE_LIMIT_REQUESTS`, `RATE_LIMIT_WINDOW_SECONDS` |

Every one of these **refuses**; none truncates. Analysing the first two million
rows of a larger file and reporting the result as the dataset's profile would
be a baseline, a null rate and a Pareto band computed over part of the data
with nothing in the report saying so. The row and column limits live on
`DataContract`, the one type every connector produces, so no ingest path
bypasses them. An unparseable limit override raises at startup rather than
silently reverting to the default.

### 1.6 File type confusion

An uploaded file's extension is a claim; its first bytes are checked against
it. An `.xlsx` renamed to `.csv`, a CSV renamed to `.xlsx`, a legacy `.xls`
posing as a modern one, and binary junk are each refused with a message naming
what was found. A refused upload is deleted rather than left on disk.

This is not virus scanning and does not pretend to be. It catches the case
that actually happens, and replaces a pandas traceback from deep inside a
connector — which reads as "the tool is broken" — with "this file is not what
it says it is".

### 1.7 Path traversal on upload

Only the basename of the client's filename is used, and only its extension is
read. A crafted `../../etc/passwd` cannot write outside the upload directory.

---

## 2. What is NOT defended

Read this section as the real threat model.

### 2.1 There is no authentication, and therefore no authorization

**Anyone who can reach the port can do anything.** Read every source, ingest
any file the server process can open, ask any question, approve any pending
fix, mark any column not personal, and read every audit trail. There are no
accounts, no sessions, no API keys, no roles.

Attribution fields — `resolved_by`, `confirmed_by`, `marked_by` — are free
text supplied by the caller. They are labelled as attributions everywhere they
are displayed, because they are **not** identities and nothing verifies them.
A field named "who marked this column safe" that anyone can fill in with any
name is a record of a claim, not of a person.

This was an explicit scope decision, not an omission. **Do not expose this
service to a network you do not fully control.**

### 2.2 There is no encryption at rest

The database, uploaded files, run snapshots, and the diagnosis cache are all
plaintext on local disk. Anyone with filesystem access has the data. Disk
encryption is the operating system's job here.

### 2.3 The rate limiter is narrow

It counts requests per client address in a fixed window, in memory, for
mutating and expensive endpoints only. It therefore does **not** defend
against:

- **A distributed source.** N addresses get N windows. This is not DDoS
  protection and cannot be.
- **A spoofed address.** The address comes from the connection, never from
  `X-Forwarded-For` — a header the client controls is a header the client can
  use to get a fresh bucket. The consequence is that **behind a reverse proxy
  every request appears to come from the proxy and shares one window.** Let
  the proxy do this instead.
- **Multiple workers.** Counters are per process and not shared.
- **Cost after admission.** One permitted ingest of a large file costs what it
  costs; `MAX_INGEST_ROWS` is what bounds that, not the limiter.

It is **off by default** (`RATE_LIMIT_ENABLED`) and always exempts loopback
callers. A limiter that throttled the local operator would be turned off
everywhere, including the one deployment that needs it.

### 2.4 CSV cell content is not sanitised

A cell beginning `=`, `+`, `-` or `@` is a formula to Excel and Google Sheets.
This system reads such values as text and never evaluates them.

The exposure here is narrower than the usual write-up of this issue, and worth
stating precisely rather than borrowing the generic warning. This system's only
export format is PPTX (`POST /export/{run_id}/pptx`), and PowerPoint does not
evaluate cell text — so nothing this system *produces* is a spreadsheet that
would execute a formula. What it does do is carry such values through
unmodified into reports, the dashboard, and deck text.

The residual risk is therefore downstream: if you copy values out of a report
into a spreadsheet, that spreadsheet's formula behaviour applies. Stripping the
characters here would corrupt legitimate negative numbers and legitimate text
in a data-quality tool whose entire job is to report values faithfully, so the
choice is to leave them intact and say so.

### 2.5 The model provider sees what is sent

Redaction controls *what* is sent, not what the provider does with it. Prompts
reach a third party subject to their retention and training policies. The
egress trail records that a call happened and what shape it had; it cannot
constrain the recipient.

### 2.6 SQL and API sources are trusted inputs

A SQL connector runs the query in its `connection_config` against the
credentials it is given. An API connector fetches the URL it is given. Neither
is a sandbox: whoever can create a source can make the server issue those
requests, which on an exposed instance is server-side request forgery. See
§2.1 — this is another consequence of having no authentication. What limits it
today is that those requests only happen when a person triggers an ingest
(§2.7).

### 2.7 There is no scheduled or continuous monitoring

The platform validates **on demand**. A run happens when a user triggers an
ingest (`POST /ingest/{source_id}`), or when a human resolves an escalation
and the paused run resumes. There is no scheduler, cron, timer or file
watcher, and no scheduling dependency. Drift detection works across repeated
ingests of the same source, but every one of those ingests is user-initiated.

This belongs in a threat model because it cuts both ways.

**It reduces attack surface.** There is no unattended code path that fetches a
URL or opens a file on its own. That materially bounds §2.6: a hostile source
registered on an exposed instance causes an outbound request only when
somebody triggers an ingest of it. Were a scheduler ever added, a source
registered once — which §2.1 says anyone who can reach the port may do —
would become a recurring, unattended outbound request from this host, and
§2.6 would become considerably more serious than it currently is.

**It also means nothing here will alert you.** This is not a detection system.
It will not notice that a file changed, that a feed started returning nulls,
or that a source was tampered with, until a person asks it to look. Any
expectation of timely detection has to be met by whatever schedules the
ingest — this platform is the thing being called, not the thing doing the
calling.

### 2.8 Dependencies are not audited here

No supply-chain verification, pinning policy, or vulnerability scanning is
part of this system.

### 2.9 The audit trail is not tamper-evident

`egress_events`, `agent_traces` and validation events are ordinary rows in the
same database everything else uses. Anyone who can write to that database can
edit or delete them. The trail answers "what happened" for an operator
investigating their own system; it is not evidence against someone with
database access.

---

## 3. Deployment guidance

**Supported:** localhost, single operator, single process — what `start.ps1`
runs.

**If you expose it anyway**, and understand §2.1:

1. Put it behind a reverse proxy that terminates TLS and enforces
   authentication. The proxy is the security boundary; this app is not.
2. Let the proxy rate-limit. Its view of client addresses is the correct one.
3. Set `FRONTEND_ORIGINS` to the exact origin you serve, not the default
   localhost list.
4. Restrict which filesystem paths the process can read: `POST /sources` with
   `{"path": ...}` will open any file the process can open.
5. Set the limits in §1.5 to values suited to your hardware.

---

## 4. Reporting a vulnerability

This is a portfolio project, not a deployed service. There is no bounty and no
disclosure timeline. Open an issue, or contact the repository owner.
