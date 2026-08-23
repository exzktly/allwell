# Analyze audit — remediation tracker

An adversarial audit of the Analyze pane and its pipeline returned **69 findings**
across five independent reviews, published as
[the defect audit](https://claude.ai/code/artifact/0207cfa1-22fd-498a-b517-d046284717b6).
This file tracks what has been fixed, what is in flight, and what is still owed.
It is the one place the state of that work is written down.

**This document is temporary.** `_Docs/Markdowns/` holds three permanent
documents by design (CLAUDE.md §5); this is a fourth, kept only while the
remediation is in progress, for the same reason
`GOD_OBJECT_REPOSITORY_ROADMAP.md` is kept — it is a live tracking document
rather than a write-up. **Delete it in the PR that closes the last group.**

## How to read the state column

| State | Meaning |
|---|---|
| `done` | Fixed, with a regression test watched failing against the unfixed code, and merged or in an open PR |
| `in progress` | Being worked now |
| `next` | The group currently queued to start |
| `queued` | Planned, not started |
| `deferred` | Deliberately postponed to a named later group, with the reason recorded |
| `dropped` | Fixed elsewhere or refuted on re-verification — reason recorded |
| `open question` | Not a defect to fix; a decision someone has to make |

## Status

| Group | Theme | Branch | Version | PR | State |
|---|---|---|---|---|---|
| 1 | Stop the data loss | `…-ugoecl` | 9.0.1 | [#842](https://github.com/exzktly/allwell/pull/842) | **merged** |
| 2 | One definition of an image, and an honest verdict | `claude/analysis-job-failure-recovery-ugoecl` | 9.0.2 | [#846](https://github.com/exzktly/allwell/pull/846) | **open, complete** — lane 5613 passed, CI green, reviewed, findings fixed |
| 3 | The record and the resume path | `claude/analyze-audit-group3-record` | 9.0.2 | — | **in progress** — 13 of 20 done; no PR yet |
| 4 | The Analyze pane | `claude/analyze-audit-group4-pane` | 9.0.2 | [#844](https://github.com/exzktly/allwell/pull/844) | **open** — reviewed; 4 of 8 review findings fixed |
| 5 | Numbers and parsing | `claude/analyze-audit-group5-numbers` | 9.0.2 | [#845](https://github.com/exzktly/allwell/pull/845) | **open** — reviewed; all 13 review findings fixed, lane re-running |

**All five branches carry version 9.0.2**, and that is deliberate rather than a
mistake to correct. `scripts/preflight.sh` requires the net delta from
`origin/main` to be exactly *one* increment, so the pre-assigned 9.0.3 / 9.0.4 /
9.0.5 scheme in an earlier revision of this file was wrong on three branches:
each is a separate PR off the same base, so each bumps to the same next number.
Whichever merges first leaves the others to rebase and re-bump. Check
`git diff origin/main -- version.py` immediately before every push.

Counts: **62 done**, **2 dropped**, **2 open questions**, **19 outstanding** —
Group 2: 0, Group 3: 7, Group 4: 4, Group 5: 0. The total exceeds the audit's 69
because it also carries the reviewers' findings on each group's own fixes, which
are rows in their own right.

> **Two interruptions, 23 August, and what they cost.** Both parallel workers
> and the Group 2 reviewer were killed mid-task by an API session limit; later
> the session ran low on context mid-way through Group 4. No work was lost
> either time — every branch's commits were pushed and every worktree was clean
> — but the ground rules below were broken in one specific way worth recording:
> Groups 4 and 5 opened their PRs *before* their adversarial reviews, because
> the reviewers had been killed. Both have since been reviewed, and both reviews
> found real defects the CI-green PRs were carrying. **CI green is not the
> review**, and a PR opened without one is not finished.
>
> Group 4's review found the sharpest instance of this file's own rules failing:
> a guard whose entire purpose is to notice a rule drifting was left *red* by a
> later commit that changed the rule and never re-ran it. Run the guards you
> wrote, on the branch you wrote them for, after every change to what they
> guard.
>
> **This file is edited on Group 2's branch only.** It was briefly updated on
> two branches at once, which is the same two-definitions failure the audit is
> about, in the document tracking it.

---

## Resume here

The state a new session needs, in the order it needs it. Everything below is
committed and pushed; nothing lives only in a container.

**Worktrees.** Each group is a `git worktree` under `/tmp/wt-<group>`, because
subagents share one filesystem and a bare `git checkout` in one moved another's
branch out from under it. If those are gone, re-create them from the pushed
branches; nothing is lost.

**1. Group 5 — one step from done.** Every review finding is fixed and
committed. The fast lane was re-running when the session ended; read it, then
push to `claude/analyze-audit-group5-numbers` and rewrite PR #845's body. The
last two commits are substantial and the body does not describe them yet.

**2. Group 4 — four review findings still owed**, listed in its table below as
`queued`: `_fluor_add_row` left out of the `_refresh_channel_dependents` sweep;
`probe_ssh_async`'s refusal return value discarded at all three call sites;
`test_hardcoded_tokens.py`'s new embedded-hex scan inheriting the *file*-level
allowlist bypass (§7.5 #10, in the file that documents the rule); and the
tile-size gate disagreeing with the tile-size emitter about the field's domain.
Then run the lane and push.

**3. Group 3 — seven findings left**, and it has never had a lane run, a review,
or a PR: L8, L9, L10, X7, X9, X10, X11. Read the audit's own entry for each
before starting — the rollup rows are terse here because the audit carries the
measurement, and `/tmp/audit.txt` (a text extraction of the published artifact)
was how this session read them.

**4. Then, for every group:** full `scripts/test.sh`, adversarial review of the
pushed diff, fix what it finds, re-run, push, open or update the PR. Group 3
still owes all of that; Groups 4 and 5 owe the second lane and the PR body.

**5. Delete this file** in the PR that closes the last group.

---

## How the four groups run in parallel

The PRs are **stacked, not serialised**: none waits for another to merge. Groups
2, 4 and 5 run concurrently on three branches, each based on `main`. Group 3
starts when Group 2's PR opens, because the two share
`services/input_resolution_service.py`.

**No two groups edit the same file.** That is the whole safety property, and it
is enforced by assignment rather than by hoping. The partition:

| Group | Owns |
|---|---|
| 2 | `util/image_exts.py`, `util/schema_infer.py`, `pipeline/completeness.py`, `pipeline/capacity.py`, `pipeline/io.py`, `pipeline/run_report.py`, `process_microscopy.py`, `services/input_resolution_service.py` |
| 4 | `analyze_tab.py`, `analyze_remote_dialog.py`, `analyze_tab_form.py`, `analyze_tab_fields.py`, `analyze_tab_seg_params.py`, `analyze_input.py` |
| 5 | `pipeline/threshold_optimize.py`, `pipeline/auto_threshold.py`, `pipeline/ome_explode.py`, `pipeline/stardist.py`, `pipeline/background.py`, `util/channel_roles.py`, `util/well_token.py`, `util/timepoint.py` |
| 3 | `analyze_suspend.py`, `analyze_preflight.py`, `analyze_run_spec.py`, `analyze_progress.py`, `analyze_tab_pinfo.py`, `analyze_tab_log.py`, `services/run_journal.py`, `services/pipeline_runner.py`, `services/pipeline_service.py`, `services/suspend_record.py`, `services/env.py`, `services/remote_runner.py` |

`tests/`, `ARCHITECTURE.md`, `README_WellViewer.md` and `version.py` are shared;
each group edits only the parts covering its own work, and the version numbers
are pre-assigned above so the bumps cannot collide.

**Two findings moved to keep one owner per file**, and the reassignment is the
point rather than an accident:

- **C11** (`auto_threshold` hardcodes tif/tiff) is a Group 2 *sweep* item living
  in a Group 5 *file*. Group 5 fixes it, using `util.image_exts.is_group_image`,
  whose signature Group 2 is not changing.
- **P12** (`qc_region_area` writes `0.0` for "could not compute") is a Group 5
  *pattern* living in a Group 2 *file* (`completeness.py`). Group 2 fixes it.

**Group 3's one known collision:** X3 needs `analyze_input.py:99`, which Group 4
owns for U8–U17 at `:50`. Group 3 rebases onto Group 4's branch rather than
editing it concurrently.

---

## Ground rules for every group

These are carried from the approved plan and are not optional.

- **One PR per group, opened only at the end.** CI is paid for. Commit freely
  and locally; do not push to a branch with an open PR mid-group.
- **Run `scripts/test.sh` in full locally before opening the PR** — the whole
  fast lane, roughly six minutes. CI then confirms rather than discovers.
- **An expert adversarial Python reviewer at the end of every group**, pointed
  at the pushed diff, told what the change claims to do, and asked for its
  findings *and* its cleared section. Its findings are fixed before the PR
  opens, and are recorded in this file as rows of their own.
- **One version bump per PR** (`version.py` only). Check
  `git diff origin/main -- version.py` immediately before each push.
- **Every fix is pinned by a test watched failing against the unfixed code**
  with a real `AssertionError` — not an `ImportError`, and not a `TypeError`
  raised while formatting the failure message. Where the fix introduces a name
  that cannot exist on the base, mutate HEAD instead (CLAUDE.md §7.7 #2).
- **ARCHITECTURE.md is updated in the same commit series**, rewriting the prose
  that is now wrong rather than appending to it.
- **Each group branches from `main` after the previous group's PR merges.**
  Never `checkout -B` a branch whose PR is still open.
- **This file is updated in the same commit as the work it describes.** A row
  whose state is stale is a broken build.

---

## Group 1 — Stop the data loss · `merged`

PR [#842](https://github.com/exzktly/allwell/pull/842), merged. Six Tier-1
findings, plus fourteen defects a sixth reviewer found in those fixes.

| ID | Finding | State |
|---|---|---|
| X1 | A resume run deletes the user's images without ever grouping them | done |
| P1 | One read error truncates a well's CSV to just the new rows, and the well still reports complete | done |
| P2 | FOV checkpoints collide across plates, so one plate's measurements land in another's CSV | done |
| P3 | Neither "already done" predicate is keyed on settings, so changing them re-runs nothing | done |
| U1 | The Run button re-enables itself mid-run, and a second pipeline can be launched over the first | done |
| C1 | A position with fewer timepoints has its last frame duplicated under every later timepoint name | done |

### Review findings on Group 1's own fixes

| ID | Finding | State |
|---|---|---|
| R1 | Basename identity meant a re-exported image was deleted and the stale archive analysed | done |
| R2 | `KEYED_ON` named six options that do not exist; fifteen real ones were unkeyed | done |
| R3 | The fold flattened `masks/` into the well's *input* archive | done |
| R4 | The fold buffered whole archives into RAM, re-deflated `ZIP_STORED`, reset timestamps | done |
| R5 | The queue's busy gate read the subprocess, so it dispatched into a live run and wedged | done |
| R6 | The settings marker was stamped on wells that computed nothing | done |
| R7 | A remote run's marker was never fetched, leaving remote plates grandfathered for ever | done |
| R8 | `test_reuse_is_keyed_completely.py` registered the new decision and generated no tests | done |
| R9 | Keying the staging directory opened an uncollected scratch leak | done |
| R10 | The ragged-position refusal blocked on Z, which the explode max-projects | done |
| R11 | One `swallow` around the whole fold loop stopped every well after a bad archive | done |
| R12 | `merge_fetched_csvs` promised per-file failure handling and caught only `OSError` | done |
| R14 | `_wells_claimed_by` re-read the schema by hand and claimed `F01` for `A01` | done |
| R15 | Docs overstated what the reuse key covered; tree entries misaligned | done |
| **R13** | `_delete_sources_that_landed` asks "did this file's *well* land?", not "did this file land?" — plus `tif_files_in` returning dangling symlinks | **deferred → Group 2** |

**Why R13 is deferred.** The precise question is answerable by diffing the
grouped member index across the zipper run. Group 2 rewrites this same
predicate surface for a different reason (X8, the `is_group_image` sweep), and
doing it in two passes would leave one module holding two definitions of the
same word — the half-sweep CLAUDE.md §7.5 #5 names as worse than not sweeping.

---

## Group 2 — One definition of an image, and an honest verdict · `complete`

The predicate sweep, plus the places completeness reports something it never
verified. They belong together: three of the four verdict bugs are *caused* by
the predicate.

**The sweep.** `util/image_exts.GROUP_IMAGE_EXTS` is the one answer and is
re-spelled as a literal in four places. Route every *intent*-side caller through
`is_group_image`, and skip leading-dot basenames, `__MACOSX/` members and zip
directory entries.

| ID | Finding | State |
|---|---|---|
| P6 | AppleDouble twins count toward intent, so a perfect plate reports partial for ever | done |
| P7 | A PNG plate writes empty `available_timepoints` and preflights as one FOV-group per well | done |
| P8 | The disk preflight sizes a run from a 4 KB sidecar instead of a 112 MB image | done |
| C4 | AppleDouble sidecars win schema inference and the result is reported confident | done |
| C11 | `auto_threshold` hardcodes tif/tiff while the pipeline can write `.png` | done |
| X8 | `tif_files_in` judges files it is not entitled to judge | done |
| P4 | Folder mode's `run_check` is structurally incapable of reporting a partial well | done |
| P5 | Loose TIFs are moved into folders the run then ignores | done |
| P9 | A well with a full CSV and no output archive reads "complete" | done |
| P10 | "No image groups found" is recorded as 0/0 and summarised as a whole plate | done |
| P11 | `aborted_reason` is computed, serialised, read — and no branch can populate it | done |
| P13 | Error accounting names groups that were never attempted, with no traceback behind them | done |
| R13 | Delete decision keyed on the well, not the file; `tif_files_in` returns dangling symlinks | done |
| — | New AST guard: no second literal spelling of the image-extension set | done |

**Leave the done-side default alone.**
`test_incremental_keys_shared.py::test_zip_reader_keeps_its_own_hidden_file_behaviour`
deliberately pins it so a rule change cannot alter what an incremental run
considers already done. Add `images_only=True` for intent instead.

**Guard mechanics.** Build on the `test_one_definition_of_the_run_dir.py`
skeleton: `python_sources`/`rel` imported into the guard's own namespace so
`test_guards_bite._run_scan` can redirect it, a fail-closed anchor assert before
the offenders assert, paired cases in `tests/test_guards_bite.py` including a
clean anchor-free surface. Prove with `scripts/prove_guard.sh` and read the
failure **type**.

**Scan surface is half the rule** (§7.5 #3). The three AST guards that landed in
one earlier branch each had a different idea of which files to read, and each
was blind exactly where another could see. Roots come from `tests/_repo_scan.py`;
a guard needing its own says why in the file.

---

## Group 3 — The record and the resume path · `in progress`

Branch `claude/analyze-audit-group3-record`, version 9.0.2, **no PR yet**. Never
had a full lane run and never had an adversarial review; both are owed before it
opens one.

| ID | Finding | State |
|---|---|---|
| L1 | The Run button never resumes a killed run, so a half-finished well is skipped whole for ever | done |
| L2 | The pre-run coverage line reports a scan that gave up as if it were complete | done |
| L3 | `close_run` has no token guard, so a failed open rewrites the previous run's record | done |
| L4 | Discard does not discard — the banner comes straight back | done |
| L5 | "Load these settings" silently drops `auto_threshold` and `run_check` | done |
| L6 | A late coverage scan re-shows a banner hidden for a running job, or dismissed | done |
| L7 | An unresolvable input path reads as "no folder", so Resume offers a different plate | done |
| L8 | A queue-driven Suspend leaves the plate offered twice, and it runs twice concurrently | queued |
| L9 | A well skipped as up-to-date is journalled "analysed" | queued |
| L10 | `TALLY_GROUPS` files "analysed" under "finished" | queued |
| L11 | `suspend_record._write_all` never got its sibling's lesson, and Suspend became a silent no-op | done |
| X2 | A remote run that fails local verification is journalled "ok", so it is never offered for resume | done |
| X3 | Stop during grouping paints a red failure and forces the log open | done |
| X4 | One undecodable byte orphans the pipeline as a session leader while the GUI reports it never started | done |
| X5 | A `TypeError` compat shim silently runs the whole resolver twice | done |
| X6 | Per-run state is reset inside the worker thread, so a Stop in the scheduling gap is discarded | done |
| X7 | `plan_input_layout` describes a different run than the one that happens | queued |
| X9 | `tif_files_in` judges files it is not entitled to judge (`TIF_GLOB` matches AppleDouble sidecars) | queued |
| X10 | "Zip mode detected" fires for any stray `.zip`, and the startup warning names a fallback that is not taken | queued |
| X11 | Six typed silent excepts; the one at `input_resolution_service.py:1329` takes the X1 path on an unreadable `in/` | queued |

What the finished ones turned into, where the shape is worth carrying forward:

- **X3 was two defects, and the second was worse.** The audit reported that
  `cancel_state_at_failure` latches only inside `_enqueue_error` while `log_q`
  is public, so a Stop during grouping painted a red failure. Reproducing it
  showed `_had_error` was not set either — so a *genuine* input failure with no
  Stop in play journalled the run `ok`. A run that never started was recorded as
  one that succeeded, and was therefore never offered for resume. The latch
  moved to the queue (`RunEventQueue.put`), which is the boundary every writer
  crosses; `_enqueue_error` keeps its name and its AST guard.
- **L6 has the same shape one layer out.** `settle_folder_banners` checked both
  visibility gates and `_render_preflight` — the seam every coverage-scan
  completion paints through — checked neither. The gates moved to the paint
  seam, so an answer arriving from a worker thread minutes later is subject to
  the same two questions as one arriving from the settle timer.
- **L11 was one rule with two copies.** `run_journal._atomic_write_json` and
  `suspend_record._write_all` were the same function; the copies had diverged in
  both available directions (one caught the `TypeError` a caller-supplied
  `spec` raises, one did not; one unlinked its staging file, one did not). Both
  now go through `util.json_write.try_write_json_atomic`, which is the module
  that already held this rule and already named the journal's copy as a
  deliberate exception — the exception was one line. Separately, `_suspend`
  wrote the record first, which had become *only if that worked*: the write has
  its own scope now and the wind-down happens either way.
- **Nothing in the repo pressed the Suspend button** until
  `tests/test_the_suspend_button_winds_the_run_down.py`.

---

## Group 4 — The Analyze pane · `in review`

PR [#844](https://github.com/exzktly/allwell/pull/844), version 9.0.2. All
seventeen `U` findings are done. The adversarial review returned **8 findings**;
four are fixed, four are owed.

| ID | Finding | State |
|---|---|---|
| U2 | "Run on:" silently switches from Local to a remote after any Manage-Remotes change | done |
| U3 | Dry-run cleanup is silently cleared by a glance at Local, and the next run really deletes | done |
| U4 | The Manage-Remotes Test button blocks the whole GUI for the ssh probe | done |
| U5 | Edit, Test and Manage do nothing at all when `remotes.toml` is malformed | done |
| U6 | QC bounds and every Cellpose parameter silently swap in a default | done |
| U7 | An unparseable StarDist threshold silently becomes the model's own default | done |
| U8 | The segmentation hint keeps showing the happy path after the Phase channel is removed | done |
| U9 | "Replace this passphrase-protected key" can silently become "reusing it" | done |
| U10 | `BootstrapLogDialog._poll` drains unbounded and drops lines silently | done |
| U11–U17 | Seven further pane defects — leaked dialogs, dead state, four small lies | done |

### Review findings on Group 4's own fixes

| ID | Finding | State |
|---|---|---|
| G4-R1 | `test_stardist_threshold_rule_is_one_rule.py` left **red**: a later commit changed the rule the guard holds and never re-ran it | done |
| G4-R2 | The QC `min ≤ max` comparison reads the shipped default `0` as a real ceiling, refusing a form that runs on `main` | done |
| G4-R3 | `min_nucleus_area_px` validated for every routine though only one shows or reads it — and a correctly gated rule already existed | done |
| G4-R4 | The `-1` threshold sentinel exception is justified by a restore path the repo explicitly prevents | done |
| G4-R5 | `_fluor_add_row` left out of the `_refresh_channel_dependents` sweep — one file, two definitions of "the rows changed" | queued |
| G4-R6 | `probe_ssh_async`'s refusal return value is discarded at all three call sites, and each has already disabled its button | queued |
| G4-R7 | The new embedded-hex scan in `test_hardcoded_tokens.py` inherits the *file*-level `COLOUR_ALLOWLIST` bypass (§7.5 #10) | queued |
| G4-R8 | The tile-size gate and `_emit_execution_flags` disagree about which routines `stardist_tile_px` belongs to | queued |

Also raised and worth carrying: `parse_threshold("nan")` returns `nan` on this
branch, so `--stardist_prob_thresh nan` passes argparse *and* the CLI pre-flight
and segments the plate with a NaN threshold. Group 5 fixes that in
`pipeline/stardist.py`; the guard's `FORM_IS_STRICTER` roster carries a liveness
test that fails once it lands, which is the intended way for the two branches to
meet.

**Testing posture for this group specifically.** CLAUDE.md §7.6: press the
button, do not assert it exists. The harness that found these built the real tab
offscreen and drove every control; reuse that shape. Do **not** reach for the
`viewer_app` fixture — these must stay in the fast lane.

---

## Group 5 — Numbers and parsing · `in review`

PR [#845](https://github.com/exzktly/allwell/pull/845), version 9.0.2. Every
audit finding and every review finding is fixed and committed; the lane was
re-running when the session ended, and the PR body does not yet describe the
last two commits.

| ID | Finding | State |
|---|---|---|
| C2 | The control-based threshold objective saturates at 1.0, so one outlier sets the plate's gate | done |
| C3 | Both threshold estimators round to an integer, so a sub-1 channel stores `0.0` | done |
| C6 | Two channels normalising alike publish a duplicate zip member; one becomes unreachable | done |
| C7 | Seeded-watershed marker construction is 70× slower than it needs to be | done |
| C8 | The phase heuristic matches `dic` as a bare substring, so fluorescence skips all correction | done |
| C9 | `DeltaTUnit` handling covers only minutes; milliseconds are 1000× wrong | done |
| C10 | Three silent zero-cell outcomes in a module that warns loudly about the fourth | done |
| C13–C16 | Four smaller compute and util defects | done |
| P12 | `qc_region_area` writes `0.0` for "could not compute" | done (Group 2) |
| C17 | The Otsu sampler contrasts a per-cell mean against a single background pixel | **open question** |

### Review findings on Group 5's own fixes

Two adversarial passes. The first found the `GFP`/`gfp` case collision, an `Ms`
test asserting the defect, and three overstated claims. The second found
thirteen, four of them regressions this branch had introduced.

| ID | Finding | State |
|---|---|---|
| G5-R1 | A **third** Otsu estimator still rounded a sub-1 gate to `0.0` — the one behind the Cell Gating tab's Auto-threshold button | done |
| G5-R2 | `%g` renders sub-1e-4 hours in exponent form, which `util/timepoint` cannot parse — Review then sorted those tokens as text | done |
| G5-R3 | The pipeline's and the viewer's "shared" objectives disagreed: only one passed population sizes to `relative_gap` | done |
| G5-R4 | `TheT` was never bounds-checked, so a one-based writer mixed `t0` with `0h` and attached every time to the wrong index | done |
| G5-R5 | The unreadable-unit warning fired on files whose timepoints resolved perfectly | done |
| G5-R6 | `cytoplasm_mask`'s constant-image branch is unreachable — `threshold_otsu` returns the value rather than raising — while four artefacts described it | done |
| G5-R7 | A comment named `is_run_input_member`, a function that exists in no branch of this tree | done |
| G5-R8 | `subtract_background` has a **fourth** identity return (`empty_well`), unswept and unwarned | done |
| G5-R9 | The relaxed trailing bound admitted `Trans647` as a phase channel | done |
| G5-R10 | A test named for a general claim the branch had just retracted | done |
| G5-R11 | ARCHITECTURE never documented the case-sensitive symbol table and said the unit lookup worked another way | done |
| G5-R12 | `util/fov` and `util/schema_infer` are the digit-class siblings the `\d` sweep did not convert | done — bounded in the doc rather than converted, because neither decides identity |
| G5-R13 | `MS` and `mS` folded to *milli*second, the exact 10⁹ error the case-sensitive table exists to prevent | done |

Shapes worth carrying forward:

- **C2's fix does not do what it was documented to do**, and saying so was the
  hardest correction on this branch. Laplace smoothing shortens the tied run
  (485 of 512 grid points → 354 on a measured pair) but does **not** move the
  reported threshold, because `argmax` returns the run's *lower* edge and that
  edge is set by where the negative control's fraction-on reaches zero, which
  smoothing does not touch. One outlying control cell still shifts the gate by
  1252 intensity units either way. What ships against the instability is
  *visibility* — `tie_count`, `tie_span`, `tie_contiguous`, all logged — not a
  cure. Which point of a genuine plateau ought to be reported is a question
  about the assay.
- **`tie_span` is `max − min` of the tied thresholds**, which is a *plateau*
  only when those points are adjacent on the grid. A bimodal objective ties at
  two separated points, and the log line then read "spanning 864.6 intensity
  unit(s) (65.9 to 930)" about two grid points — degraded output presented as
  good, from the line added to prevent exactly that.
- **Five optimiser tests asserted a difference of exactly 1.0** on tiny
  fixtures, which *is* the saturation C2 removes. They derive the expected score
  from the result's own counts now; every threshold assertion was already
  correct and is untouched.

---

### Open questions

| ID | Question | Why it is not mine to decide |
|---|---|---|
| C17 | Should the Otsu sampler contrast a per-cell mean against a background *distribution* rather than a single pixel? | The measurement is real — a 30 % higher threshold from the statistic mismatch — but which value is the better default gate is a domain call |
| — | Is a fourth document in `_Docs/Markdowns/` acceptable for the duration, given CLAUDE.md §5? | Recorded here rather than assumed; delete this file when the last group lands |

---

## Dropped

| ID | Finding | Why |
|---|---|---|
| C5 | A constant well-shaped token steals the well slot, collapsing a plate onto one well | Fixed by PR #834, which found a stronger case than the audit did: on the *default* layout `f01`–`f12` are valid row-F well labels. Verified against current `main` |
| C12 | `util/fov.py` carries the falsy-zero bug its sibling documents and fixed | Fixed by PR #834 — verified: `parse_fov(0)` now returns `0.0` |

---

## Verification, per group, in order

1. `ruff check .`
2. AST-scan every touched file for names loaded but never bound, and for
   annotation-only names — `tests/test_no_unbound_names.py` covers both, and
   ruff sees neither. This caught a live `NameError` in Group 1.
3. For each fix, run its regression test against the **unfixed** code and
   confirm a real `AssertionError`. Read the failure *type*, not the count.
4. `scripts/test.sh` in full.
5. Adversarial reviewer on the pushed diff. Fix its findings, add them to this
   file as rows, re-run the lane.
6. Bump `version.py`, push, open the PR, watch the one CI run.

**Run the decisions, do not read them** (§7.1). Each group has at least one
throwaway script under the scratchpad whose printed output goes in the PR body:

- Group 2 — a `junk=True` plate through `coverage_for_well`,
  `_collect_image_stems` and `sample_image_bytes`, printing intent, timepoints
  and sampled bytes before and after.
- Group 3 — a killed run offered for resume, and a failed-`open_run` run that
  must not touch the previous record.
- Group 4 — the offscreen tab driven through every control, with an excepthook.
- Group 5 — the threshold objective's tie width, and a sub-1 channel's stored
  threshold.

**In the real app**, once Groups 2–3 have landed: run a small plate, kill it
mid-run, reopen, confirm the banner offers it and that resuming recomputes only
the missing FOV groups. Then change a segmentation setting and confirm the plate
re-runs rather than reporting "up to date".

## Out of scope

- `services/remote_runner.py`'s extracted modules beyond the call sites named —
  they had three adversarial passes in PR #836.
- A `pipeline/` ↛ `services/` guard. The convention holds today and nothing
  violates it; adding the guard is worth doing but is not this work.
