# Inference subsystems — implementation tracker

**This document is temporary.** It exists because three related bodies of work
are being carried across more than one session, and CLAUDE.md §6.1 requires that
a plan of this size live on disk rather than in conversation. It is deleted by
the pull request that closes its last row, and that pull request says in its body
that it is gone.

It plans three subsystems that add *inference* — statistical or learned decisions
made from the data rather than typed by the user — to All-Well. They are
independent of one another and are sequenced as three separate pull requests.

| Section | Subsystem | Why it exists |
|---|---|---|
| **A** | Field quality and per-plate field anomaly | A field that was out of focus, saturated, empty or covered in debris still produces cells, still produces intensities, and lands in the CSV looking exactly like a good one. |
| **B** | Segmentation threshold calibration | `DEFAULT_PROB_THRESH_TEXT = "0.2"` is a constant standing in for a per-dataset decision, and the user has no evidence with which to change it. |
| **C** | Filename-schema inference | The user types `experiment:channel:well:fov:timepoint` and finds out it was wrong from an empty preview. |

Section D of the original exploration — plate-effect detection (edge effects,
row/column gradients, replicate disagreement) — is deliberately **not** planned
here. It was excluded by the requester.

## How to read the rows

Every row has a state, a place, and — once it is `done` — the test that pins it.

| State | Meaning |
|---|---|
| `todo` | Not started. |
| `wip` | Started, not finished. Never left in this state across a session boundary without a note saying where it stopped. |
| `done` | Implemented **and** pinned by a test that has been watched failing without the fix (CLAUDE.md §7.7 #2). "Implemented" alone is not `done`. |
| `declined` | Examined and deliberately not done, with the reason written both here and at the site. A declined row is never silently dropped. |

**Counts are derived from the rows, never typed.** To count:

```bash
grep -c '| done |' _Docs/Markdowns/INFERENCE_SUBSYSTEMS_TRACKER.md
grep -c '| todo |' _Docs/Markdowns/INFERENCE_SUBSYSTEMS_TRACKER.md
```

A row moves to `done` in the *same commit* as the change it describes.

---

## 0. Ground rules that apply to all three sections

These are the repository's existing contracts, restated here only where a
section is at risk of breaking one. They are not new rules.

### 0.1 No new third-party dependency

All three subsystems are numpy / scipy / stdlib only.

**scikit-learn is off limits for anything in a kept-forever layer.**
`profiles/full.toml` declares `sklearn`, `umap` and `pynndescent` as deps that
*only* the Multivariate feature pulls in, and the PyInstaller build adds them to
`excludes` when Multivariate is profiled out. A QC surface that imports sklearn
would break the thin build the day it shipped. This rules out `IsolationForest`,
`EllipticEnvelope` and `MinCovDet` for Section A's anomaly model; the plan below
uses robust per-metric statistics from numpy instead, which is also the more
explainable choice.

### 0.2 The pipeline stays self-sufficient, and the manifest is not optional

`tests/test_architecture_contracts.py::test_pipeline_is_self_sufficient` forbids
`pipeline/` from importing `review` / `widgets` / Qt at any nesting.
`tests/test_remote_manifest.py` requires an entry in
`services/remote_manifest.py::_REQUIRED_REMOTE_FILES` for **every** new
`pipeline/` or `util/` module — including ones the remote never imports, because
the guard checks completeness, not reachability. Each new module below has a
manifest row of its own for that reason.

### 0.3 Report; do not decide. Offer; do not apply.

`analyze/preflight.py` already documents both halves of this, and all three
subsystems are bound by it:

- Section A **scores** fields and **shows** the score. Excluding a flagged field
  is a toggle that defaults to off, and it composes non-destructively through
  the existing `QC_Pass` mechanism rather than deleting rows.
- Section B **draws the curve** and marks a suggested value on it. It never
  writes the form field without the user pressing something.
- Section C **detects** a schema and shows what it matched. It never replaces a
  schema the user has typed without an explicit Apply.

### 0.4 Undefined is not zero

CLAUDE.md §7 #3. Every one of these subsystems has a path where it cannot
compute an answer: a plate with three fields has no usable MAD, a metric whose
MAD is zero has no defined robust z, a folder of two filenames cannot support a
schema inference, a sweep on a frame with no nuclei has no plateau. **None of
those may store `0.0`.** They store `None` and the surface says "not computed"
with the reason.

### 0.5 Degraded output must be visible in the artifact

CLAUDE.md §7. Anything these subsystems decline to compute is named in the
artifact that carries the result — the `pipeline_info.json` block, the plot, the
readout — not only in a log line.

### 0.6 Every UI addition is reviewed against a rendering, not a description

`tests/visual/render_views.py` builds the real `AllWellApp` off-screen over the
deterministic `mini_dataset` and writes a PNG per view to
`_Docs/visual_audit/screenshots/`. Every section below that adds a control
extends that script to cover the new view **first**, runs it, and then has a UX
reviewer read the actual PNGs. A UX review conducted against a written
description of a control is not a UX review.

```bash
QT_QPA_PLATFORM=offscreen MPLBACKEND=Agg python tests/visual/render_views.py
```

### 0.7 Every section ends with a scientific-Python review

At the end of each section's implementation, before its pull request is opened,
an expert scientific-Python reviewer is dispatched against the section's diff.
It is pointed at the numerics specifically: estimator choice, edge cases,
dtype and overflow behaviour, whether the statistic means what the label claims,
and whether the fixtures could distinguish a right answer from a wrong one.

### 0.8 A test that cannot fail is not evidence

CLAUDE.md §7.8. For every behavioural row, the implementer writes down the
single edit that would break the feature, makes that edit in a
`git worktree` of HEAD, and watches a named test fail with a real
`AssertionError`. For every *guard* over existing code, `scripts/prove_guard.sh`
is run against the base ref and its site count is compared with the sweep's own
count. The mutation rows below are not optional extras; they are the rows that
decide whether the other rows are `done`.

### 0.9 One version bump per pull request

`version.py::__version__`, checked with `git diff origin/main -- version.py`
immediately before the push. Three pull requests, three patch bumps — unless a
section's user-visible surface justifies a minor, which is a judgement made when
that section is opened, not now.

---

## Section A — Field quality and per-plate field anomaly

### A.0 What is missing today, established by reading the tree

- No focus, blur, sharpness or saturation metric exists anywhere in `pipeline/`
  or `review/domain/`. Searching those trees for `focus|blur|sharp|laplacian`
  returns only `pipeline/flatfield.py`'s Gaussian blur (a correction, not a
  measurement) and one StarDist help string.
- `review/domain/qc_params.py` gates per **cell**, on `shape_area` / `area_px`,
  `shape_solidity`, `shape_circularity` and `qc_region_area`. Nothing in it can
  see that an entire field was unusable.
- `pipeline/run_report.py` exists because "a run that loses FOV-groups still
  writes a per-well CSV and `<well>_out.zip`, and on disk that output is
  indistinguishable from a well that processed cleanly." That fixed
  **coverage**. Quality was never addressed.
- The pipeline already computes field-level statistics and **throws them away**:
  `pipeline/stardist.py` logs `min`, `max`, `mean`, `dyn_range`, `finite%` and
  `nonzero%` per FOV as a `seg-input` line, purely so the last line before a
  ClipperLib abort names the culprit. Nothing persists them, so no consumer
  downstream can read them.
- The same module's near-constant-field guard returns **zero nuclei** with a log
  warning. That is a computed-as-zero path whose only trace is the log — exactly
  the shortfall §7 says must be visible in the artifact.

### A.1 The metrics

Computed inside `process_image_group` (`process_microscopy.py:1640`, a thin
orchestrator over five phases), on arrays that are already in memory. Each is
recorded together with **which image it was measured on**, because correction
changes most of them:

| Metric | Measured on | Definition | Why this one |
|---|---|---|---|
| `focus_norm_lap_var` | raw nuclear frame | `var(laplace(I)) / mean(I)**2` | Variance of the Laplacian is the standard focus statistic; dividing by mean squared makes it invariant to exposure, so it compares across a plate whose wells differ in brightness. One focus metric, not two — Tenengrad and variance-of-Laplacian are strongly correlated and a second one buys a false sense of coverage. |
| `sat_frac` | raw, per quantified channel | fraction of pixels at the dtype maximum | Saturation destroys intensity quantification silently and is invisible after background subtraction. Must be measured pre-correction. |
| `bg_level` | post-correction nuclear frame | median of pixels outside the label mask | A field whose background did not subtract is measurable here and nowhere else. |
| `nuc_count` | label image | `int(labels.max())` | Already computed and logged; the cheapest anomaly signal there is. |
| `nuc_area_median` | label image | median of `area_px` | Separates "a field with few big blobs" from "a field with many small ones" — the debris signature the per-cell gate catches one cell at a time. |
| `dyn_range` | raw nuclear frame | `p99.9 − p0.1` | Already computed by the `seg-input` log line as `max − min`; the percentile form is robust to a single hot pixel. |

Cost: one `scipy.ndimage.laplace` and a handful of reductions over arrays
already resident. Reasoned from the call site, **not measured** — the first row
of the section is to measure it, and the section does not proceed if the
overhead is not small against the StarDist inference it sits beside.

### A.2 Where the numbers are stored

`pipeline/run_report.py`'s mechanism is the right one and is reused in shape:
one small JSON per well, dropped into a hidden directory as the well worker
finishes, collected by the orchestrator into a named block inside
`pipeline_info.json`, then removed. It is process-safe by construction (one file
per well, written atomically, never appended to) and it already works for the
local worker pool, folder mode and remote runs.

**It is not folded into `run_report` itself.** Coverage accounting and quality
measurement answer different questions, `run_report`'s payload is already merged
across a remote fetch by a function with its own semantics
(`merge_fetched_run_report`), and a reader asking "was this well complete?"
should not have to step over six float metrics per field. The new module is
`pipeline/field_quality.py`, carrying both the metric computation and its own
per-well drop / collect, reusing `util.json_write.write_json_atomic`.

Per-cell CSV columns were considered and **declined**: the metrics are per-field,
so repeating them per cell adds roughly 6.7 MB to a 120,000-row well CSV to
restate one row's worth of information.

The remote fetch path needs the same merge `run_report` has, and that is a row
of its own — a remote run whose quality block is dropped on fetch is a
degradation that would be invisible.

### A.3 The anomaly model

`review/domain/field_quality_scores.py`, numpy only, Qt-free, pure.

**Per-metric robust z against the plate's own distribution.** For each metric,
take the median and the MAD across every field in the dataset, and score each
field as `0.6745 * (x − median) / MAD`. A field's overall score is the largest
absolute robust z across metrics, and the report **names the metric that drove
it** — "B7 field 3: focus 6.2 MADs below the plate median".

Why not Mahalanobis distance over the six metrics together: it needs a covariance
estimate, the robust estimators for that (MCD) are sklearn, and the plain
covariance of six correlated metrics on a plate with few fields is unstable.
Why not an isolation forest: same dependency problem (§0.1), plus it returns a
score no user can act on. The per-metric form is explainable, is defined with
as few as a handful of fields, and tells the user *what* is wrong, which is the
only form of this that changes what they do next.

**The refusals, which are the part most likely to be got wrong:**

- Fewer than `_MIN_FIELDS_FOR_SCORING` fields in the dataset → no scores at all,
  and the surface says why. A MAD over three values is not a dispersion estimate.
- `MAD == 0` for a metric (every field identical, or a metric the run did not
  compute) → that metric contributes `None`, not `inf` and not `0.0`.
- A metric absent from a field's record (older dataset, a channel that was not
  quantified) → `None`, and the field is scored on the metrics it has, with the
  count of scored metrics reported beside the verdict.
- A dataset written before this subsystem existed has no block at all → the
  surface says "this dataset was analysed before field quality was recorded",
  never "0 fields flagged".

Default flag threshold `|z| >= 5`, user-editable. Five rather than three because
the metrics are not normal and a plate has hundreds of fields; the number is a
starting point and the UI shows the distribution so the user can move it against
evidence rather than against a default they cannot see.

### A.4 The surfaces

Three, in order of value:

1. **Home → Cell Gating, beside the existing QC debris gate.** A "Field quality"
   panel: the flagged count, a per-metric breakdown, the threshold, and a
   toggle — **off by default** — that excludes flagged fields. Exclusion goes
   through `review/data/cache_projectors.py::project_verdict_column`, the same
   shared walk `QC_Pass` and the crop verdict already use, writing a
   `Field_Pass` column that ANDs into `df_included_mask` alongside `QC_Pass`.
   Nothing is deleted and `Included` is never touched.
2. **The Home plate map.** A per-well indication of how many of its fields are
   flagged. Form to be decided by the UX review — a badge, a corner tint and a
   hatch are all plausible and they are not equally readable against the
   existing well colouring, which already carries rank colour, selection state
   and progress.
3. **End of run.** One line in the run log and one in the Analyze preflight's
   report of what the last run did: "94 of 96 wells clean; B7 and C3 have
   flagged fields."

### A.5 Risk, stated plainly

This subsystem can change published numbers. A user who turns the exclusion
toggle on gets different means, different error bars and different fold changes.
That is the point, but it means:

- The toggle is **off by default**, matching `QCParams.enabled = False`.
- The flagged count is visible wherever the toggle is.
- Turning it on is recorded in the saved view state like any other gate, so a
  figure can be traced back to whether it was on.

### A.6 Rows

| # | Row | State | Place | Evidence |
|---|---|---|---|---|
| A1 | Measure the per-field metric cost against a real StarDist FOV before building anything. If it is not small against inference, stop and re-scope. | `todo` | scratch script | measured number written into this row |
| A2 | `pipeline/field_quality.py` — the six metrics, pure, numpy + `scipy.ndimage` only | `todo` | new module | unit tests on synthetic frames: a Gaussian-blurred frame scores lower focus than its source; a clipped frame scores `sat_frac > 0` |
| A3 | Manifest entry for `pipeline/field_quality.py` | `todo` | `services/remote_manifest.py` | `tests/test_remote_manifest.py` |
| A4 | Per-well JSON drop + orchestrator collect into a `field_quality` block | `todo` | `pipeline/field_quality.py`, `process_microscopy.py` | a run over the test fixture produces the block |
| A5 | Merge the block across a remote fetch | `todo` | `services/remote_runner.py` fetch path | test mirroring `merge_fetched_run_report`'s |
| A6 | Wire the metrics into `process_image_group`'s phases | `todo` | `process_microscopy.py:1640` | the block is populated by a real group run |
| A7 | Record the near-constant-field skip as a field-quality verdict, not only a log warning | `todo` | `pipeline/stardist.py`, `pipeline/field_quality.py` | a near-constant fixture frame shows as a named skip in the artifact |
| A8 | `review/domain/field_quality_scores.py` — robust z, driver metric, all four refusals | `todo` | new module | one test per refusal, each asserting `None` and never `0.0` |
| A9 | `Field_Pass` projection through `project_verdict_column`, ANDed at `df_included_mask` | `todo` | `review/features/qc/` or a sibling | a flagged field's cells drop out only when the toggle is on |
| A10 | Cell Gating panel | `todo` | `review/features/qc/tab_view.py` | driven through the real control, not the seam beneath it (§7.6) |
| A11 | Plate-map indication | `todo` | `review/features/home/plate_panel.py` | render test |
| A12 | End-of-run line + preflight line | `todo` | `pipeline/`, `analyze/preflight.py` | assertion on the emitted line, not the computed value (§7.4 #1) |
| A13 | Extend `tests/visual/render_views.py` to cover the Cell Gating panel and the plate map with flagged fields present | `todo` | `tests/visual/render_views.py` | PNGs exist and are non-empty |
| A14 | **UX review against the rendered PNGs** | `todo` | subagent | findings recorded as rows |
| A15 | Act on the UX findings | `todo` | — | — |
| A16 | Mutation: delete the `Field_Pass` AND at the chokepoint; a named test must fail | `todo` | worktree of HEAD | the failing test named here |
| A17 | Mutation: make `MAD == 0` return `0.0` instead of `None`; a named test must fail | `todo` | worktree of HEAD | the failing test named here |
| A18 | ARCHITECTURE.md §9 / §10 and README_WellViewer.md | `todo` | `_Docs/Markdowns/` | `tests/test_doc_version_sync.py` |
| A19 | Guard family + affected files + full fast lane; version bump; PR | `todo` | — | what was run, reported honestly |
| A20 | **Scientific-Python review of the section diff** | `todo` | subagent | findings recorded as rows before the PR opens |

---

## Section B — Segmentation threshold calibration

### B.0 The honest framing, which is narrower than it first looks

`analyze/seg_params.py` ships `DEFAULT_PROB_THRESH_TEXT = "0.2"` against the
pretrained model's own ~0.479, with a comment saying the model's threshold is
"conservative for these plates" and that an untouched run therefore detects
"dim, out-of-focus and marginal" nuclei. That constant decides what every
untouched run does, and the user has no evidence with which to move it.

**There is no ground truth here.** No labelled corpus of correct segmentations
exists in this repository, so any "optimal threshold" this subsystem reports is
a heuristic that cannot be validated against truth. That is the weakest science
of the three sections, and it dictates the design:

> **The deliverable is the curve, not the number.**

The subsystem draws nucleus count against probability threshold for the loaded
frame, shades the region where the count is stable, marks the current value, and
offers a suggestion the user can accept or ignore. A user looking at that plot
can see for themselves whether their threshold sits on a plateau or on a cliff —
which is a better answer than a number they have to trust.

This is a demotion from "calibrate the parameter" to "show the evidence", and it
is deliberate.

### B.1 Where it goes

`review/features/image_browser/playground/` — the single-frame correction
testbed. It is already the right home and needs no argument made for it:

- Its state module says loose-image mode "is how the tab is usable before a
  plate has ever been analysed, **which is exactly when segmentation parameters
  want tuning**".
- It already has a StarDist worker (`playground/worker.py::request_stardist_labels`),
  so threshold work runs off the UI thread on machinery that exists.
- It already renders a frame with its mask overlaid, so the sweep result has
  somewhere to point.

### B.2 The sweep, and the one thing that makes it affordable

A naive sweep is *M* full StarDist inferences per frame and is not affordable.

`pipeline/stardist.py` already knows the split API: under `ALLWELL_SEG_DEBUG` it
calls `model.predict(nir_norm)` and reads `pred[0], pred[1]` as the probability
and radial-distance maps, inside a `try` whose comment says it "falls back
silently if the split API differs". If instances can be resolved from a cached
`(prob, dist)` pair at many thresholds, the sweep is **one inference plus *M*
cheap NMS passes**.

That is an assumption about the installed StarDist version, and it is the first
row of the section. If the API does not support it cleanly, the fallback is
*M* full inferences on a **centre crop** of the frame, with the crop stated in
the readout — a sweep over a crop is honest and a sweep presented as though it
covered the frame is not.

### B.3 The criterion, and its stated weakness

Nucleus count against threshold is monotonically decreasing. The suggestion is
the midpoint of the **widest plateau**: the longest run of consecutive threshold
steps over which `|Δ log count|` stays below a tolerance. The plateau's width is
reported as the confidence, and a frame with no plateau wider than the minimum
says **"no stable region found"** rather than returning the midpoint of the
whole sweep.

A flat count region is evidence that detections are not sensitive to the exact
cut — it is not evidence that the detections are correct. The readout says so in
those terms. A frame with no nuclei, or a count that never moves across the whole
sweep, is "not computed", not a suggestion of the sweep's midpoint.

### B.4 What this section does not do

- It does **not** change `DEFAULT_PROB_THRESH_TEXT`. Changing it changes the
  numbers every untouched run produces, and this subsystem produces no evidence
  that would justify that for plates it has never seen.
- It does **not** write the Analyze form field by itself. Accepting a suggestion
  is a press, and the Playground already has a path for handing a setting back.
- It does **not** sweep the NMS threshold. One knob, one curve.

### B.5 Rows

| # | Row | State | Place | Evidence |
|---|---|---|---|---|
| B1 | Establish whether instances can be resolved from a cached `(prob, dist)` at *M* thresholds on the pinned StarDist. Write the finding here either way. | `todo` | scratch script | the measured API behaviour |
| B2 | `review/domain/threshold_sweep.py` — plateau detection, pure, numpy only, no StarDist import | `todo` | new module | plateau found on a synthetic staircase; "no stable region" on a synthetic cliff |
| B3 | Sweep execution on the Playground worker, bounded and cancellable | `todo` | `playground/worker.py` | the UI thread is never blocked |
| B4 | Crop fallback, with the crop named in the readout when it is used | `todo` | `playground/` | the readout states the crop |
| B5 | Curve rendering: count vs threshold, plateau shaded, current value marked | `todo` | `playground/renderer.py` | the drawn artifact is asserted, not the computed array (§7.4 #1) |
| B6 | The refusals: no nuclei, no plateau, a flat curve — each named in the readout | `todo` | `playground/` | one test each, none returning a number |
| B7 | Accepting the suggestion is an explicit press, and the press is what writes it | `todo` | `playground/` | the control is *driven*, not the callback called (§7.6 #1) |
| B8 | Extend `tests/visual/render_views.py` to render the Playground with a completed sweep | `todo` | `tests/visual/render_views.py` | PNG exists and is non-empty |
| B9 | **UX review against the rendered PNGs** | `todo` | subagent | findings recorded as rows |
| B10 | Act on the UX findings | `todo` | — | — |
| B11 | Mutation: make the plateau detector always return the sweep midpoint; a named test must fail | `todo` | worktree of HEAD | the failing test named here |
| B12 | One test builds the Playground through its real constructor with the sweep control present (§7.8 rule 3) | `todo` | `tests/` | the builder is exercised, not a stub |
| B13 | ARCHITECTURE.md §5 (Playground) and README_WellViewer.md | `todo` | `_Docs/Markdowns/` | `tests/test_doc_version_sync.py` |
| B14 | Guard family + affected files + Playground's slow-lane file; version bump; PR | `todo` | — | what was run, reported honestly |
| B15 | **Scientific-Python review of the section diff** | `todo` | subagent | findings recorded as rows before the PR opens |

---

## Section C — Filename-schema inference

### C.0 Why this is first

It is the smallest, it changes no published number, its failure mode is a
preview the user overrides, and it removes the most common way a new user's
first run produces nothing. It is also the only one of the three whose entire
surface is a single shared widget with two hosts.

### C.1 The single control, and the layering constraint

`widgets/schema_picker.py` is used by **both** places that ask this question —
the Analyze form's Filename Schema section and the Datasets pane's Zip Loose
Folder page. One widget, two hosts, one place to add a Detect button.

But `widgets/` carries **no app coupling** (`tests/test_architecture_contracts.py`),
so the picker may not walk a directory or know what a dataset is. The split:

- `widgets/schema_picker.py` gains a `detectRequested` signal and a
  `setDetection(result)` method. It knows nothing about where filenames come
  from.
- `util/schema_infer.py` (stdlib only, Qt-free) does the inference over a list of
  filenames handed to it. It sits beside `util/schema.py`, whose vocabulary and
  validity rules it must agree with exactly, and it needs a
  `_REQUIRED_REMOTE_FILES` entry (§0.2) even though the remote never calls it.
- The two hosts supply the filenames. The Import Loose page already walks the
  folder; the Analyze form knows the input directory and uses
  `util/dir_scan.py`, which exists for exactly this.

### C.2 The inference

Given up to *K* sampled filenames (capped, and the cap stated in the readout):

1. **Separator.** For each candidate (`_`, `-`, `.`), split every name and keep
   the candidate whose token count is most consistent across the corpus. A
   corpus with no consistent token count under any separator is a refusal, not a
   guess.
2. **Per-position scores.** For each position, score each field role over the
   corpus:
   - `well` — fraction of tokens `util.well_token.canonical_well_label` accepts.
   - `timepoint` — fraction `util.timepoint.parse_hours` accepts.
   - `experiment` — cardinality 1 across the corpus is a strong signal.
   - `channel` — low cardinality, high repetition, non-numeric.
   - `fov` — numeric or `f`/`s`-prefixed, moderate cardinality.
   - `ignore` — the floor every position can fall back to.
3. **Assignment.** Each field is used at most once. Over five positions and six
   roles this is small enough for an exact search; no heuristic tie-breaking is
   needed and none is used.
4. **Verification.** The winning schema is run back through the existing preview
   path and the readout states what it matched: *"experiment:channel:well:fov:timepoint,
   separator `_` — 1,248 of 1,250 sampled filenames parse."* The two that did not
   are shown, because the exceptions are what tell the user whether to trust it.

`util.schema.validate_schema` decides validity, so what the inference proposes is
what the pipeline accepts — the same reason `widgets/schema_picker.py` already
takes its vocabulary from there.

### C.3 The refusals

- A corpus too small to be evidence → "too few files to detect a layout".
- No consistent token count → "these filenames do not share a layout".
- A best assignment that fails `validate_schema` (no `well`, or no `channel`) →
  report the assignment **and** what is missing, rather than proposing something
  the pipeline will reject.
- A detection that contradicts what the user has already typed → shown side by
  side. It never overwrites.

### C.4 Rows

| # | Row | State | Place | Evidence |
|---|---|---|---|---|
| C1 | `util/schema_infer.py` — separator choice, per-position scoring, exact assignment, the match count | `todo` | new module | tests over realistic corpora |
| C2 | Manifest entry for `util/schema_infer.py` | `todo` | `services/remote_manifest.py` | `tests/test_remote_manifest.py` |
| C3 | Fixtures carry the mess of real data (§7.1 #4): `.DS_Store`, AppleDouble sidecars, directory entries, names that parse to nothing, a mixed-case well token | `todo` | `tests/` | the inference survives them |
| C4 | Fixtures where the right and wrong answers differ (§7.8 rule 2): a corpus whose channel and FOV positions would swap under a weaker rule | `todo` | `tests/` | a weaker rule is named and shown to fail the test |
| C5 | `detectRequested` / `setDetection` on the picker, with no app knowledge added | `todo` | `widgets/schema_picker.py` | `test_architecture_contracts.py` stays green |
| C6 | Analyze form host: sample the input directory off the UI thread, call the inference, fill the picker | `todo` | `analyze/form.py` | driven through the real button (§7.6 #1) |
| C7 | Import Loose host: same, reusing the walk it already does | `todo` | `datasets/import_loose/` | driven through the real button |
| C8 | The four refusals, each with its own readout wording | `todo` | both hosts | one test each |
| C9 | Extend `tests/visual/render_views.py` to render both hosts with a detection shown, and with a refusal shown | `todo` | `tests/visual/render_views.py` | PNGs exist and are non-empty |
| C10 | **UX review against the rendered PNGs** — both hosts, both states | `todo` | subagent | findings recorded as rows |
| C11 | Act on the UX findings | `todo` | — | — |
| C12 | Mutation: make the assignment always return `DEFAULT_SCHEMA`; a named test must fail | `todo` | worktree of HEAD | the failing test named here |
| C13 | ARCHITECTURE.md §6 / §6b and README_WellViewer.md | `todo` | `_Docs/Markdowns/` | `tests/test_doc_version_sync.py` |
| C14 | Guard family + affected files + fast lane; version bump; PR | `todo` | — | what was run, reported honestly |
| C15 | **Scientific-Python review of the section diff** | `todo` | subagent | findings recorded as rows before the PR opens |

---

## Sequencing

**C, then A, then B.** Three pull requests, opened one at a time, each merged
before the next branch starts — they touch overlapping files (`ARCHITECTURE.md`,
`version.py`, and for A and B the Review-side feature tree), and CLAUDE.md's
warning about another session landing in the same files applies just as much to
two branches of one session's own work.

- **C first** because it is the smallest, carries no numeric risk, and its UX
  review teaches the review loop on the cheapest surface.
- **A second** because it is the most valuable and the most invasive; it wants
  the review loop already working.
- **B last** because it is the weakest scientifically and is the one most likely
  to be re-scoped or declined outright after A1's and B1's measurements land.

Each section's own first row is a measurement, and each may end in `declined`
with the measurement written down. That is a success condition for this plan,
not a failure.

## What would make this plan wrong

Written now, so that it can be checked later rather than rationalised:

- **A** is wrong if the per-field metrics cost a meaningful fraction of a FOV's
  processing time (A1), or if on real plates the robust z flags fields that a
  microscopist looking at them calls fine. The second needs real plates and
  cannot be settled here.
- **B** is wrong if the plateau criterion picks thresholds a user disagrees with
  more often than the constant `0.2` does. The curve survives that; the
  suggestion does not, and should be removed rather than defended.
- **C** is wrong if real acquisition filenames do not share a consistent token
  count as often as assumed — in which case the refusal path is the whole
  feature and the readout for it matters more than the inference.
