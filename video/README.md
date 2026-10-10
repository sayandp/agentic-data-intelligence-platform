# Demo videos

Two cuts of one story, rendered with [Remotion](https://www.remotion.dev) from
**real screenshots of the running dashboard**. Every dashboard frame is the
app's own render of a real run in the local database; no UI is mocked and no
run is invented. The title cards, the architecture diagram, the FixAction
strip and the stat tiles are motion graphics, not imitations of a screen.

| File | Format | Length | Size |
| --- | --- | --- | --- |
| `out/agentic-platform-horizontal-v2.mp4` | 1920×1080, H.264 yuv420p + AAC, faststart | 1:15 (75.0 s) | 22.5 MB |
| `out/agentic-platform-vertical-v2.mp4` | 1080×1920, H.264 yuv420p + AAC, faststart | 0:39 (39.0 s) | 10.5 MB |
| `out/agentic-platform-horizontal-v2-nomusic.mp4` | same picture, SFX only, −16 LUFS | 1:15 (75.0 s) | 22.2 MB |
| `out/agentic-platform-vertical-v2-nomusic.mp4` | same picture, SFX only, −16 LUFS | 0:39 (39.0 s) | 10.3 MB |
| `out/agentic-platform-horizontal.mp4` (v1) | 1920×1080, H.264, yuv420p, faststart, silent | 1:57 (117.4 s) | 24.1 MB |
| `out/agentic-platform-vertical.mp4` (v1) | 1080×1920, H.264, yuv420p, faststart, silent | 0:54 (54.0 s) | 15.6 MB |

- **v1** is published on the GitHub release `demo-video-2026-10-10`. It has no audio track: its brief allowed CC0 music or none, and the bundled library is Mixkit-licensed.
- **v2** has music and SFX, which the v2 brief allows (Mixkit Free License, credited below). It is **not published yet** and is waiting for approval.
- `out/` is gitignored. The v2 end card shows the repo URL and, under it, the byline "Sayand" (the `byline` prop).
- Both versions carry burned-in English text on every shot, because most viewers watch muted.

## v2: why v1 felt flat

Measured on v1's own timeline (`src/scenes/FootageH.tsx`, `Horizontal.tsx`):

- **Shots ran long.** About 30 shots in 116 s, so the average was 3.9 s. The
  hero (detect → diagnose → act) was one 16.3 s continuous camera over a
  single card. The architecture diagram sat for 12 s, stat tiles for 7 s,
  and each domain pack for 4.3 s. Nothing ever cut on a beat.
- **Pages sat there.** Most shots were a slow 1.3–1.8× push over a static
  screenshot. State changed in only four places (the upload, the Keep click,
  Acknowledge and the deck click), so most of the runtime showed a page
  rather than something happening.
- **Too much to read.** Captions averaged about 16 words over two lines, under
  a five-step rail. A viewer reads instead of watching, and on a phone the
  band covered a quarter of the frame.
- **No sound.** With no music, no transient on a cut and no click, the edit had
  no pulse, and slow pacing reads as slower still in silence.
- **Flat staging.** Full-bleed screenshots with no depth, frame, parallax or
  grain. Every transition was a soft blur or fade, with no punch-in, flash or
  match cut, and the energy curve stayed level from hook to CTA.
- **Covering everything.** v1 covered every feature. It had no single moment
  built to be remembered, so nothing peaked.

v2 answers each point. It cuts on a 120 BPM grid (15 frames per beat), keeps
shots to 1.5–2.5 s, puts no more than 6 words on screen, builds every scene
around a visible change, adds music and SFX, uses depth (device frame,
parallax, grain), and is built around five hero moments.

## v2: how it was made

`src/v2/` (`Horizontal2` and `Vertical2` compositions). It reuses v1's captures and adds only:
- `scripts/capture_v2.mjs`:
  - one frame per typed character of the Ask question, so the typewriter is the dashboard's own render
  - run #648's Audit page after the on-camera decision, so the status flip cuts between two real states
- `scripts/v2_data.py`: the seeded orders rows, plus the tokens produced by the app's **own** redactor (`app.privacy.redaction`, imported read-only): `<EMAIL_1>`, `<ADDRESS_1>`, and so on.

**Grid.** `scripts/beat_grid.py` fits House Vibez at 122.00 BPM (98.1% match, 8.6 ms mean error, `analysis/beat_house-vibez.json`). The track plays at 120/122.00, so every beat is exactly 15 frames, and scenes are placed in beats (`b(n)`). The cut was checked on the render: the music's beats land 0.5 f (median) from the cut grid, 90% within 0.9 f.

**Five hero moments.**
- **Broken → healed.** Run #647's file glitches on `town`, the stored diagnosis snaps in, and the column heals row by row with a check mark. Split-screen before/after. Then the revert of run #648: the cast is attempted, fails on `'$80.90'`, the VHS rewind plays, and the real Audit row says `(reverted)`.
- **Human in the loop.** The pointer glides to Keep, the frame freezes with a hand-drawn circle, then click and ripple. The cut lands on the same row reading `rejected · dashboard-user`.
- **Ask anything.** The real per-key frames type the question, the generated SQL glows, and the real JSON result is revealed line by line on the drop.
- **The report writes itself.** The stored narrative is revealed line by line over the real pixels.
- **Trust.** The redactor's real tokens replace the values, the egress row pops, and the stat cards count up.

**Techniques.**
- 2.5D browser frame with perspective and orbit
- crash-zoom punch-ins (6f ease-in) with a zoom-blur trail
- a spotlight that dims everything but the element named in the caption
- whip-pan seam (ask → report)
- diagonal theme sweep across Default → Dark → Aurora
- odometer reels on the EVIDENCE.md figures
- flash and light-leak seams on the downbeat
- split-screen before/after
- glass-panel architecture with particles on a drawn path and glowing LLM agents
- stat cards with their source line
- pointer with click ripples
- parallax ground
- grain and vignette
- RGB-split glitch
- VHS rewind
- mosaic-to-token
- kinetic typography (≤ 6 words, accent words)

The colours are the dashboard's own **Aurora** theme. DESIGN.md defines it as the opt-in skin "to look striking in a live demo", so the glass and glow are still the product's visual language.

**Sound.** Music sits under SFX, every cue placed on beats (cue tables in `src/v2/scenes.tsx`). `scripts/master_audio.py` sets the level to −14 LUFS integrated behind a look-ahead peak limiter: `node scripts/finalize.mjs v2` muxes the result as AAC 192 kbps. Measured on the delivered files:

| | Integrated | Peak (sample) | Peak (true, master) | Beat offset |
| --- | --- | --- | --- | --- |
| Horizontal | −14.19 LUFS | −1.40 dBFS | about −1.05 dBTP | +0.48 f median |
| Vertical | −14.18 LUFS | −1.43 dBFS | about −1.15 dBTP | +0.51 f median |

The beat offset is how far the music's beats in the render sit from the cut grid; for both cuts, 90% of beats are within 0.86 f (tolerance 3 f).

**No-music cuts** (`*-v2-nomusic.mp4`) are the same picture with an SFX-only track. `music:false` renders the audio from the same timeline, which is muxed onto the v2 video stream. That track is mastered to **−16 LUFS integrated, true peak ≤ −1 dBTP**, which is better for presentation rooms (`master_audio.py --target -16 --ceiling -2.0`). The extra ceiling headroom covers AAC overshoot.

**Honesty notes for v2.**
- **Data tables.** These are drawn from the seeded file, not from a screen, and each carries a label saying which file and run it is.
- **The cast-attempt and rewind shots are recreated, and say so on screen.** No run captured the attempt as footage: the cast is tried and rejected inside `app/repair.py`, and the dashboard never renders it. Both shots carry the label "Recreated from run #648 · real audit row follows" (32 px, top of frame). The very next shot is run #648's real Audit row reading `auto_fix_reverted (reverted)` with the post-condition message. The table values are the real rows of the file run #648 ingested.
- **Thumbnails are real footage.** Both are run #648's real Audit row with the `(reverted)` cell circled, never the recreated shot.
- **Agent count re-done from the current code.** §1.1 was re-counted on 2026-10-10 at `367b965`. It lists 11 agents, each with its module and the file that writes its trace, and 5 of them use an LLM. The old nine is kept there, marked historical. The How scene shows "11 agents. Each one job." with "EVIDENCE.md §1.1" as the source line. The diagram includes Session Summary (LLM) and Agriculture, and the particle path runs through all 11.
- **Odometer reels.** Only EVIDENCE.md figures get them; the narrative's own numbers are shown as the stored text.
- **No "four planes" claim.** "Four planes" was cut from the captions because EVIDENCE.md has no plane breakdown to back the number. The plane names stay as diagram labels.
- **LLM glow.** The diagram lights the five LLM users in the current EVIDENCE §1.1: diagnosis, query, modeling intent, narrative and session summary.

### Independent review and what changed

A separate reviewer agent critiqued the first v2 render against the shot cards and both checklists. Its top notes, and the fix for each:

1. **The How scene was one 10 s shot, illegible on a phone.** It is now four 2.5 s shots: wide, a crash-zoom onto Ingestion + Reliability, a cut to Intelligence + Delivery, then a pull-back. The panels no longer drift, and a scrim sits behind the caption.
2. **The average shot was about 3.0 s.** Long shots now cut hard on beats inside a scene:
   - Ask: typing → tighter crop → SQL → the drop → lower rows
   - Report: summary → narrative → the claim link
   - Caught: row → reverted → post-condition
   - Trust: one stat card per shot
   - Domains: two full-width shots instead of a split

   That makes about 40 shots in 75 s.
3. **Footage text was too small to read.** Footage zooms are now 2.2–2.4×; going further would upscale the 2× captures. A spotlight dims everything outside the element the caption names.
4. **The zoom-blur trail was invisible.** The ghost layers sat under the opaque frame; they now draw on top. Punch-ins are 6f ease-in crashes, as the card specifies.
5. **There were too many full-frame flashes.** Each cut now has two, at least 16 beats apart. The other seams are cuts, light leaks or the whip.
6. **"Four planes." had no source in EVIDENCE.md.** It was removed.
7. **Text density was too high.** Stat cards are now a number, a 2-word label and a source footer. The end card is the title, the URL and the handle.
8. **Visible seams.** The typing frames now play on their own, not composited. The narrative mask is one contiguous block with a caret on the reveal edge. Also fixed:
   - the rewind now plays at 1× (no dead tail)
   - the freeze lasts 45f before the click
   - the ripple finishes before the cut
   - the vertical URL breaks at "/"
   - the band left behind by the heal wipe is gone
   - the blank frame on each stat-card cut is gone

**Deviations kept on purpose:**
- The crash-zoom holds 8–10f before pushing; the card asks for at least 30f, which would break the 1.5–2.5 s shot target.
- Motion blur is a two-ghost trail rather than `CameraMotionBlur`.
- The before/after is a side-by-side split followed by a cut, not the slider card.
- The kinetic type is a blur-slam rather than either of the type-entrance card's two styles.

### Soundtrack credits

Music: **House Vibez**, Lily J, Mixkit Stock Music Free License, https://assets.mixkit.co/music/745/745.mp3

Sound effects are all under the Mixkit Sound Effects Free License, via the video-shotcraft bundle. Each one has a traceable source; the list with URLs is in `scripts/audio_assets.mjs`:
- Glitch static
- Virtual quick glitch
- Digital glitch break
- Data scaner
- Fast whoosh transition
- Quick air woosh
- Fast small sweep transition
- Fast transitions swoosh
- Quick switch click
- Crystal chime
- Magic sparkle touch
- Sweeping sparkle presentation intro
- Fast tape rewind cinematic transition
- Short bass hit
- Big cinematic impact
- Exciting fast hit
- Weak hit impact
- Digital typewriter
- Fast swipe zoom

## v1: how it was made

1. **Stack.** `start.ps1`, then a guard (`scripts/guard.mjs`) that refuses to
   film unless `:5173` serves *this* app: page title, an app-specific heading,
   and a healthy backend on `:8000`.
2. **Data.** Existing completed runs were filmed where they existed (report,
   analytics, forecast on run #608; Marketing #385; Agriculture #384). The
   database had **no auto-applied fix and no revert**, so
   `scripts/seed_demo_runs.py` created them through the public API only, on a
   small seeded synthetic orders file (`random.Random(7)`, fake
   `@example.com` emails):

   | Run | What was ingested | What the system decided |
   | --- | --- | --- |
   | #646 | clean orders | baseline established |
   | #647 | `city` arrives as `town` | `rename_column` diagnosed (0.95), gate passed, **auto-fixed and kept** |
   | #648 | `price` arrives as `"$80.90"` text | `safe_type_cast` diagnosed (0.98), gate passed, post-condition failed, **reverted and escalated** |
   | #649 | same rows over a loopback HTTP API | completed |
   | #650 | same rows from a SQLite table (SQL connector) | completed, connector warning |
   | #651 | a two-sheet `.xlsx` | completed, connector warning |

   Those outcomes were read back from the database, not assumed. The capture
   then resolved #648 ("Keep data as-is") and acknowledged one connector
   warning, on camera. The pre-existing escalation on run #2 was left alone.
   Gemini calls made for the video: the diagnoses, narratives and summaries of
   these six runs (plus the on-camera upload), one Ask, and one Predict.
3. **Capture.** `scripts/capture.mjs` (Playwright) saves full-page
   screenshots at 2× (1440×900 viewport) and 3× (390×844), the before/after
   frames of every click, and element coordinates. Scrolling, the pointer and
   the freeze-and-circle rings are camera work in Remotion over those frames,
   placed on coordinates measured from the captures.
4. **Edit.** `src/` — shotcraft camera moves (crane rise on the report, focus
   handoff between shots, whip-pan between insight screens, freeze-and-circle
   on the revert and the decision buttons), styled with the dashboard's own
   tokens from `DESIGN.md` (stage = sidebar navy, accent = the Dark theme's
   signal teal, system UI + mono data faces, flat 1px-bordered surfaces).

## Re-render

```powershell
.\start.ps1                       # with $env:ADIP_DEMO_SQL_URL set, see below
cd video
npm ci
..\backend\.venv\Scripts\python.exe scripts\seed_demo_runs.py   # new runs; re-check outcomes
node scripts/capture.mjs          # aborts unless :5173 is this app
npx remotion render Horizontal out/raw/agentic-platform-horizontal.mp4
npx remotion render Vertical   out/raw/agentic-platform-vertical.mp4
node scripts/finalize.mjs         # -> out/*.mp4: yuv420p (limited range), +faststart, < 50 MB check
node scripts/frames.mjs           # out/frames/*.png + thumbnails

# v2
node scripts/capture_v2.mjs       # typing frames + the resolved Audit page
..ackend.venvScriptspython.exe scripts2_data.py
$env:SHOTCRAFT = "<path to the video-shotcraft skill>"; node scripts/audio_assets.mjs
npx remotion render Horizontal2 out/raw/agentic-platform-horizontal-v2.mp4
npx remotion render Vertical2   out/raw/agentic-platform-vertical-v2.mp4
uv run --with pyloudnorm --with soundfile --with numpy --with scipy --python 3.11 scripts/master_audio.py out/raw/agentic-platform-horizontal-v2.mp4 out/raw/agentic-platform-horizontal-v2.wav
uv run --with pyloudnorm --with soundfile --with numpy --with scipy --python 3.11 scripts/master_audio.py out/raw/agentic-platform-vertical-v2.mp4 out/raw/agentic-platform-vertical-v2.wav
node scripts/finalize.mjs v2      # -> out/*-v2.mp4 with the mastered AAC track
node scripts/frames_v2.mjs        # out/frames-v2/*.png + v2 thumbnails
```

The SQL source reads its URL from the backend's environment:
`$env:ADIP_DEMO_SQL_URL = "sqlite:///D:/main_project/video/capture/data/orders.db"`
before `start.ps1`. A re-seed creates new run numbers, and the captions name
#647, so update them in `src/scenes/FootageH.tsx` / `FootageV.tsx` if they move.
The CTA byline is a prop: `--props=props.json` with `{"byline": "…"}`.

## Every figure on screen, and where it comes from

Generated by `node scripts/figures-table.mjs` from `src/claims.ts`, the only
place the video's own figures are defined.

| On screen | Meaning | Provenance | Kind |
| --- | --- | --- | --- |
| **11** | agents (v2; v1 showed the historical nine) | EVIDENCE.md §1.1 (current count, re-counted 2026-10-10 at 367b965: 11 agents, 5 use an LLM) | code inventory |
| **5** | actions in the closed FixAction enum | EVIDENCE.md §3.1 (Closed FixAction enum: five members) | code inventory |
| **11/12** | diagnoses correct | EVIDENCE.md §4 (Diagnosis accuracy, scripts/part6_evaluation.py) | live model |
| **0** | wrongly auto-fixed, at all 10 thresholds | EVIDENCE.md §4 (Wrongly auto-fixed across sweep, part6_wrongly_auto_fixed_replay.py) | cache replay, 0 live calls |
| **2 of 6** | auto-applies reverted by their post-condition | EVIDENCE.md §4 (Post-condition reverts) | cache replay |
| **0** | data values stored in the egress trail | EVIDENCE.md §4 (Egress trail leak scan) | tests + live E2E |
| **1075** | backend tests passed (full run, 2026-10-10) | EVIDENCE.md §1.5 (Backend full-suite pass, re-run 2026-10-10 at 091484e: 1075 passed, 0 failed) | test suite |
| **0** | contrast failures; 910 text nodes measured in each of 3 themes | EVIDENCE.md §4 (Contrast audit) | real browser |

| Statement (no figure) | Backed by |
| --- | --- |
| The model chooses WHICH allowlisted fix; the code decides HOW. | EVIDENCE.md §1.1 (Diagnosis row) + §3.1 (build_fix_spec: parameters from validation events, never the diagnosis) |
| Stage 2 never sees the raw findings, so it cannot invent numbers. | EVIDENCE.md §3.1 (generate_prose takes only grounded claims) |
| A language model is called only where language is needed: diagnosis, query, modeling intent, narrative. | EVIDENCE.md §1.1 (Uses an LLM? column); Session Summary prose: §5.5 |
| No model configured? Reports fall back to a labelled template. | EVIDENCE.md §5.3 (Degrade gracefully) |
| No scheduler — runs start on ingest. | EVIDENCE.md §6.12 |
| A new domain = roles + rules + a view. | EVIDENCE.md §5.2 and §5.6 (pack-local surface: rule pack, router, page; roles in analytics/roles.py) |
| PII is masked before an outbound model call; the egress trail records shape only. | EVIDENCE.md §1.3 (egress_events: shape only) + §4 (PII detection tiers, egress leak scan) |

v2 shows `11` (agents), `5`, `11/12`, `0` (wrong auto-fixes) and `1075`.
- `11` and `1075` come from EVIDENCE.md §1.1 and §1.5, re-measured on 2026-10-10; the earlier `nine` and `874` are kept there, marked historical.
- v1 shows `Nine`, `5`, `11/12`, `0`, `2 of 6`, `0` (egress) and `0` (contrast).

**Numbers inside filmed dashboard frames** (run numbers, row counts, the
egress "41 distinct values", the forecast's RMSE, the Ask result) are not
claims made by the video. They are what those runs returned, shown as the
dashboard rendered them.

### Corrections made against the brief, from EVIDENCE.md

- **LLM use.** The brief said only diagnosis, query and narrative grounding
  call a model. EVIDENCE §1.1 lists **four**: diagnosis, narrative, query and
  *modeling intent*, and the egress trail also shows the Session Summary's
  stage-2 prose call (§5.5). The architecture diagram marks all of them.
- **"Live degrade-gracefully under a real outage"** is not recorded as a live
  result in EVIDENCE.md: §5.3 backs the template fallback with tests and
  §5.7 with a regression test that drives a real client raising
  `ConnectError`. The video says only "with no model configured, reports fall
  back to a labelled template".
- **"Revert byte-identically"** is not stated in EVIDENCE.md, so the video
  says "the data stays as it arrived" (the code returns the frame it was
  given when a post-condition fails, `app/repair.py`).

## Known things the footage shows

- The dashboard is **not responsive**: at a 390px viewport the sidebar stays
  and the page renders about 790 CSS px wide. The vertical cut frames the
  content column of those captures; it does not show a mobile layout.
- The Compare page renders a second card titled **"Marketing"** where the
  agriculture comparison belongs. The video frames above it. (Not fixed here:
  app code was read-only for this work.)
- The Predict chart's last actual month drops toward zero: a partial month in
  `demo_full.csv`. Real data, framed on the performance card.
