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
| 1 | Stop the data loss | [#842](https://github.com/exzktly/allwell/pull/842) | **done** — CI green, awaiting merge |
| 2 | One definition of an image, and an honest verdict | `claude/analysis-job-failure-recovery-ugoecl` | 9.0.2 | — | **in progress** |
| 3 | The record and the resume path | `claude/analyze-audit-group3-record` | 9.0.5 | — | queued — starts when Group 2's PR opens |
| 4 | The Analyze pane | `claude/analyze-audit-group4-pane` | 9.0.3 | — | **in progress** (parallel worker) |
| 5 | Numbers and parsing | `claude/analyze-audit-group5-numbers` | 9.0.4 | — | **in progress** (parallel worker) |

Counts: **20 done** (6 Tier-1 findings and 14 review findings on those fixes),
**2 dropped**, **2 open questions**, **62 outstanding** — Group 2: 14,
Group 3: 20, Group 4: 16, Group 5: 12.

The outstanding count exceeds the audit's 69 because it also carries the
reviewer's findings on Group 1 and the tasks (a guard, a sweep) that no single
finding owns. It is the count of *rows below*, which is the number that can be
checked.

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

## Group 1 — Stop the data loss · `done`

PR [#842](https://github.com/exzktly/allwell/pull/842). Six Tier-1 findings,
plus twelve defects a sixth reviewer found in those fixes.

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

## Group 2 — One definition of an image, and an honest verdict · `next`

The predicate sweep, plus the places completeness reports something it never
verified. They belong together: three of the four verdict bugs are *caused* by
the predicate.

**The sweep.** `util/image_exts.GROUP_IMAGE_EXTS` is the one answer and is
re-spelled as a literal in four places. Route every *intent*-side caller through
`is_group_image`, and skip leading-dot basenames, `__MACOSX/` members and zip
directory entries.

| ID | Finding | State |
|---|---|---|
| P6 | AppleDouble twins count toward intent, so a perfect plate reports partial for ever | queued |
| P7 | A PNG plate writes empty `available_timepoints` and preflights as one FOV-group per well | queued |
| P8 | The disk preflight sizes a run from a 4 KB sidecar instead of a 112 MB image | queued |
| C4 | AppleDouble sidecars win schema inference and the result is reported confident | queued |
| C11 | `auto_threshold` hardcodes tif/tiff while the pipeline can write `.png` | queued |
| X8 | `tif_files_in` judges files it is not entitled to judge | queued |
| P4 | Folder mode's `run_check` is structurally incapable of reporting a partial well | queued |
| P5 | Loose TIFs are moved into folders the run then ignores | queued |
| P9 | A well with a full CSV and no output archive reads "complete" | queued |
| P10 | "No image groups found" is recorded as 0/0 and summarised as a whole plate | queued |
| P11 | `aborted_reason` is computed, serialised, read — and no branch can populate it | queued |
| P13 | Error accounting names groups that were never attempted, with no traceback behind them | queued |
| R13 | Delete decision keyed on the well, not the file; `tif_files_in` returns dangling symlinks | queued |
| — | New AST guard: no second literal spelling of the image-extension set | queued |

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

## Group 3 — The record and the resume path · `queued`

| ID | Finding | State |
|---|---|---|
| L1 | The Run button never resumes a killed run, so a half-finished well is skipped whole for ever | queued |
| L2 | The pre-run coverage line reports a scan that gave up as if it were complete | queued |
| L3 | `close_run` has no token guard, so a failed open rewrites the previous run's record | queued |
| L4 | Discard does not discard — the banner comes straight back | queued |
| L5 | "Load these settings" silently drops `auto_threshold` and `run_check` | queued |
| L6 | A late coverage scan re-shows a banner hidden for a running job, or dismissed | queued |
| L7 | An unresolvable input path reads as "no folder", so Resume offers a different plate | queued |
| L8 | A queue-driven Suspend leaves the plate offered twice, and it runs twice concurrently | queued |
| L9 | A well skipped as up-to-date is journalled "analysed" | queued |
| L10 | `TALLY_GROUPS` files "analysed" under "finished" | queued |
| L11 | (fourth lifecycle defect in the rollup — read the audit entry before starting) | queued |
| X2 | A remote run that fails local verification is journalled "ok", so it is never offered for resume | queued |
| X3 | Stop during grouping paints a red failure and forces the log open | queued |
| X4 | One undecodable byte orphans the pipeline as a session leader while the GUI reports it never started | queued |
| X5 | A `TypeError` compat shim silently runs the whole resolver twice | queued |
| X6 | Per-run state is reset inside the worker thread, so a Stop in the scheduling gap is discarded | queued |
| X7 | `plan_input_layout` describes a different run than the one that happens | queued |
| X9 | (in the X7–X11 rollup — read the audit entry) | queued |
| X10 | (in the X7–X11 rollup — read the audit entry) | queued |
| X11 | (in the X7–X11 rollup — six typed silent excepts) | queued |

Notable shapes:

- **L1** is the one that leaves a half-finished well permanently 3-of-12.
  `resume_aware_spec` asks only `suspend_record.load_all()` and is wired into
  `start_from_queue` alone. Widen it to the journal and route `start_run`
  through it, so every entry point answers the same way.
- **L3** — give `close_run` a `run_token` parameter mirroring `record_well`'s
  refusal, and skip `_index_upsert` when `open_run`'s write failed. Then decide
  the untokenised remote writers deliberately (`remote_runner.py:5169`, `:6320`,
  `:6814`), which is the gap ARCHITECTURE now describes honestly.
- **X2** is one token, plus an AST guard that no `("error", …)` literal is built
  outside `_enqueue_error` in that module.
- **X3** is the same root cause one layer out: `cancel_state_at_failure` latches
  only inside `_enqueue_error` while `log_q` is public.
- **X4** — add `encoding="utf-8", errors="replace"` to `spawn_pipeline` and
  `run_zipper`, and independently wrap both read loops so any exception still
  reaches `signal_subprocess(proc)` and `proc.wait()`.

---

## Group 4 — The Analyze pane · `queued`

| ID | Finding | State |
|---|---|---|
| U2 | "Run on:" silently switches from Local to a remote after any Manage-Remotes change | queued |
| U3 | Dry-run cleanup is silently cleared by a glance at Local, and the next run really deletes | queued |
| U4 | The Manage-Remotes Test button blocks the whole GUI for the ssh probe | queued |
| U5 | Edit, Test and Manage do nothing at all when `remotes.toml` is malformed | queued |
| U6 | QC bounds and every Cellpose parameter silently swap in a default | queued |
| U7 | An unparseable StarDist threshold silently becomes the model's own default | queued |
| U8 | The segmentation hint keeps showing the happy path after the Phase channel is removed | queued |
| U9 | "Replace this passphrase-protected key" can silently become "reusing it" | queued |
| U10 | `BootstrapLogDialog._poll` drains unbounded and drops lines silently | queued |
| U11–U17 | Seven further pane defects — leaked dialogs, dead state, four small lies | queued |

**U2 and U3 lead** — they are the two that change what the software does to
someone's data. **U6/U7** coerce to a default *before* the validator sees them;
keep the raw text in `opts` as the radius fields already do and extend
`_numeric_option_errors`. **U4** lifts `RemoteFormDialog._test_ssh`'s
thread+queue+timer idiom into one helper both Test buttons use.

**Testing posture for this group specifically.** CLAUDE.md §7.6: press the
button, do not assert it exists. The harness that found these built the real tab
offscreen and drove every control; reuse that shape. Do **not** reach for the
`viewer_app` fixture — these must stay in the fast lane.

---

## Group 5 — Numbers and parsing · `queued`

| ID | Finding | State |
|---|---|---|
| C2 | The control-based threshold objective saturates at 1.0, so one outlier sets the plate's gate | queued |
| C3 | Both threshold estimators round to an integer, so a sub-1 channel stores `0.0` | queued |
| C6 | Two channels normalising alike publish a duplicate zip member; one becomes unreachable | queued |
| C7 | Seeded-watershed marker construction is 70× slower than it needs to be | queued |
| C8 | The phase heuristic matches `dic` as a bare substring, so fluorescence skips all correction | queued |
| C9 | `DeltaTUnit` handling covers only minutes; milliseconds are 1000× wrong | queued |
| C10 | Three silent zero-cell outcomes in a module that warns loudly about the fourth | queued |
| C13–C16 | Four smaller compute and util defects (`background.py` identity returns, `parse_threshold` letting `NaN` through, `\d` matching non-ASCII digits, two docstring/behaviour mismatches) | queued |
| P12 | `qc_region_area` writes `0.0` for "could not compute" | queued |
| C17 | The Otsu sampler contrasts a per-cell mean against a single background pixel | **open question** |

Notes:

- **C2** — add statistical support, and report the tie width so a plateau is
  visible rather than presented as an argmax. Measured: 73 of 512 grid points
  tied at the maximum.
- **C6** — refuse in `read_ome_layout`, before an archive is written.
  `atomic_zip`'s verification cannot see the duplicate because it is on both
  sides.
- **C7** — `bincount` and a vectorised `center_of_mass`; measured 23.7 s → 0.28 s
  per FOV, roughly 2.7 hours off a 96-well seeded-watershed run.
- **C8** was **narrowed** by PR #834, which bounded `trans` and `bf` and left
  `dic` unbounded. Fixing one alternative short of the others is the half-sweep
  §7.5 #5 warns about, inside the PR whose purpose was this.
- **C9** — treat an unrecognised unit as unknown and fall back to positional
  `t<i>` rather than to seconds.
- **C10** is read-only: TF is not installed in this container, so it is verified
  by reading and the PR says so.
- **P12** and **C3** are the same defect class as §7's cardinal one: a value the
  code never computed stored as `0.0`, which is also a legitimate measurement.

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
